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


def claim_job(client: Client, worker_token: uuid.UUID, stale_minutes: int, target_env: str) -> Optional[Dict[str, Any]]:
    """
    Atomically claim one queued (or stale-running) job matching target_env.
    Returns the full row dict, or None if no work available.
    """
    result = client.rpc(
        "claim_pricing_report",
        {"p_worker_token": str(worker_token), "p_stale_minutes": stale_minutes, "p_target_env": target_env},
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
    input_attributes: Optional[Dict[str, Any]] = None,
    source_market_captured_at: Optional[str] = None,
) -> None:
    """Mark a job as ready with results. Idempotent — overwrites existing results.

    Sets completed_at to now.  market_captured_at is set to now for fresh
    scrapes (live_analysis).  For forecast_snapshot reports, pass the source
    live_analysis report's market_captured_at as source_market_captured_at so
    freshness reflects when the underlying market data was actually captured.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    update: Dict[str, Any] = {
        "status": "ready",
        "core_version": core_version,
        "result_summary": summary,
        "result_calendar": calendar,
        "error_message": None,
        # Explicit freshness timestamps (migration 010)
        "completed_at": now,
        # For forecast_snapshot: inherit market_captured_at from source live_analysis.
        # For live_analysis (fresh scrape): capture time == completion time.
        "market_captured_at": source_market_captured_at if source_market_captured_at is not None else now,
    }
    if debug:
        update["result_core_debug"] = debug
    if input_attributes is not None:
        update["input_attributes"] = input_attributes

    client.table("pricing_reports").update(update).eq("id", report_id).eq(
        "worker_claim_token", str(worker_token)
    ).execute()


def sync_linked_listing_attributes(
    client: Client,
    report_id: str,
    input_attributes: Dict[str, Any],
) -> None:
    """Update linked saved listing attributes from finalized report attributes."""
    link_result = (
        client.table("listing_reports")
        .select("saved_listing_id")
        .eq("pricing_report_id", report_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = link_result.data or []
    if not rows:
        return

    listing_id = rows[0].get("saved_listing_id")
    if not listing_id:
        return

    client.table("saved_listings").update(
        {"input_attributes": input_attributes}
    ).eq("id", listing_id).execute()


def update_progress(
    client: Client,
    report_id: str,
    worker_token: uuid.UUID,
    *,
    pct: int,
    stage: str,
    message: str,
    est_seconds_remaining: Optional[int] = None,
) -> bool:
    """Update heartbeat + progress metadata atomically.

    Returns True if the token still owns the job (row was updated).
    Safe to call from both the heartbeat thread and the main thread.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    meta: Dict[str, Any] = {
        "pct": max(0, min(100, pct)),
        "stage": stage,
        "message": message,
        "updated_at": now,
    }
    if est_seconds_remaining is not None:
        meta["est_seconds_remaining"] = max(0, est_seconds_remaining)
    result = (
        client.table("pricing_reports")
        .update({
            "worker_heartbeat_at": now,
            "progress_meta": meta,
        })
        .eq("id", report_id)
        .eq("worker_claim_token", str(worker_token))
        .execute()
    )
    return bool(result.data)


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
