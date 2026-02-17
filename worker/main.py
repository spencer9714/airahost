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
from worker.core.dynamic_pricing import compute_dynamic_pricing_adjustment
# mock_core removed — scrape failures now mark jobs as error

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "5"))
STALE_MINUTES = int(os.getenv("WORKER_STALE_MINUTES", "15"))
MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))
HEARTBEAT_SECONDS = int(os.getenv("WORKER_HEARTBEAT_SECONDS", "10"))
MAX_RUNTIME_SECONDS = int(os.getenv("WORKER_MAX_RUNTIME_SECONDS", "180"))
CDP_CONNECT_TIMEOUT_MS = int(os.getenv("CDP_CONNECT_TIMEOUT_MS", "15000"))
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


def _merge_extracted_specs_into_attributes(
    current_attributes: Dict[str, Any],
    transparent_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    For URL mode, overwrite placeholder form values with extracted Airbnb specs
    so downstream UI shows real listing details.

    Reads from transparent_result["targetSpec"] (new format).
    """
    merged = dict(current_attributes or {})
    specs = (transparent_result or {}).get("targetSpec") or {}
    if not isinstance(specs, dict):
        return merged

    if specs.get("propertyType"):
        merged["propertyType"] = specs["propertyType"]
    if isinstance(specs.get("accommodates"), int) and specs["accommodates"] > 0:
        merged["maxGuests"] = specs["accommodates"]
    if isinstance(specs.get("bedrooms"), int) and specs["bedrooms"] >= 0:
        merged["bedrooms"] = specs["bedrooms"]
    if isinstance(specs.get("baths"), (int, float)) and specs["baths"] > 0:
        merged["bathrooms"] = float(specs["baths"])
    if isinstance(specs.get("beds"), int) and specs["beds"] > 0:
        merged["beds"] = specs["beds"]

    return merged


def _should_bypass_precache_for_url_mode(
    input_mode: str,
    listing_url: Optional[str],
) -> bool:
    """
    URL mode must scrape at least once to replace placeholder attributes
    (e.g. 1 bed / 1 bath / 2 guests) with real listing specs.
    """
    return input_mode == "url" and bool(listing_url)


def _build_scrape_calendar(
    daily_results: list,
    start_date: str,
    end_date: str,
    discount_policy: Dict[str, Any],
    transparent_result: Dict[str, Any],
) -> tuple:
    """
    Build summary + calendar from day-by-day scrape results.

    Each entry in daily_results is a dict with at least:
      date, median_price, is_weekend, flags

    Uses per-day median_price as basePrice.  Falls back to the overall
    median for days with no data.  Returns (None, None) if ALL days
    have no price data.
    """
    from datetime import datetime as dt, timedelta as td, timezone as tz
    import statistics as _stats

    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    start = dt.strptime(start_date, "%Y-%m-%d").replace(tzinfo=tz.utc)
    end = dt.strptime(end_date, "%Y-%m-%d").replace(tzinfo=tz.utc)
    today = dt.now(tz.utc).date()
    total_days = max(1, (end - start).days)

    def _policy_num(*keys: str) -> Optional[float]:
        for key in keys:
            if key in discount_policy and discount_policy.get(key) is not None:
                try:
                    return float(discount_policy[key])
                except Exception:
                    continue
        return None

    def _cap_price(price_value: int, floor_value: Optional[float], ceiling_value: Optional[float]) -> int:
        out = float(price_value)
        if floor_value is not None:
            out = max(out, float(floor_value))
        if ceiling_value is not None:
            out = min(out, float(ceiling_value))
        return round(out)

    min_price_floor = _policy_num("minPriceFloor", "min_price_floor")
    max_price_ceiling = _policy_num("maxPriceCeiling", "max_price_ceiling")

    # Build a date -> daily_result lookup
    dr_map: Dict[str, Dict] = {}
    for dr in daily_results:
        dr_map[dr["date"]] = dr

    # Compute overall median for fallback
    valid_prices = [
        dr["median_price"] for dr in daily_results
        if dr.get("median_price") is not None
    ]
    if not valid_prices:
        return None, None

    overall_median = round(_stats.median(valid_prices))

    # Build calendar inputs used by the unified dynamic adjustment pipeline.
    calendar_inputs = []
    for i in range(total_days):
        d = start + td(days=i)
        ds = d.strftime("%Y-%m-%d")
        dr = dr_map.get(ds)
        raw_base = dr.get("median_price") if dr else None
        base_daily_price = round(raw_base) if raw_base is not None else None
        calendar_inputs.append(
            {
                "date": d.date(),
                "baseDailyPrice": base_daily_price,
                "compsUsed": int((dr or {}).get("comps_used") or 0),
                "priceDistribution": (dr or {}).get("price_distribution") or {},
                "flags": list((dr or {}).get("flags") or []),
            }
        )

    dynamic_rows = compute_dynamic_pricing_adjustment(today, calendar_inputs)

    # Build calendar days with discounts after dynamic layer.
    calendar = []
    for i, dynamic in enumerate(dynamic_rows):
        d = start + td(days=i)
        ds = d.strftime("%Y-%m-%d")
        dow = d.weekday()
        is_weekend = dow >= 4  # Fri, Sat

        base_daily_price = dynamic.get("baseDailyPrice")
        price_after_time_adjustment = dynamic.get("priceAfterTimeAdjustment")
        flags = list(dynamic.get("flags") or [])

        if price_after_time_adjustment is not None:
            disc = apply_discount(price_after_time_adjustment, total_days, discount_policy)
            effective_refundable = _cap_price(
                disc["refundablePrice"],
                min_price_floor,
                max_price_ceiling,
            )
            effective_non_refundable = _cap_price(
                disc["nonRefundablePrice"],
                min_price_floor,
                max_price_ceiling,
            )
            legacy_base_price = price_after_time_adjustment
            legacy_refundable = effective_refundable
            legacy_non_refundable = effective_non_refundable
        else:
            # Keep legacy fields numeric for backward-compatible UI rendering.
            legacy_base_price = overall_median
            legacy_disc = apply_discount(legacy_base_price, total_days, discount_policy)
            legacy_refundable = _cap_price(
                legacy_disc["refundablePrice"],
                min_price_floor,
                max_price_ceiling,
            )
            legacy_non_refundable = _cap_price(
                legacy_disc["nonRefundablePrice"],
                min_price_floor,
                max_price_ceiling,
            )
            effective_refundable = None
            effective_non_refundable = None

        entry: Dict[str, Any] = {
            "date": ds,
            "dayOfWeek": DAY_NAMES[dow],
            "isWeekend": is_weekend,
            # Legacy keys retained.
            "basePrice": legacy_base_price,
            "refundablePrice": legacy_refundable,
            "nonRefundablePrice": legacy_non_refundable,
            # Unified dynamic-adjustment fields.
            "baseDailyPrice": base_daily_price,
            "dynamicAdjustment": dynamic.get("dynamicAdjustment"),
            "lastMinuteMultiplier": (dynamic.get("dynamicAdjustment") or {}).get(
                "timeMultiplier"
            ),
            "priceAfterTimeAdjustment": price_after_time_adjustment,
            "effectiveDailyPriceRefundable": effective_refundable,
            "effectiveDailyPriceNonRefundable": effective_non_refundable,
            "flags": flags,
        }
        calendar.append(entry)

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
        base_prices, total_days, discount_policy
    )
    est_monthly = round(selected_range_avg * 30 * (occupancy / 100))

    # Insight from price data
    rec_price_info = (transparent_result or {}).get("recommendedPrice") or {}
    rec = rec_price_info.get("nightly")
    if rec and median:
        diff = round(median - rec)
        if abs(diff) <= 5:
            headline = f"Your recommended price of ${round(rec)}/night is well-aligned with the local market."
        elif diff > 5:
            headline = f"At ${round(rec)}/night you're competitively positioned against the ${median} market median."
        else:
            headline = f"Comparable listings average ${median}/night. Your recommended price factors in market positioning."
    else:
        headline = f"Based on nearby comparable listings, the median nightly price is ${median}."

    weekly_avg = average_refundable_price_for_stay(
        base_prices, min(7, total_days), discount_policy
    )
    monthly_avg = average_refundable_price_for_stay(
        base_prices, min(28, total_days), discount_policy
    )
    stay_length_averages = build_stay_length_averages(
        base_prices, total_days, discount_policy
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
        finalized_input_attributes = dict(attributes)
        finalized_input_attributes["inputMode"] = input_mode
        finalized_input_attributes["listingUrl"] = listing_url or None
        bypass_precache = _should_bypass_precache_for_url_mode(
            str(input_mode), listing_url
        )

        # Check cache first (except URL mode where we must scrape to extract specs)
        cache_key = job.get("cache_key") or compute_cache_key(
            address, attributes, start_date, end_date, discount_policy, listing_url, input_mode
        )
        cached = None if bypass_precache else get_cached(client, cache_key)
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
                input_attributes=finalized_input_attributes,
            )
            return

        # transparent_result holds the new structured output from scrape/criteria
        transparent_result: Optional[Dict[str, Any]] = None

        def _fail(error_msg: str, detail: str = "") -> None:
            """Mark job as error — no mock fallback."""
            logger.warning(f"[{report_id}] Failing: {detail or error_msg}")
            db_helpers.fail_job(
                client, report_id, worker_token,
                error_message=error_msg,
                debug={
                    "error": detail or error_msg,
                    "worker_host": socket.gethostname(),
                    "worker_version": WORKER_VERSION,
                    "total_ms": round((time.time() - start_time) * 1000),
                },
            )

        if listing_url:
            # Mode A: URL scrape — user provided a listing URL
            logger.info(f"[{report_id}] Mode A (URL scrape): {listing_url}")
            try:
                from worker.scraper.price_estimator import run_scrape

                daily_results, transparent_result = run_scrape(
                    listing_url=listing_url,
                    checkin=start_date,
                    checkout=end_date,
                    cdp_url=CDP_URL,
                    max_scroll_rounds=MAX_SCROLL_ROUNDS,
                    max_cards=MAX_CARDS,
                    max_runtime_seconds=MAX_RUNTIME_SECONDS,
                    rate_limit_seconds=RATE_LIMIT_SECONDS,
                    cdp_connect_timeout_ms=CDP_CONNECT_TIMEOUT_MS,
                )

                valid_prices = [r["median_price"] for r in daily_results if r.get("median_price")]
                if daily_results and valid_prices:
                    result = _build_scrape_calendar(
                        daily_results, start_date, end_date, discount_policy, transparent_result,
                    )
                    if result[0] is not None and result[1] is not None:
                        summary, calendar = result
                        finalized_input_attributes = _merge_extracted_specs_into_attributes(
                            finalized_input_attributes, transparent_result
                        )
                        core_version = WORKER_VERSION + "+scrape"
                    else:
                        _fail(
                            "Service is busy. Could not collect enough pricing data — please try again later.",
                            "All day-queries returned no valid prices",
                        )
                        return
                else:
                    scrape_err = ((transparent_result or {}).get("debug") or {}).get("error") or "No results"
                    _fail(
                        "Service is busy. Could not reach Airbnb data — please try again later.",
                        f"Scrape produced no daily results: {scrape_err}",
                    )
                    return

            except ValueError as exc:
                _fail(str(exc), str(exc))
                return

            except Exception as exc:
                _fail(
                    "Service is busy. An error occurred during analysis — please try again later.",
                    str(exc),
                )
                return

        elif input_mode == "criteria":
            # Mode B: Criteria search — find best matching listing, then scrape comps
            logger.info(f"[{report_id}] Mode B (criteria search): {address}")
            try:
                from worker.scraper.price_estimator import run_criteria_search

                daily_results, transparent_result = run_criteria_search(
                    address=address,
                    attributes=attributes,
                    checkin=start_date,
                    checkout=end_date,
                    cdp_url=CDP_URL,
                    max_scroll_rounds=MAX_SCROLL_ROUNDS,
                    max_cards=MAX_CARDS,
                    max_runtime_seconds=MAX_RUNTIME_SECONDS,
                    rate_limit_seconds=RATE_LIMIT_SECONDS,
                    cdp_connect_timeout_ms=CDP_CONNECT_TIMEOUT_MS,
                )

                valid_prices = [r["median_price"] for r in daily_results if r.get("median_price")]
                if daily_results and valid_prices:
                    result = _build_scrape_calendar(
                        daily_results, start_date, end_date, discount_policy, transparent_result,
                    )
                    if result[0] is not None and result[1] is not None:
                        summary, calendar = result
                        core_version = WORKER_VERSION + "+criteria"
                    else:
                        _fail(
                            "Service is busy. Could not collect enough pricing data — please try again later.",
                            "All day-queries returned no valid prices in criteria mode",
                        )
                        return
                else:
                    criteria_err = ((transparent_result or {}).get("debug") or {}).get("error") or "No results"
                    _fail(
                        "Service is busy. Could not reach Airbnb data — please try again later.",
                        f"Criteria search produced no daily results: {criteria_err}",
                    )
                    return

            except ValueError as exc:
                _fail(str(exc), str(exc))
                return

            except Exception as exc:
                _fail(
                    "Service is busy. An error occurred during analysis — please try again later.",
                    str(exc),
                )
                return

        else:
            # No listing URL and no criteria — cannot proceed
            _fail(
                "Please provide either a listing URL or search criteria.",
                "No listing URL and input mode is not criteria",
            )
            return

        total_ms = round((time.time() - start_time) * 1000)

        debug = (transparent_result or {}).get("debug") or {}
        debug.update({
            "cache_hit": False,
            "cache_key": cache_key,
            "cache_bypassed_for_url_mode": bypass_precache,
            "worker_host": socket.gethostname(),
            "worker_version": WORKER_VERSION,
            "total_ms": total_ms,
        })

        # Enrich summary with transparency fields
        if transparent_result:
            summary["targetSpec"] = transparent_result.get("targetSpec")
            summary["queryCriteria"] = transparent_result.get("queryCriteria")
            summary["compsSummary"] = transparent_result.get("compsSummary")
            summary["priceDistribution"] = transparent_result.get("priceDistribution")
            summary["recommendedPrice"] = transparent_result.get("recommendedPrice")
            summary["comparableListings"] = transparent_result.get("comparableListings")

        # Write results
        db_helpers.complete_job(
            client, report_id, worker_token,
            summary=summary,
            calendar=calendar,
            core_version=core_version,
            debug=debug,
            input_attributes=finalized_input_attributes,
        )

        if listing_url:
            try:
                db_helpers.sync_linked_listing_attributes(
                    client, report_id, finalized_input_attributes
                )
            except Exception as exc:
                logger.warning(f"[{report_id}] Failed to sync linked listing attributes: {exc}")

        # Store in cache (enriched summary includes transparency)
        try:
            source = debug.get("source", "unknown")
            comps_count = 0
            if transparent_result:
                comps_count = (transparent_result.get("compsSummary") or {}).get("collected", 0)
            meta = {
                "source": source,
                "listing_url": listing_url or "",
                "comps_count": comps_count,
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
    logger.info(f"  CDP={CDP_URL}, connect_timeout={CDP_CONNECT_TIMEOUT_MS}ms")

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
