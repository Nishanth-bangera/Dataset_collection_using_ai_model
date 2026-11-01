"""
AI Attendance System – FastAPI backend (Supabase edition).

Storage layout in the 'face-datasets' Supabase Storage bucket:
    <USN>/
        student_details.json
        images/
            right_1.jpg … front_3.jpg   (15 pose images)
        embeddings.npy
        <USN>.zip
"""

import io
import json
import re
import zipfile
import tempfile
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from postgrest.exceptions import APIError

if __package__:
    from .database import get_supabase
    from .face_processing import (
        check_duplicate_face,
        generate_face_embedding,
        recognize_face_from_image,
    )
    from .embedding_manager import load_all_embeddings
    from .attendance import create_attendance_table, mark_attendance
else:
    from database import get_supabase
    from face_processing import (
        check_duplicate_face,
        generate_face_embedding,
        recognize_face_from_image,
    )
    from embedding_manager import load_all_embeddings
    from attendance import create_attendance_table, mark_attendance

# ── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Attendance System",
    description="Student Dataset Collection API (Supabase)",
    version="2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

create_attendance_table()  # no-op with Supabase; kept for compatibility

STORAGE_BUCKET = "face-datasets"

# Local staging directory for temp files during upload (not permanent storage)
_STAGING_DIR = Path(tempfile.gettempdir()) / "ai_attendance_staging"
_STAGING_DIR.mkdir(exist_ok=True)


# ── Pydantic models ───────────────────────────────────────────────────────────

class Student(BaseModel):
    name: str
    usn: str
    email: EmailStr
    mobile: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _storage_upload(path: str, data: bytes, content_type: str = "application/octet-stream"):
    """Upload bytes to Supabase Storage, overwriting if the file already exists."""
    db = get_supabase()
    try:
        db.storage.from_(STORAGE_BUCKET).upload(
            path,
            data,
            {"content-type": content_type, "upsert": "true"},
        )
    except Exception as error:
        raise RuntimeError(f"Storage upload failed for {path}: {error}") from error


def _student_exists_in_db(usn: str) -> bool:
    db = get_supabase()
    result = db.table("students").select("usn").eq("usn", usn).execute()
    return bool(result.data)


def _dataset_exists_in_storage(usn: str) -> bool:
    """Return True if the student already has an embeddings.npy in Storage."""
    db = get_supabase()
    try:
        files = db.storage.from_(STORAGE_BUCKET).list(usn)
        return any(f.get("name") == "embeddings.npy" for f in (files or []))
    except Exception:
        return False


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def home():
    return {"message": "AI Attendance System is running! (Supabase edition)"}


# ── Register student ──────────────────────────────────────────────────────────

@app.post("/register")
def register_student(student: Student):
    name   = student.name.strip()
    usn    = student.usn.strip().upper()
    email  = str(student.email).strip().lower()
    mobile = student.mobile.strip()

    # Validation
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if not re.fullmatch(r"[A-Z0-9]+", usn):
        raise HTTPException(status_code=400, detail="Invalid USN format")
    if not re.fullmatch(r"\d{10}", mobile):
        raise HTTPException(status_code=400, detail="Mobile number must contain exactly 10 digits")

    # Duplicate check
    if _student_exists_in_db(usn):
        raise HTTPException(status_code=409, detail="A student with this USN is already registered")

    details = {"name": name, "usn": usn, "email": email, "mobile": mobile}

    db = get_supabase()

    # Insert into Supabase students table
    try:
        db.table("students").insert({
            "name":   name,
            "usn":    usn,
            "email":  email,
            "mobile": mobile,
        }).execute()
    except APIError as error:
        error_msg = str(error)
        if "duplicate" in error_msg.lower() or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=409,
                detail="USN, email or mobile number is already registered",
            ) from error
        raise HTTPException(
            status_code=500,
            detail="Student registration could not be completed",
        ) from error

    # Save student_details.json to Storage
    json_bytes = json.dumps(details, indent=4).encode("utf-8")
    _storage_upload(f"{usn}/student_details.json", json_bytes, "application/json")

    return {
        "message": "Student registered successfully",
        "student": usn,
        "usn":     usn,
    }


# ── Upload face dataset ───────────────────────────────────────────────────────

@app.post("/upload-dataset/{usn}")
async def upload_dataset(usn: str, files: list[UploadFile] = File(...)):
    usn = usn.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+", usn):
        raise HTTPException(status_code=400, detail="Invalid USN")

    # Student must be registered
    if not _student_exists_in_db(usn):
        raise HTTPException(status_code=404, detail="Student is not registered")

    # No duplicate dataset
    if _dataset_exists_in_storage(usn):
        raise HTTPException(status_code=409, detail="A face dataset already exists for this student")

    # Exactly the 15 expected pose filenames
    expected_names = {
        f"{pose}_{index}.jpg"
        for pose in ("right", "left", "up", "down", "front")
        for index in range(1, 4)
    }
    received_names = [upload.filename for upload in files]
    if len(files) != 15 or len(set(received_names)) != 15 or set(received_names) != expected_names:
        raise HTTPException(status_code=400, detail="Exactly the 15 expected pose images are required")

    # Read + validate every image before doing any permanent work
    validated_files: list[tuple[str, bytes]] = []
    for upload in files:
        content = await upload.read()
        if not content or len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"{upload.filename} is empty or exceeds 5 MB")
        image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=400, detail=f"{upload.filename} is not a valid image")
        validated_files.append((upload.filename, content))

    # Generate face embeddings in a local temp directory
    try:
        with tempfile.TemporaryDirectory(prefix="dataset-upload-", dir=_STAGING_DIR) as tmp:
            staging = Path(tmp)

            # Write images to temp dir for face_processing helpers
            for filename, content in validated_files:
                (staging / filename).write_bytes(content)

            all_embeddings: list[np.ndarray] = []
            for image_path in sorted(staging.glob("*.jpg")):
                try:
                    all_embeddings.append(generate_face_embedding(image_path))
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error

            embeddings_array = np.asarray(all_embeddings, dtype=np.float32)
            if embeddings_array.shape[0] != 15:
                raise HTTPException(
                    status_code=400,
                    detail="Could not generate a face embedding for every image",
                )

            # Duplicate-face check against Supabase Storage embeddings
            duplicate_result = check_duplicate_face(
                embeddings_array,
                usn,
                dataset_dir=None,   # signal to use Supabase-backed loader
            )
            if duplicate_result["duplicate"]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Possible duplicate face detected. Manual review required.",
                        "matched_student": duplicate_result["matched_student"],
                        "similarity":      duplicate_result["similarity"],
                    },
                )

            # Build the zip in memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                # student details
                db = get_supabase()
                details_bytes = db.storage.from_(STORAGE_BUCKET).download(f"{usn}/student_details.json")
                archive.writestr("student_details.json", details_bytes)
                # images
                for filename, content in validated_files:
                    archive.writestr(f"images/{filename}", content)
                # embeddings
                emb_buffer = io.BytesIO()
                np.save(emb_buffer, embeddings_array)
                archive.writestr("embeddings.npy", emb_buffer.getvalue())

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Face verification or dataset saving failed; no partial dataset was kept",
        ) from error

    # ── All validation passed – upload everything to Supabase Storage ──
    try:
        # 1. Individual images
        for filename, content in validated_files:
            _storage_upload(f"{usn}/images/{filename}", content, "image/jpeg")

        # 2. embeddings.npy
        emb_bytes = io.BytesIO()
        np.save(emb_bytes, embeddings_array)
        _storage_upload(f"{usn}/embeddings.npy", emb_bytes.getvalue())

        # 3. Zip archive
        _storage_upload(f"{usn}/{usn}.zip", zip_buffer.getvalue(), "application/zip")

        # 4. Log to face_datasets table
        get_supabase().table("face_datasets").insert({
            "usn":         usn,
            "image_count": 15,
            "zip_path":    f"{usn}/{usn}.zip",
        }).execute()

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Dataset files could not be saved to cloud storage",
        ) from error

    return {
        "message":     "Dataset uploaded successfully",
        "usn":         usn,
        "image_count": 15,
        "zip_file":    f"{STORAGE_BUCKET}/{usn}/{usn}.zip",
    }


# ── Attendance ────────────────────────────────────────────────────────────────

@app.post("/attendance/{usn}")
def attendance_api(usn: str):
    usn = usn.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+", usn):
        raise HTTPException(status_code=400, detail="Invalid USN")

    if not _student_exists_in_db(usn):
        raise HTTPException(status_code=404, detail="Student is not registered")

    result = mark_attendance(usn)
    return {"usn": usn, "message": result}


# ── Face recognition ──────────────────────────────────────────────────────────

@app.post("/recognize-face")
async def recognize_face(file: UploadFile = File(...)):
    image_bytes = await file.read(5 * 1024 * 1024 + 1)
    if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image is empty or exceeds 5 MB")

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image")

    try:
        result = recognize_face_from_image(image)
    except Exception as error:
        raise HTTPException(
            status_code=503, detail="Face recognition model is unavailable"
        ) from error

    if not result["success"]:
        return result

    query_embedding = result["embedding"]
    try:
        database = load_all_embeddings()
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=500, detail="Registered face embeddings could not be loaded"
        ) from error

    if not database:
        return {"success": False, "message": "No registered face embeddings found"}

    best_match = None
    best_score = -1.0
    for usn, embeddings in database.items():
        if embeddings.shape[1] != query_embedding.size:
            raise HTTPException(
                status_code=500, detail=f"Stored embeddings for {usn} are incompatible"
            )
        score = float(np.max(embeddings @ query_embedding))
        if score > best_score:
            best_score = score
            best_match = usn

    return {
        "success":              True,
        "recognized_candidate": best_match,
        "similarity":           best_score,
        "message":              "Recognition candidate found. Verify the matching threshold before marking attendance.",
    }
