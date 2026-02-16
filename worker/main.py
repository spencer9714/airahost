"""
AriaHost Worker — Long-running process that polls Supabase for queued
pricing reports and processes them.

Usage:
    python -m worker.main

Environment variables: see .env.example
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

# Load .env from worker directory or repo root
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv()

from worker.core import db as db_helpers
from worker.core.cache import compute_cache_key, get_cached, set_cached
from worker.core.discounts import (
    apply_discount,
    average_refundable_price_for_stay,
    build_stay_length_averages,
)
from worker.core.mock_core import CORE_VERSION as MOCK_VERSION
from worker.core.mock_core import generate_mock_report

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "5"))
STALE_MINUTES = int(os.getenv("WORKER_STALE_MINUTES", "15"))
MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))
HEARTBEAT_SECONDS = int(os.getenv("WORKER_HEARTBEAT_SECONDS", "10"))
MAX_RUNTIME_SECONDS = int(os.getenv("WORKER_MAX_RUNTIME_SECONDS", "180"))
WORKER_VERSION = os.getenv("WORKER_VERSION", "worker-0.1.0")
CDP_URL = os.getenv("CDP_URL", "http://127.0.0.1:9222")

MAX_SCROLL_ROUNDS = int(os.getenv("MAX_SCROLL_ROUNDS", "12"))
MAX_CARDS = int(os.getenv("MAX_CARDS", "80"))
RATE_LIMIT_SECONDS = float(os.getenv("SCRAPE_RATE_LIMIT_SECONDS", "1.0"))

# ---------------------------------------------------------------------------
# Logging — console + rotating file (logs/worker.log)
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Ensure logs/ directory exists next to this file (i.e. worker/logs/)
_log_dir = Path(__file__).resolve().parent / "logs"
_log_dir.mkdir(exist_ok=True)

# Root logger setup — captures all worker.* loggers
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)

# Console handler
_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
_root_logger.addHandler(_console)

# Rotating file handler: 5 MB per file, keep 5 backups
_file_handler = logging.handlers.RotatingFileHandler(
    filename=str(_log_dir / "worker.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
_root_logger.addHandler(_file_handler)

logger = logging.getLogger("worker")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_event = threading.Event()


def _signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, shutting down gracefully...")
    _shutdown_event.set()


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ---------------------------------------------------------------------------
# Heartbeat thread
# ---------------------------------------------------------------------------


def _run_heartbeat(
    report_id: str,
    worker_token: uuid.UUID,
    stop_event: threading.Event,
):
    """Background thread that sends heartbeats while a job is running."""
    client = db_helpers.get_client()
    while not stop_event.is_set():
        stop_event.wait(HEARTBEAT_SECONDS)
        if stop_event.is_set():
            break
        try:
            ok = db_helpers.heartbeat(client, report_id, worker_token)
            if not ok:
                logger.warning(f"Heartbeat rejected for {report_id} — claim lost?")
                break
        except Exception as exc:
            logger.error(f"Heartbeat error for {report_id}: {exc}")


# ---------------------------------------------------------------------------
# Job processing
# ---------------------------------------------------------------------------


def _get_listing_url(job: Dict[str, Any]) -> Optional[str]:
    """Extract listing URL from the job row (multiple possible locations)."""
    url = job.get("input_listing_url")
    if url:
        return url.strip()
    attrs = job.get("input_attributes") or {}
    url = attrs.get("listingUrl") or attrs.get("listing_url")
    if url:
        return str(url).strip()
    return None


def _build_scrape_calendar(
    recommended: float,
    start_date: str,
    end_date: str,
    discount_policy: Dict[str, Any],
    comps_debug: Dict[str, Any],
) -> tuple:
    """
    Build summary + calendar from scrape results.
    Uses the recommended nightly price as baseline with some variation.
    """
    from datetime import datetime as dt, timedelta as td, timezone as tz

    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    start = dt.strptime(start_date, "%Y-%m-%d").replace(tzinfo=tz.utc)
    end = dt.strptime(end_date, "%Y-%m-%d").replace(tzinfo=tz.utc)
    total_days = max(1, (end - start).days)

    # Build calendar days with price variation around the recommended price
    calendar = []
    for i in range(total_days):
        d = start + td(days=i)
        dow = d.weekday()
        is_weekend = dow >= 4  # Fri, Sat

        # Simple variation: ±5% for daily, +15% weekend boost
        base_price = round(recommended)
        if is_weekend:
            base_price = round(recommended * 1.15)

        disc = apply_discount(base_price, total_days, discount_policy)

        calendar.append({
            "date": d.strftime("%Y-%m-%d"),
            "dayOfWeek": DAY_NAMES[dow],
            "isWeekend": is_weekend,
            "basePrice": base_price,
            "refundablePrice": disc["refundablePrice"],
            "nonRefundablePrice": disc["nonRefundablePrice"],
        })

    # Summary
    base_prices = [d["basePrice"] for d in calendar]
    sorted_p = sorted(base_prices)
    median = sorted_p[len(sorted_p) // 2]
    min_p = sorted_p[0]
    max_p = sorted_p[-1]

    weekday_p = [d["basePrice"] for d in calendar if not d["isWeekend"]]
    weekend_p = [d["basePrice"] for d in calendar if d["isWeekend"]]
    weekday_avg = round(sum(weekday_p) / len(weekday_p)) if weekday_p else median
    weekend_avg = round(sum(weekend_p) / len(weekend_p)) if weekend_p else median

    occupancy = 70  # reasonable default for scraped results
    selected_range_avg = average_refundable_price_for_stay(
        [d["basePrice"] for d in calendar], total_days, discount_policy
    )
    est_monthly = round(selected_range_avg * 30 * (occupancy / 100))

    # Insight from comps
    wm = comps_debug.get("weighted_median")
    rec = comps_debug.get("recommended_nightly")
    if wm and rec:
        diff = round(wm - rec)
        if diff > 5:
            headline = f"Comparable listings average ${round(wm)}/night. Our recommendation accounts for a new-listing discount."
        elif diff < -5:
            headline = f"At ${round(rec)}/night you're competitively positioned against the ${round(wm)} market median."
        else:
            headline = f"Your recommended price of ${round(rec)}/night is well-aligned with the local market."
    else:
        headline = f"Based on nearby comparable listings, we recommend ${round(recommended)}/night."

    weekly_avg = average_refundable_price_for_stay(
        [d["basePrice"] for d in calendar], min(7, total_days), discount_policy
    )
    monthly_avg = average_refundable_price_for_stay(
        [d["basePrice"] for d in calendar], min(28, total_days), discount_policy
    )
    stay_length_averages = build_stay_length_averages(
        [d["basePrice"] for d in calendar], total_days, discount_policy
    )

    summary = {
        "insightHeadline": headline,
        "nightlyMin": min_p,
        "nightlyMedian": median,
        "nightlyMax": max_p,
        "occupancyPct": occupancy,
        "weekdayAvg": weekday_avg,
        "weekendAvg": weekend_avg,
        "estimatedMonthlyRevenue": est_monthly,
        "weeklyStayAvgNightly": weekly_avg,
        "monthlyStayAvgNightly": monthly_avg,
        "selectedRangeNights": total_days,
        "selectedRangeAvgNightly": selected_range_avg,
        "stayLengthAverages": stay_length_averages,
    }

    return summary, calendar


def process_job(job: Dict[str, Any], worker_token: uuid.UUID) -> None:
    """
    Process a single pricing report job.

    Mode 1: If listing URL available → scrape with Playwright via CDP
    Mode 2: If no listing URL → deterministic mock pricing
    """
    report_id = job["id"]
    client = db_helpers.get_client()
    start_time = time.time()

    # Start heartbeat thread
    hb_stop = threading.Event()
    hb_thread = threading.Thread(
        target=_run_heartbeat,
        args=(report_id, worker_token, hb_stop),
        daemon=True,
    )
    hb_thread.start()

    try:
        address = job.get("input_address", "")
        attributes = job.get("input_attributes") or {}
        start_date = str(job.get("input_date_start", ""))
        end_date = str(job.get("input_date_end", ""))
        discount_policy = job.get("discount_policy") or {}
        listing_url = _get_listing_url(job)
        input_mode = attributes.get("inputMode", "criteria")

        # Check cache first
        cache_key = job.get("cache_key") or compute_cache_key(
            address, attributes, start_date, end_date, discount_policy, listing_url, input_mode
        )
        cached = get_cached(client, cache_key)
        if cached:
            summary, calendar = cached
            logger.info(f"[{report_id}] Cache hit for key={cache_key[:12]}...")
            db_helpers.complete_job(
                client, report_id, worker_token,
                summary=summary,
                calendar=calendar,
                core_version=WORKER_VERSION + "+cache",
                debug={
                    "cache_hit": True,
                    "cache_key": cache_key,
                    "worker_host": socket.gethostname(),
                    "worker_version": WORKER_VERSION,
                    "total_ms": round((time.time() - start_time) * 1000),
                },
            )
            return

        if listing_url:
            # Mode A: URL scrape — user provided a listing URL
            logger.info(f"[{report_id}] Mode A (URL scrape): {listing_url}")
            try:
                from worker.scraper.price_estimator import run_scrape

                recommended, comps, debug = run_scrape(
                    listing_url=listing_url,
                    checkin=start_date,
                    checkout=end_date,
                    cdp_url=CDP_URL,
                    max_scroll_rounds=MAX_SCROLL_ROUNDS,
                    max_cards=MAX_CARDS,
                    max_runtime_seconds=MAX_RUNTIME_SECONDS,
                    rate_limit_seconds=RATE_LIMIT_SECONDS,
                )

                if recommended and recommended > 0:
                    summary, calendar = _build_scrape_calendar(
                        recommended, start_date, end_date, discount_policy, debug,
                    )
                    core_version = WORKER_VERSION + "+scrape"
                else:
                    logger.warning(f"[{report_id}] Scrape produced no recommendation, falling back to mock")
                    summary, calendar, debug = generate_mock_report(
                        address, attributes, start_date, end_date, discount_policy,
                    )
                    debug["scrape_fallback"] = True
                    debug["scrape_error"] = debug.get("error") or "No recommendation produced"
                    core_version = MOCK_VERSION + "+scrape-fallback"

            except Exception as exc:
                logger.error(f"[{report_id}] Scrape error: {exc}, falling back to mock")
                summary, calendar, debug = generate_mock_report(
                    address, attributes, start_date, end_date, discount_policy,
                )
                debug["scrape_error"] = str(exc)
                debug["scrape_fallback"] = True
                core_version = MOCK_VERSION + "+scrape-fallback"

        elif input_mode == "criteria":
            # Mode B: Criteria search — find best matching listing, then scrape comps
            logger.info(f"[{report_id}] Mode B (criteria search): {address}")
            try:
                from worker.scraper.price_estimator import run_criteria_search

                recommended, comps, debug = run_criteria_search(
                    address=address,
                    attributes=attributes,
                    checkin=start_date,
                    checkout=end_date,
                    cdp_url=CDP_URL,
                    max_scroll_rounds=MAX_SCROLL_ROUNDS,
                    max_cards=MAX_CARDS,
                    max_runtime_seconds=MAX_RUNTIME_SECONDS,
                    rate_limit_seconds=RATE_LIMIT_SECONDS,
                )

                if recommended and recommended > 0:
                    summary, calendar = _build_scrape_calendar(
                        recommended, start_date, end_date, discount_policy, debug,
                    )
                    core_version = WORKER_VERSION + "+criteria"
                else:
                    logger.warning(f"[{report_id}] Criteria search produced no recommendation, falling back to mock")
                    summary, calendar, debug = generate_mock_report(
                        address, attributes, start_date, end_date, discount_policy,
                    )
                    debug["criteria_fallback"] = True
                    debug["criteria_error"] = debug.get("error") or "No recommendation produced"
                    core_version = MOCK_VERSION + "+criteria-fallback"

            except Exception as exc:
                logger.error(f"[{report_id}] Criteria search error: {exc}, falling back to mock")
                summary, calendar, debug = generate_mock_report(
                    address, attributes, start_date, end_date, discount_policy,
                )
                debug["criteria_error"] = str(exc)
                debug["criteria_fallback"] = True
                core_version = MOCK_VERSION + "+criteria-fallback"

        else:
            # Fallback: Mock pricing
            logger.info(f"[{report_id}] Fallback (mock): {address}")
            summary, calendar, debug = generate_mock_report(
                address, attributes, start_date, end_date, discount_policy,
            )
            core_version = MOCK_VERSION

        total_ms = round((time.time() - start_time) * 1000)
        debug.update({
            "cache_hit": False,
            "cache_key": cache_key,
            "worker_host": socket.gethostname(),
            "worker_version": WORKER_VERSION,
            "total_ms": total_ms,
        })

        # Write results
        db_helpers.complete_job(
            client, report_id, worker_token,
            summary=summary,
            calendar=calendar,
            core_version=core_version,
            debug=debug,
        )

        # Store in cache
        try:
            meta = {
                "source": debug.get("source", "unknown"),
                "listing_url": listing_url or "",
                "comps_count": debug.get("comps_collected", 0),
            }
            set_cached(client, cache_key, summary, calendar, meta=meta)
        except Exception as exc:
            logger.warning(f"[{report_id}] Failed to write cache: {exc}")

        logger.info(f"[{report_id}] Completed in {total_ms}ms ({core_version})")

    except Exception as exc:
        elapsed_ms = round((time.time() - start_time) * 1000)
        error_msg = f"Processing failed: {str(exc)[:200]}"
        logger.error(f"[{report_id}] {error_msg}")

        try:
            db_helpers.fail_job(
                client, report_id, worker_token,
                error_message="We encountered an issue processing your report. Please try again.",
                debug={
                    "error": str(exc),
                    "worker_host": socket.gethostname(),
                    "worker_version": WORKER_VERSION,
                    "total_ms": elapsed_ms,
                },
            )
        except Exception as db_exc:
            logger.error(f"[{report_id}] Failed to mark job as error: {db_exc}")

    finally:
        hb_stop.set()
        hb_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------


def main():
    logger.info(f"AriaHost Worker starting (version={WORKER_VERSION})")
    logger.info(f"  poll={POLL_SECONDS}s, stale={STALE_MINUTES}min, max_attempts={MAX_ATTEMPTS}")
    logger.info(f"  heartbeat={HEARTBEAT_SECONDS}s, max_runtime={MAX_RUNTIME_SECONDS}s")
    logger.info(f"  CDP={CDP_URL}")

    client = db_helpers.get_client()
    backoff = POLL_SECONDS
    max_backoff = POLL_SECONDS * 12  # 60s at default

    while not _shutdown_event.is_set():
        try:
            worker_token = uuid.uuid4()
            job = db_helpers.claim_job(client, worker_token, STALE_MINUTES)

            if job is None:
                # No work — wait with current backoff
                _shutdown_event.wait(backoff)
                backoff = min(backoff * 1.5, max_backoff)
                continue

            # Got work — reset backoff
            backoff = POLL_SECONDS
            report_id = job["id"]
            attempts = job.get("worker_attempts", 0)

            if attempts > MAX_ATTEMPTS:
                logger.warning(f"[{report_id}] Exceeded max attempts ({attempts}), marking error")
                db_helpers.fail_job(
                    client, report_id, worker_token,
                    error_message="This report failed after multiple attempts. Please create a new one.",
                    debug={
                        "error": f"Exceeded max attempts ({attempts})",
                        "worker_host": socket.gethostname(),
                        "worker_version": WORKER_VERSION,
                    },
                )
                continue

            logger.info(f"Claimed job {report_id} (attempt {attempts})")
            process_job(job, worker_token)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            logger.error(f"Worker loop error: {exc}")
            _shutdown_event.wait(backoff)
            backoff = min(backoff * 2, max_backoff)

    logger.info("Worker shut down.")


if __name__ == "__main__":
    main()
