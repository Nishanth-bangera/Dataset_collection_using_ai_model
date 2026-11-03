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
    # Rely on the students table's unique constraints instead of doing a
    # separate existence query before the insert. This removes one round trip;
    # the insert below still handles duplicate USNs/emails/mobiles safely.
    try:
        db.table("students").insert(
            {"name": name, "usn": usn, "email": email, "mobile": mobile}
        ).execute()
    except APIError as error:
        if _is_duplicate_error(error):
            raise HTTPException(
                status_code=409,
                detail="USN, email or mobile number is already registered",
            ) from error
        raise HTTPException(status_code=500, detail="Student registration could not be completed") from error

    details = json.dumps(
        {"name": name, "usn": usn, "email": email, "mobile": mobile},
        indent=2,
    ).encode("utf-8")
    details_path = f"{_storage_folder(usn)}/student_details.json"
    try:
        _storage_upload(details_path, details, "application/json")
    except Exception as error:
        try:
            db.table("students").delete().eq("usn", usn).execute()
        except Exception:
            pass
        raise HTTPException(
            status_code=503,
            detail="Student was not registered because private Storage could not save the details",
        ) from error

    return {"message": "Student registered successfully", "student": usn, "usn": usn}


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
        if _dataset_exists(usn):
            raise HTTPException(status_code=409, detail="A face dataset already exists for this student")

        bucket = db.storage.from_(STORAGE_BUCKET)
        folder = _storage_folder(usn)
        options = CreateSignedUploadUrlOptions(upsert="true")
        def create_upload(filename: str) -> dict:
            path = f"{folder}/images/{filename}"
            signed = bucket.create_signed_upload_url(path, options)
            return {"filename": filename, "path": signed["path"], "token": signed["token"]}

        with ThreadPoolExecutor(max_workers=len(EXPECTED_FILENAMES)) as executor:
            uploads = list(executor.map(create_upload, sorted(EXPECTED_FILENAMES)))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=503, detail="Could not prepare secure image uploads") from error

    return {"usn": usn, "bucket": STORAGE_BUCKET, "uploads": uploads}


@app.post("/upload-dataset/{usn}/complete")
def complete_dataset_upload(usn: str):
    """Confirm all expected objects exist and record that face processing is pending."""
    usn = _normalized_usn(usn)
    db = get_supabase()
    folder = _storage_folder(usn)

    try:
        if not _student_exists(usn):
            raise HTTPException(status_code=404, detail="Student is not registered")
        if _dataset_exists(usn):
            raise HTTPException(status_code=409, detail="A face dataset already exists for this student")

        stored_files = db.storage.from_(STORAGE_BUCKET).list(f"{folder}/images") or []
        stored_names = {item.get("name") for item in stored_files if item.get("name")}
        missing_names = EXPECTED_FILENAMES - stored_names
        if missing_names:
            raise HTTPException(
                status_code=400,
                detail=f"Storage is missing required images: {', '.join(sorted(missing_names))}",
            )

        db.table("face_datasets").insert(
            {
                "usn": usn,
                "image_count": 5,
                "zip_path": None,
                "processing_status": "pending",
            }
        ).execute()
    except HTTPException:
        raise
    except APIError as error:
        if _is_duplicate_error(error):
            raise HTTPException(status_code=409, detail="A face dataset already exists for this student") from error
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
