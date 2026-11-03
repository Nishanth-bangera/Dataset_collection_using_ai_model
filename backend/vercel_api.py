"""Vercel API for OTP-authenticated student registration and private face uploads."""

import json
import os
import re
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from postgrest.exceptions import APIError
from storage3.types import CreateSignedUploadUrlOptions

from .database import get_supabase

STORAGE_BUCKET = "face-datasets"
POSES = ("front", "left", "right", "up", "down")
EXPECTED_FILENAMES = {f"{pose}.jpg" for pose in POSES}
app = FastAPI(title="AI Attendance System", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"], allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "Authorization", "apikey", "x-client-info"])


class Student(BaseModel):
    name: str
    usn: str
    email: EmailStr
    mobile: str | None = None


class DatasetUploadRequest(BaseModel):
    filenames: list[str]


def _normalized_usn(raw: str) -> str:
    usn = raw.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{5,24}", usn):
        raise HTTPException(status_code=400, detail="Enter a valid USN")
    return usn


def _folder(usn: str) -> str:
    return quote(usn, safe="-_.~")


def _verified_email(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Verify your email to continue")
    token = authorization.split(" ", 1)[1].strip()
    try:
        user = get_supabase().auth.get_user(token).user
        email = (getattr(user, "email", None) or "").strip().lower()
        if not email:
            raise ValueError("Email missing")
        return email
    except Exception as error:
        raise HTTPException(status_code=401, detail="Email verification expired. Please sign in again") from error


def _student(usn: str) -> dict | None:
    result = get_supabase().table("students").select("name,usn,email,mobile").eq("usn", usn).limit(1).execute()
    return result.data[0] if result.data else None


def _stored_names(usn: str) -> set[str]:
    files = get_supabase().storage.from_(STORAGE_BUCKET).list(f"{_folder(usn)}/images") or []
    return {item.get("name") for item in files if item.get("name")}


def _ensure_student_details(student: dict) -> None:
    usn = student["usn"]
    root_files = get_supabase().storage.from_(STORAGE_BUCKET).list(_folder(usn)) or []
    if any(item.get("name") == "student_details.json" for item in root_files):
        return
    details = {key: student.get(key) for key in ("name", "usn", "email", "mobile") if student.get(key) is not None}
    get_supabase().storage.from_(STORAGE_BUCKET).upload(
        f"{_folder(usn)}/student_details.json", json.dumps(details, indent=2).encode(),
        {"content-type": "application/json", "upsert": "true"},
    )


def _status(usn: str) -> dict:
    student = _student(usn)
    if not student:
        raise HTTPException(status_code=404, detail="Student record was not found")
    names = _stored_names(usn)
    complete = EXPECTED_FILENAMES.issubset(names)
    face_status = "completed" if complete else ("in_progress" if names else "pending")
    upload_status = face_status
    get_supabase().table("students").update({
        "registration_status": "completed",
        "face_capture_status": face_status,
        "dataset_upload_status": upload_status,
    }).eq("usn", usn).execute()
    dataset = get_supabase().table("face_datasets").select("usn").eq("usn", usn).limit(1).execute()
    if complete and not dataset.data:
        get_supabase().table("face_datasets").insert({"usn": usn, "image_count": 5, "zip_path": None, "processing_status": "pending"}).execute()
    return {
        "student": student,
        "registration_status": "completed",
        "face_capture_status": face_status,
        "dataset_upload_status": upload_status,
        "uploaded_images": sorted(names & EXPECTED_FILENAMES),
        "missing_images": sorted(EXPECTED_FILENAMES - names),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/public-config")
def public_config():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY") or "").strip()
    if not url or not key:
        raise HTTPException(status_code=503, detail="Supabase public configuration is missing")
    return {"supabase_url": url, "supabase_publishable_key": key}


@app.post("/register")
def register_student(student: Student, authorization: str | None = Header(default=None)):
    email = _verified_email(authorization)
    usn = _normalized_usn(student.usn)
    name = student.name.strip()
    if not re.fullmatch(r"[\w][\w .'-]{1,99}", name, flags=re.UNICODE):
        raise HTTPException(status_code=400, detail="Enter a valid student name")
    if email != str(student.email).strip().lower():
        raise HTTPException(status_code=403, detail="Verified email does not match the registration email")
    db = get_supabase()
    try:
        existing = _student(usn)
        if existing:
            if existing["email"].lower() != email:
                raise HTTPException(status_code=409, detail="This USN is already associated with another account")
            _ensure_student_details(existing)
            return _status(usn)
        payload = {"name": name, "usn": usn, "email": email, "registration_status": "completed", "face_capture_status": "pending", "dataset_upload_status": "pending"}
        if student.mobile and re.fullmatch(r"\d{10}", student.mobile.strip()):
            payload["mobile"] = student.mobile.strip()
        db.table("students").insert(payload).execute()
        details = {**payload}
        _ensure_student_details(details)
        return _status(usn)
    except HTTPException:
        raise
    except APIError as error:
        # Concurrent first registration: accept the winner only if it is this verified student.
        existing = _student(usn)
        if existing and existing["email"].lower() == email:
            _ensure_student_details(existing)
            return _status(usn)
        raise HTTPException(status_code=409, detail="Registration could not be completed for these details") from error
    except Exception as error:
        # Keep the database row if metadata storage fails; registration remains resumable.
        raise HTTPException(status_code=503, detail="Registration is saved, but student metadata storage needs retry") from error


@app.post("/sign-in/{usn}")
def sign_in(usn: str, authorization: str | None = Header(default=None)):
    email = _verified_email(authorization)
    usn = _normalized_usn(usn)
    student = _student(usn)
    if not student or student["email"].lower() != email:
        # Keep USN/email lookup failures indistinguishable.
        raise HTTPException(status_code=401, detail="Sign in could not be verified. Check your details and try again")
    return _status(usn)


@app.post("/upload-dataset/{usn}")
def create_dataset_upload_urls(usn: str, request: DatasetUploadRequest, authorization: str | None = Header(default=None)):
    usn = _normalized_usn(usn)
    email = _verified_email(authorization)
    if len(request.filenames) != 5 or len(set(request.filenames)) != 5 or set(request.filenames) != EXPECTED_FILENAMES:
        raise HTTPException(status_code=400, detail="Exactly one image for each of the five poses is required")
    student = _student(usn)
    if not student or student["email"].lower() != email:
        raise HTTPException(status_code=401, detail="Sign in could not be verified. Please sign in again")
    missing = EXPECTED_FILENAMES - _stored_names(usn)
    if not missing:
        return {"usn": usn, "bucket": STORAGE_BUCKET, "uploads": [], "uploaded": sorted(EXPECTED_FILENAMES), "already_complete": True}
    bucket = get_supabase().storage.from_(STORAGE_BUCKET)
    options = CreateSignedUploadUrlOptions(upsert="true")
    uploads = []
    for filename in sorted(missing):
        signed = bucket.create_signed_upload_url(f"{_folder(usn)}/images/{filename}", options)
        uploads.append({"filename": filename, "path": signed["path"], "token": signed["token"]})
    return {"usn": usn, "bucket": STORAGE_BUCKET, "uploads": uploads, "uploaded": sorted(EXPECTED_FILENAMES - missing)}


@app.post("/upload-dataset/{usn}/complete")
def complete_dataset_upload(usn: str, authorization: str | None = Header(default=None)):
    usn = _normalized_usn(usn)
    email = _verified_email(authorization)
    student = _student(usn)
    if not student or student["email"].lower() != email:
        raise HTTPException(status_code=401, detail="Sign in could not be verified. Please sign in again")
    missing = EXPECTED_FILENAMES - _stored_names(usn)
    if missing:
        raise HTTPException(status_code=400, detail=f"Some images are still missing: {', '.join(sorted(missing))}")
    try:
        if not get_supabase().table("face_datasets").select("usn").eq("usn", usn).limit(1).execute().data:
            get_supabase().table("face_datasets").insert({"usn": usn, "image_count": 5, "zip_path": None, "processing_status": "pending"}).execute()
    except APIError as error:
        if getattr(error, "code", None) != "23505":
            raise HTTPException(status_code=503, detail="Images are saved, but completion could not be recorded") from error
    return {"message": "Images uploaded successfully", "usn": usn, "image_count": 5, "status": _status(usn)}


@app.post("/attendance/{usn}")
def attendance_not_available_yet(usn: str):
    raise HTTPException(status_code=501, detail="Attendance is disabled until live face verification is connected")


@app.post("/recognize-face")
def recognition_not_available_yet():
    raise HTTPException(status_code=501, detail="Face recognition runs in the separate processing stage")


app.frontend("/", directory="frontend")
