"""
Supabase-backed attendance helpers.
Replaces the old SQLite attendance table; marks one record per student per day.
"""

from datetime import datetime, timedelta, timezone
from postgrest.exceptions import APIError

if __package__:
    from .database import get_supabase
else:
    from database import get_supabase

# India Standard Time (UTC+5:30) – fixed offset, no DST.
INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


def create_attendance_table():
    """
    No-op: Supabase tables are created via schema.sql in the dashboard.
    Kept so existing call sites in main.py don't break.
    """
    pass


def mark_attendance(usn: str) -> str:
    """Insert one attendance row for today; silently ignores duplicates."""
    usn = usn.strip().upper()
    now = datetime.now(INDIA_TIMEZONE)
    attendance_date = now.strftime("%Y-%m-%d")
    attendance_time = now.strftime("%H:%M:%S")

    db = get_supabase()

    try:
        db.table("attendance").insert({
            "usn": usn,
            "date": attendance_date,
            "time": attendance_time,
            "status": "Present",
        }).execute()
    except APIError as error:
        # The database unique constraint makes this safe under concurrent requests.
        if getattr(error, "code", None) == "23505":
            return "Attendance already marked today"
        raise

    return "Attendance marked successfully"
