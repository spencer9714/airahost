from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]


def load_environment() -> None:
    """Load environment variables from project root, worker folder, and ml folder."""
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "worker" / ".env", override=False)
    load_dotenv(ROOT / "ml" / ".env", override=False)


def get_client() -> Client:
    load_environment()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise RuntimeError(
            "Missing Supabase credentials. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env or worker/.env."
        )

    return create_client(url, key)
