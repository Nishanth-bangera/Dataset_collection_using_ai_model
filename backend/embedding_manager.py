"""
Supabase-backed embedding manager.
Downloads embeddings.npy from Supabase Storage and caches them in memory,
replacing the old local-filesystem cache.
"""

import io
from threading import RLock

import numpy as np

if __package__:
    from .database import get_supabase
else:
    from database import get_supabase

STORAGE_BUCKET = "face-datasets"

_cache_lock = RLock()
_cached_embeddings: dict | None = None
_cached_usns: frozenset | None = None


def _list_student_usns() -> frozenset:
    """Return USNs with completed face-dataset uploads."""
    db = get_supabase()
    response = db.table("face_datasets").select("usn").execute()
    return frozenset(row["usn"] for row in (response.data or []) if row.get("usn"))


def _download_embedding(usn: str) -> np.ndarray:
    """Download and return the embedding matrix for one student."""
    db = get_supabase()
    path = f"{usn}/embeddings.npy"
    data = db.storage.from_(STORAGE_BUCKET).download(path)
    embeddings = np.load(io.BytesIO(data), allow_pickle=False)
    return np.asarray(embeddings, dtype=np.float32)


def load_all_embeddings() -> dict:
    """Load and cache normalised embeddings keyed by student USN."""
    global _cached_embeddings, _cached_usns

    with _cache_lock:
        current_usns = _list_student_usns()
        if _cached_embeddings is not None and current_usns == _cached_usns:
            return dict(_cached_embeddings)

        student_embeddings = {}
        for usn in current_usns:
            try:
                embeddings = _download_embedding(usn)
            except Exception as error:
                raise ValueError(f"Cannot load embeddings for student {usn}") from error

            if embeddings.ndim != 2 or embeddings.shape[0] == 0 or embeddings.shape[1] == 0:
                raise ValueError(f"Embeddings for student {usn} must be a non-empty 2D array")
            if not np.isfinite(embeddings).all():
                raise ValueError(f"Embeddings for student {usn} contain invalid values")

            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            if np.any(norms == 0):
                raise ValueError(f"Embeddings for student {usn} contain a zero vector")

            embeddings = embeddings / norms
            embeddings.setflags(write=False)
            student_embeddings[usn] = embeddings

        _cached_embeddings = student_embeddings
        _cached_usns = current_usns
        return dict(_cached_embeddings)


def recognize_student(query_embedding, threshold: float = 0.50) -> dict:
    """Return the best matching student; threshold is experimental, not validated."""
    if not np.isfinite(threshold) or not -1.0 <= threshold <= 1.0:
        raise ValueError("Similarity threshold must be between -1 and 1")

    query = np.asarray(query_embedding, dtype=np.float32)
    if query.ndim != 1 or query.size == 0 or not np.isfinite(query).all():
        raise ValueError("Query embedding must be a finite, non-empty vector")

    query_norm = np.linalg.norm(query)
    if query_norm == 0:
        raise ValueError("Query embedding cannot be a zero vector")
    query = query / query_norm

    database = load_all_embeddings()
    best_match = None
    best_score = -1.0

    for usn, embeddings in database.items():
        if embeddings.shape[1] != query.size:
            raise ValueError(f"Embedding size for student {usn} does not match the query")
        score = float(np.max(embeddings @ query))
        if score > best_score:
            best_score = score
            best_match = usn

    if best_match is not None and best_score >= threshold:
        return {"recognized": True, "usn": best_match, "similarity": best_score}

    return {"recognized": False, "usn": None, "similarity": best_score}
