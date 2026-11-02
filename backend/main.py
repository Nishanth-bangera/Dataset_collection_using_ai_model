"""
AI Attendance System – FastAPI backend (Supabase edition).

Storage layout in the 'face-datasets' Supabase Storage bucket:
    <USN>/
        student_details.json
        images/
            front.jpg, left.jpg, right.jpg, up.jpg, down.jpg (5 pose images)
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
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
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


def _storage_delete(paths: list[str]):
    if not paths:
        return
    get_supabase().storage.from_(STORAGE_BUCKET).remove(paths)


def _student_exists_in_db(usn: str) -> bool:
    db = get_supabase()
    result = db.table("students").select("usn").eq("usn", usn).execute()
    return bool(result.data)


def _dataset_exists_in_storage(usn: str) -> bool:
    """Return True if the student already has an embeddings.npy in Storage."""
    db = get_supabase()
    files = db.storage.from_(STORAGE_BUCKET).list(usn)
    return any(f.get("name") == "embeddings.npy" for f in (files or []))


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

    # Keep name and USN format flexible. USN is still required by the request
    # model and database, and database uniqueness prevents duplicate USNs.
    if not re.fullmatch(r"\d{10}", mobile):
        raise HTTPException(status_code=400, detail="Mobile number must contain exactly 10 digits")

    # Check USN early for a clear response; database unique constraints handle races.
    try:
        if _student_exists_in_db(usn):
            raise HTTPException(status_code=409, detail="A student with this USN is already registered")
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=503, detail="Supabase is unavailable") from error

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
    details_path = f"{usn}/student_details.json"
    try:
        _storage_upload(details_path, json_bytes, "application/json")
    except Exception as error:
        try:
            _storage_delete([details_path])
        except Exception:
            pass
        try:
            db.table("students").delete().eq("usn", usn).execute()
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="Student details could not be saved to Storage") from error

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

    # No duplicate dataset; fail closed if Storage cannot be checked.
    try:
        if _dataset_exists_in_storage(usn):
            raise HTTPException(status_code=409, detail="A face dataset already exists for this student")
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=503, detail="Supabase Storage is unavailable") from error

    # Exactly one image for each required pose.
    expected_names = {f"{pose}.jpg" for pose in ("front", "left", "right", "up", "down")}
    received_names = [upload.filename for upload in files]
    if len(files) != 5 or len(set(received_names)) != 5 or set(received_names) != expected_names:
        raise HTTPException(status_code=400, detail="Exactly one image for each of the five poses is required")

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
            if embeddings_array.shape[0] != 5:
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
    uploaded_paths: list[str] = []
    try:
        # 1. Individual images
        for filename, content in validated_files:
            path = f"{usn}/images/{filename}"
            _storage_upload(path, content, "image/jpeg")
            uploaded_paths.append(path)

        # 2. embeddings.npy
        emb_bytes = io.BytesIO()
        np.save(emb_bytes, embeddings_array)
        embedding_path = f"{usn}/embeddings.npy"
        _storage_upload(embedding_path, emb_bytes.getvalue())
        uploaded_paths.append(embedding_path)

        # 3. Zip archive
        zip_path = f"{usn}/{usn}.zip"
        _storage_upload(zip_path, zip_buffer.getvalue(), "application/zip")
        uploaded_paths.append(zip_path)

        # 4. Log to face_datasets table
        get_supabase().table("face_datasets").insert({
            "usn":         usn,
            "image_count": 5,
            "zip_path":    f"{usn}/{usn}.zip",
        }).execute()

    except Exception as error:
        try:
            _storage_delete(uploaded_paths)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail="Dataset files could not be saved to cloud storage",
        ) from error

    return {
        "message":     "Dataset uploaded successfully",
        "usn":         usn,
        "image_count": 5,
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
