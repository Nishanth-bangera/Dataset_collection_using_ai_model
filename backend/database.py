"""
Supabase-backed database helpers.
Replaces the old SQLite create_table / get_connection interface so the rest of
the codebase keeps working without changes to call sites that only used those
two names.
"""

import os
from threading import Lock
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_supabase: Client | None = None
_client_lock = Lock()


def get_supabase() -> Client:
    """Create the Supabase client on first request, not during Vercel build."""
    global _supabase
    if _supabase is None:
        with _client_lock:
            if _supabase is None:
                url = os.getenv("SUPABASE_URL", "").strip()
                key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
                if not url or not key:
                    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be configured")
                if key.startswith("sb_publishable_"):
                    raise RuntimeError(
                        "SUPABASE_SERVICE_KEY contains a publishable key. "
                        "Set it to the Supabase server-side secret/service-role key."
                    )
                _supabase = create_client(url, key)
    return _supabase


# Legacy shims kept for older call sites.

def get_connection():
    """
    Deprecated: was the SQLite connection factory.
    Returns the Supabase client so call sites that only call
    `conn.execute(...)` need individual updates, but at least import fine.
    """
    return get_supabase()


def create_table():
    """
    Deprecated: tables are now managed in Supabase (see backend/schema.sql).
    This is a no-op kept for import compatibility.
    """
    pass
