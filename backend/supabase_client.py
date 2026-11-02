import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

if SUPABASE_KEY.startswith("sb_publishable_"):
    raise RuntimeError(
        "SUPABASE_SERVICE_KEY contains a publishable key. "
        "Set it to the Supabase server-side secret/service-role key in .env."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

