"""
Supabase-backed database helpers.
Replaces the old SQLite create_table / get_connection interface so the rest of
the codebase keeps working without changes to call sites that only used those
two names.
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_SUPABASE_URL: str = os.environ["SUPABASE_URL"]
_SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

# Single shared client – thread-safe for reads; use with care on writes.
supabase: Client = create_client(_SUPABASE_URL, _SUPABASE_KEY)


def get_supabase() -> Client:
    """Return the shared Supabase client."""
    return supabase


# ── Legacy shims kept so existing imports in main.py / attendance.py don't break ──

def get_connection():
    """
    Deprecated: was the SQLite connection factory.
    Returns the Supabase client so call sites that only call
    `conn.execute(...)` need individual updates, but at least import fine.
    """
    return supabase


def create_table():
    """
    Deprecated: tables are now managed in Supabase (see backend/schema.sql).
    This is a no-op kept for import compatibility.
    """
    pass
