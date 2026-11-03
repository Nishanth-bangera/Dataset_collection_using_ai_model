"""Lightweight Vercel API for registration and direct-to-Supabase uploads.

Face embeddings and duplicate-face processing stay in the separate processing
module so the Vercel function does not need OpenCV, NumPy, InsightFace, or ONNX.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from postgrest.exceptions import APIError
from storage3.types import CreateSignedUploadUrlOptions

from .database import get_supabase

STORAGE_BUCKET = "face-datasets"
POSES = ("front", "left", "right", "up", "down")
EXPECTED_FILENAMES = {f"{pose}.jpg" for pose in POSES}

app = FastAPI(
    title="AI Attendance System",
    description="Student registration and face dataset collection API",
    version="3.0",
)

# Production frontend and API share one Vercel origin. These entries only
# support local Live Server development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "apikey", "x-client-info"],
)


class Student(BaseModel):
    name: str
    usn: str
    email: EmailStr
    mobile: str


class DatasetUploadRequest(BaseModel):
    filenames: list[str]


def _normalized_usn(raw_usn: str) -> str:
    usn = raw_usn.strip().upper()
    if not usn or len(usn) > 64 or "/" in usn or "\\" in usn:
        raise HTTPException(
            status_code=400,
            detail="USN is required and cannot contain path separators",
        )
    return usn


def _storage_folder(usn: str) -> str:
    # Keep ordinary USNs readable while safely encoding punctuation and spaces.
    return quote(usn, safe="-_.~")


def _is_duplicate_error(error: Exception) -> bool:
    return (
        getattr(error, "code", None) == "23505"
        or "duplicate" in str(error).lower()
        or "unique" in str(error).lower()
    )


def _student_exists(usn: str) -> bool:
    result = (
        get_supabase()
        .table("students")
        .select("usn")
        .eq("usn", usn)
        .limit(1)
        .execute()
    )
    return bool(result.data)


def _get_student(usn: str) -> dict | None:
    result = (
        get_supabase().table("students")
        .select("name,usn,email,mobile")
        .eq("usn", usn).limit(1).execute()
    )
    return result.data[0] if result.data else None


def _stored_images(usn: str) -> set[str]:
    files = get_supabase().storage.from_(STORAGE_BUCKET).list(
        f"{_storage_folder(usn)}/images"
    ) or []
    return {item.get("name") for item in files if item.get("name")}


def _student_result(student: dict, *, existing: bool) -> dict:
    stored = _stored_images(student["usn"])
    missing = EXPECTED_FILENAMES - stored
    return {
        "student": student["usn"],
        "name": student["name"],
        "email": student["email"],
        "mobile": student.get("mobile"),
        "existing_student": existing,
        "dataset_completed": not missing,
        "missing_images": sorted(missing),
    }


def _ensure_student_details(student: dict) -> None:
    """Create metadata only when absent; never overwrite an existing student file."""
    folder = _storage_folder(student["usn"])
    files = get_supabase().storage.from_(STORAGE_BUCKET).list(folder) or []
    if any(item.get("name") == "student_details.json" for item in files):
        return
    details = {key: student.get(key) for key in ("name", "usn", "email", "mobile")}
    _storage_upload(
        f"{folder}/student_details.json",
        json.dumps(details, indent=2).encode("utf-8"),
        "application/json",
    )


def _dataset_exists(usn: str) -> bool:
    result = (
        get_supabase()
        .table("face_datasets")
        .select("usn")
        .eq("usn", usn)
        .limit(1)
        .execute()
    )
    return bool(result.data)


def _storage_upload(path: str, content: bytes, content_type: str) -> None:
    get_supabase().storage.from_(STORAGE_BUCKET).upload(
        path,
        content,
        {"content-type": content_type, "upsert": "true"},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/public-config")
def public_config():
    """Return only browser-safe Supabase settings; never return the service key."""
    url = os.getenv("SUPABASE_URL", "").strip()
    publishable_key = (
        os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_PUBLISHABLE_KEY")
        or ""
    ).strip()
    if not url or not publishable_key:
        raise HTTPException(status_code=503, detail="Supabase public configuration is missing")
    return {"supabase_url": url, "supabase_publishable_key": publishable_key}


@app.post("/register")
def register_student(student: Student):
    name = student.name.strip()
    usn = _normalized_usn(student.usn)
    email = str(student.email).strip().lower()
    mobile = student.mobile.strip()

    if not re.fullmatch(r"\d{10}", mobile):
        raise HTTPException(status_code=400, detail="Mobile number must contain exactly 10 digits")

    db = get_supabase()
    existing = _get_student(usn)
    if existing:
        same_person = (
            existing["name"].strip().casefold() == name.casefold()
            and existing["email"].strip().casefold() == email.casefold()
            and existing["mobile"].strip() == mobile
        )
        if not same_person:
            raise HTTPException(status_code=409, detail="These details do not match the existing USN")
        try:
            _ensure_student_details(existing)
            return _student_result(existing, existing=True)
        except Exception as error:
            raise HTTPException(status_code=503, detail="Your registration exists, but its details file could not be checked") from error

    try:
        db.table("students").insert(
            {"name": name, "usn": usn, "email": email, "mobile": mobile}
        ).execute()
    except APIError as error:
        if _is_duplicate_error(error):
            # A simultaneous registration may have won the unique constraint.
            existing = _get_student(usn)
            if existing and existing["name"].strip().casefold() == name.casefold() \
                    and existing["email"].strip().casefold() == email.casefold() \
                    and existing["mobile"].strip() == mobile:
                _ensure_student_details(existing)
                return _student_result(existing, existing=True)
            raise HTTPException(status_code=409, detail="USN, email or mobile number is already registered") from error
        raise HTTPException(status_code=500, detail="Student registration could not be completed") from error

    try:
        _ensure_student_details({"name": name, "usn": usn, "email": email, "mobile": mobile})
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Registration is saved. Please submit the same details again to retry its details file",
        ) from error

    return _student_result({"name": name, "usn": usn, "email": email, "mobile": mobile}, existing=False)


@app.post("/upload-dataset/{usn}")
def create_dataset_upload_urls(usn: str, request: DatasetUploadRequest):
    """Issue one-time upload tokens; image bytes go straight from browser to Storage."""
    usn = _normalized_usn(usn)
    filenames = request.filenames
    if len(filenames) != 5 or len(set(filenames)) != 5 or set(filenames) != EXPECTED_FILENAMES:
        raise HTTPException(status_code=400, detail="Exactly one image for each of the five poses is required")

    db = get_supabase()
    try:
        if not _student_exists(usn):
            raise HTTPException(status_code=404, detail="Student is not registered")
        bucket = db.storage.from_(STORAGE_BUCKET)
        folder = _storage_folder(usn)
        options = CreateSignedUploadUrlOptions(upsert="true")
        missing = EXPECTED_FILENAMES - _stored_images(usn)
        if not missing:
            return {"usn": usn, "bucket": STORAGE_BUCKET, "uploads": [], "already_complete": True}
        def create_upload(filename: str) -> dict:
            path = f"{folder}/images/{filename}"
            signed = bucket.create_signed_upload_url(path, options)
            return {"filename": filename, "path": signed["path"], "token": signed["token"]}

        with ThreadPoolExecutor(max_workers=len(missing)) as executor:
            uploads = list(executor.map(create_upload, sorted(missing)))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=503, detail="Could not prepare secure image uploads") from error

    return {
        "usn": usn,
        "bucket": STORAGE_BUCKET,
        "uploads": uploads,
        "existing_images": sorted(EXPECTED_FILENAMES - missing),
    }


@app.post("/upload-dataset/{usn}/complete")
def complete_dataset_upload(usn: str):
    """Confirm all expected objects exist and record that face processing is pending."""
    usn = _normalized_usn(usn)
    db = get_supabase()
    try:
        if not _student_exists(usn):
            raise HTTPException(status_code=404, detail="Student is not registered")
        stored_names = _stored_images(usn)
        missing_names = EXPECTED_FILENAMES - stored_names
        if missing_names:
            raise HTTPException(
                status_code=400,
                detail=f"Storage is missing required images: {', '.join(sorted(missing_names))}",
            )

        if not _dataset_exists(usn):
            db.table("face_datasets").insert(
                {"usn": usn, "image_count": 5, "zip_path": None, "processing_status": "pending"}
            ).execute()
    except HTTPException:
        raise
    except APIError as error:
        if _is_duplicate_error(error) and _dataset_exists(usn):
            pass
        else:
            raise HTTPException(status_code=503, detail="Dataset upload could not be recorded") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="Could not verify uploaded images in Storage") from error

    return {
        "message": "Images uploaded successfully; face processing is pending",
        "usn": usn,
        "image_count": 5,
        "processing_status": "pending",
    }


@app.post("/attendance/{usn}")
def attendance_not_available_yet(usn: str):
    raise HTTPException(
        status_code=501,
        detail="Attendance is disabled until live face verification is connected",
    )


@app.post("/recognize-face")
def recognition_not_available_yet():
    raise HTTPException(
        status_code=501,
        detail="Face recognition runs in the separate processing stage, not in the lightweight Vercel API",
    )


# Vercel serves these files on the same origin, so browser API calls use relative URLs.
app.frontend("/", directory="frontend")
