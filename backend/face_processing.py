import cv2
import numpy as np
from pathlib import Path
from threading import Lock
from insightface.app import FaceAnalysis


# Keep paths independent of the shell's current working directory.
DATASET_DIR = Path(__file__).resolve().parent.parent / "student_datasets"
DUPLICATE_SIMILARITY_THRESHOLD = 0.50

_face_app = None
_face_app_lock = Lock()


def _get_face_app():
    global _face_app
    if _face_app is None:
        with _face_app_lock:
            if _face_app is None:
                app = FaceAnalysis(
                    name="buffalo_l",
                    providers=["CPUExecutionProvider"]
                )
                app.prepare(ctx_id=-1, det_size=(640, 640))
                _face_app = app
    return _face_app


def generate_face_embedding(image_path):
    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError("Unable to read image")

    faces = _get_face_app().get(image)
    if len(faces) != 1:
        raise ValueError("Exactly one face must be visible in the image")

    return faces[0].normed_embedding


def compare_faces(embedding1, embedding2):
    return float(np.dot(embedding1, embedding2))


def recognize_face_from_image(image):
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        return {
            "success": False,
            "message": "Invalid image"
        }

    faces = _get_face_app().get(image)

    if len(faces) == 0:
        return {
            "success": False,
            "message": "No face detected"
        }

    if len(faces) > 1:
        return {
            "success": False,
            "message": "Multiple faces detected"
        }

    return {
        "success": True,
        "embedding": np.asarray(faces[0].normed_embedding, dtype=np.float32)
    }


def check_duplicate_face(new_embeddings, current_usn, dataset_dir=DATASET_DIR):
    """Return a possible match against existing students' saved embeddings."""
    new_embeddings = np.asarray(new_embeddings)
    if new_embeddings.ndim != 2 or new_embeddings.shape[0] == 0:
        raise ValueError("At least one valid face embedding is required")

    if dataset_dir is None:
        if __package__:
            from .embedding_manager import load_all_embeddings
        else:
            from embedding_manager import load_all_embeddings

        for usn, old_embeddings in load_all_embeddings().items():
            if usn.upper() == current_usn.upper():
                continue
            if old_embeddings.shape[1] != new_embeddings.shape[1]:
                raise ValueError(f"Saved embeddings for {usn} have an incompatible shape")
            for new_embedding in new_embeddings:
                similarities = old_embeddings @ new_embedding
                similarity = float(np.max(similarities))
                if similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
                    return {
                        "duplicate": True,
                        "matched_student": usn,
                        "similarity": similarity
                    }
        return {
            "duplicate": False,
            "matched_student": None,
            "similarity": None
        }

    for student_folder in dataset_dir.iterdir():
        if not student_folder.is_dir() or student_folder.name.upper() == current_usn.upper():
            continue

        embedding_file = student_folder / "embeddings.npy"
        if not embedding_file.is_file():
            continue

        try:
            old_embeddings = np.load(embedding_file, allow_pickle=False)
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read saved embeddings for {student_folder.name}") from error

        if old_embeddings.ndim != 2 or old_embeddings.shape[1] != new_embeddings.shape[1]:
            raise ValueError(f"Saved embeddings for {student_folder.name} have an incompatible shape")

        for new_embedding in new_embeddings:
            for old_embedding in old_embeddings:
                similarity = compare_faces(new_embedding, old_embedding)
                if similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
                    return {
                        "duplicate": True,
                        "matched_student": student_folder.name,
                        "similarity": similarity
                    }

    return {
        "duplicate": False,
        "matched_student": None,
        "similarity": None
    }
