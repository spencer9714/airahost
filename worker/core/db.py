"""
Supabase client helpers for the worker.

All DB access uses the service role key, which bypasses RLS.
This key must NEVER be exposed to the frontend.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Optional

from supabase import create_client, Client

logger = logging.getLogger("worker.core.db")


def get_client() -> Client:
    """Create a Supabase client using the service role key."""
    try:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    except:
        url = "https://mtwummnephmqxyuxjkgu.supabase.co"
        key = "sb_publishable_2zuVxrcxIbJCIA5LZFRy2g_GwVH5dw9"
    return create_client(url, key)


def claim_job(client: Client, worker_token: uuid.UUID, stale_minutes: int, target_env: str, job_lane: str = "interactive") -> Optional[Dict[str, Any]]:
    """
    Atomically claim one queued (or stale-running) job matching target_env and job_lane.
    Returns the full row dict, or None if no work available.
    """
    result = client.rpc(
        "claim_pricing_report",
        {
            "p_worker_token": str(worker_token),
            "p_stale_minutes": stale_minutes,
            "p_job_lane": job_lane,
            "p_target_env": target_env,
        },
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
    input_address: Optional[str] = None,
    input_listing_url: Optional[str] = None,
    write_input_listing_url: bool = False,
    discount_policy: Optional[Dict[str, Any]] = None,
    cache_key: Optional[str] = None,
    source_market_captured_at: Optional[str] = None,
) -> None:
    """Mark a job as ready with results. Idempotent — overwrites existing results.

    Sets completed_at to now.  market_captured_at is set to now for fresh
    scrapes (live_analysis).  For forecast_snapshot reports, pass the source
    live_analysis report's market_captured_at as source_market_captured_at so
    freshness reflects when the underlying market data was actually captured.

    For nightly jobs that reload saved listing data at execution time, pass
    input_address, input_listing_url (with write_input_listing_url=True),
    discount_policy, and cache_key so the completed report row fully reflects
    actual execution inputs rather than the stale queued snapshot.

    write_input_listing_url=True: write input_listing_url unconditionally,
    including clearing to NULL when listing_url is None.  This separates
    "parameter not provided" (False, skip update) from "explicitly no URL" (True+None).
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
    if input_address is not None:
        update["input_address"] = input_address
    # write_input_listing_url=True writes the value unconditionally (including None → clears to NULL).
    # Default False preserves existing behaviour: skip update when value is None.
    if write_input_listing_url:
        update["input_listing_url"] = input_listing_url
    elif input_listing_url is not None:
        update["input_listing_url"] = input_listing_url
    if discount_policy is not None:
        update["discount_policy"] = discount_policy
    if cache_key is not None:
        update["cache_key"] = cache_key

    client.table("pricing_reports").update(update).eq("id", report_id).eq(
        "worker_claim_token", str(worker_token)
    ).execute()

    try:
        client.rpc(
            "ingest_market_price_observations",
            {"p_report_id": report_id},
        ).execute()
    except Exception as exc:
        logger.warning(
            f"[{report_id}] market observation ingestion failed after report completion: {exc}"
        )


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


def claim_price_update_job(
    client: Client, worker_token: uuid.UUID, stale_minutes: int
) -> Optional[Dict[str, Any]]:
    """
    Atomically claim one queued (or stale-running) price update job.
    Returns the full row dict, or None if no work is available.
    """
    result = client.rpc(
        "claim_price_update_job",
        {
            "p_worker_token": str(worker_token),
            "p_stale_minutes": stale_minutes,
        },
    ).execute()

    rows = result.data
    if rows and len(rows) > 0:
        return rows[0]
    return None


def complete_price_update_job(
    client: Client,
    job_id: str,
    worker_token: uuid.UUID,
    *,
    result_payload: Dict[str, Any],
) -> None:
    """Mark a claimed price update job as ready with result payload."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    client.table("price_update_jobs").update(
        {
            "status": "ready",
            "result": result_payload,
            "error_message": None,
            "completed_at": now,
            "updated_at": now,
            "worker_heartbeat_at": now,
        }
    ).eq("id", job_id).eq("worker_claim_token", str(worker_token)).execute()


def fail_price_update_job(
    client: Client,
    job_id: str,
    worker_token: uuid.UUID,
    *,
    error_message: str,
    result_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Mark a claimed price update job as error."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    update: Dict[str, Any] = {
        "status": "error",
        "error_message": error_message,
        "completed_at": now,
        "updated_at": now,
        "worker_heartbeat_at": now,
    }
    if result_payload is not None:
        update["result"] = result_payload

    client.table("price_update_jobs").update(update).eq("id", job_id).eq(
        "worker_claim_token", str(worker_token)
    ).execute()
