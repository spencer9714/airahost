"""
Supabase client helpers for the worker.

All DB access uses the service role key, which bypasses RLS.
This key must NEVER be exposed to the frontend.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Optional

from supabase import create_client, Client


def get_client() -> Client:
    """Create a Supabase client using the service role key."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def claim_job(client: Client, worker_token: uuid.UUID, stale_minutes: int) -> Optional[Dict[str, Any]]:
    """
    Atomically claim one queued (or stale-running) job.
    Returns the full row dict, or None if no work available.
    """
    result = client.rpc(
        "claim_pricing_report",
        {"p_worker_token": str(worker_token), "p_stale_minutes": stale_minutes},
    ).execute()

    rows = result.data
    if rows and len(rows) > 0:
        return rows[0]
    return None


def heartbeat(client: Client, report_id: str, worker_token: uuid.UUID) -> bool:
    """Update heartbeat timestamp. Returns True if the token still owns the job."""
    result = client.rpc(
        "heartbeat_pricing_report",
        {"p_report_id": report_id, "p_worker_token": str(worker_token)},
    ).execute()
    return result.data is True


def complete_job(
    client: Client,
    report_id: str,
    worker_token: uuid.UUID,
    *,
    summary: Dict[str, Any],
    calendar: list,
    core_version: str,
    debug: Optional[Dict[str, Any]] = None,
) -> None:
    """Mark a job as ready with results. Idempotent — overwrites existing results."""
    update: Dict[str, Any] = {
        "status": "ready",
        "core_version": core_version,
        "result_summary": summary,
        "result_calendar": calendar,
        "error_message": None,
    }
    if debug:
        update["result_core_debug"] = debug

    client.table("pricing_reports").update(update).eq("id", report_id).eq(
        "worker_claim_token", str(worker_token)
    ).execute()


def fail_job(
    client: Client,
    report_id: str,
    worker_token: uuid.UUID,
    *,
    error_message: str,
    debug: Optional[Dict[str, Any]] = None,
) -> None:
    """Mark a job as error with a user-friendly message."""
    update: Dict[str, Any] = {
        "status": "error",
        "error_message": error_message,
    }
    if debug:
        update["result_core_debug"] = debug

    client.table("pricing_reports").update(update).eq("id", report_id).eq(
        "worker_claim_token", str(worker_token)
    ).execute()
