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
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

# Load .env from worker directory or repo root.
# override=False so shell-set env vars (e.g. WORKER_LANE, WORKER_ENV) always win.
# This lets multiple local workers run from separate terminals with different config.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)
load_dotenv(override=False)

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
WORKER_ENV = os.getenv("WORKER_ENV", "production")
WORKER_LANE = os.getenv("WORKER_LANE", "interactive")

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

    # Weekday/weekend averages: use actual market comparable medians from the
    # transparent result when available.  These represent what comparable
    # listings charge on those days — not the user's own adjusted prices.
    rec_price_info = (transparent_result or {}).get("recommendedPrice") or {}
    rec = rec_price_info.get("nightly")
    market_weekday = rec_price_info.get("weekdayEstimate")
    market_weekend = rec_price_info.get("weekendEstimate")

    weekday_p = [d["basePrice"] for d in calendar if not d["isWeekend"]]
    weekend_p = [d["basePrice"] for d in calendar if d["isWeekend"]]
    weekday_avg = (
        round(market_weekday) if market_weekday
        else (round(sum(weekday_p) / len(weekday_p)) if weekday_p else median)
    )
    weekend_avg = (
        round(market_weekend) if market_weekend
        else (round(sum(weekend_p) / len(weekend_p)) if weekend_p else median)
    )

    # Occupancy: there is no way to determine true booking occupancy from search
    # data alone.  Use a data-quality proxy: higher market data coverage
    # (fraction of days where we found valid comparable prices) indicates a
    # more active, higher-demand market → higher occupancy estimate.
    valid_day_count = sum(1 for dr in daily_results if dr.get("median_price") is not None)
    coverage_pct = valid_day_count / max(1, len(daily_results))
    if coverage_pct >= 0.80:
        occupancy = 73
    elif coverage_pct >= 0.60:
        occupancy = 67
    elif coverage_pct >= 0.40:
        occupancy = 60
    else:
        occupancy = 53

    selected_range_avg = average_refundable_price_for_stay(
        base_prices, total_days, discount_policy
    )
    est_monthly = round(selected_range_avg * 30 * (occupancy / 100))
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
    Process a single pricing report job (live_analysis only).

    forecast_snapshot jobs are no longer executed; any stale queued
    forecast_snapshot rows will be failed immediately if encountered.
    """
    report_id = job["id"]

    # Safeguard: forecast_snapshot jobs must never be executed.
    # The creation API routes now return 410 Gone, so no new ones should arrive.
    # Any residual queued forecast_snapshot rows from before the deprecation
    # are failed here so they don't block the queue.
    if job.get("report_type") == "forecast_snapshot":
        logger.warning(
            f"[{report_id}] Received deprecated forecast_snapshot job — failing immediately. "
            "No new forecast_snapshot jobs should be created."
        )
        client = db_helpers.get_client()
        db_helpers.fail_job(
            client, report_id, worker_token,
            error_message="forecast_snapshot has been removed. Please run a live analysis instead.",
            debug={
                "error": "forecast_snapshot_deprecated",
                "worker_host": socket.gethostname(),
                "worker_version": WORKER_VERSION,
            },
        )
        return

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

    def _progress(pct: int, stage: str, message: str, est: Optional[int] = None) -> None:
        """Update progress metadata in DB.  Non-fatal on error."""
        try:
            db_helpers.update_progress(
                client, report_id, worker_token,
                pct=pct, stage=stage, message=message,
                est_seconds_remaining=est,
            )
        except Exception as _pe:
            logger.warning(f"[{report_id}] Progress update failed (non-fatal): {_pe}")

    def _make_day_callback(start_pct: int, end_pct: int, stage: str, message_prefix: str) -> Callable[[int, int], None]:
        """Factory: maps (completed, total) day counts to a pct range and calls _progress."""
        def _cb(completed: int, total: int) -> None:
            if total <= 0:
                return
            frac = completed / total
            pct = round(start_pct + frac * (end_pct - start_pct))
            remaining_days = total - completed
            est = round(remaining_days * MAX_RUNTIME_SECONDS / max(total, 1)) if remaining_days > 0 else None
            _progress(pct, stage, f"{message_prefix} ({completed}/{total} days)", est)
        return _cb

    _progress(5, "connecting", "Connecting to browser...")

    try:
        # ── Nightly live-reload ───────────────────────────────────────────────
        # Scheduled nightly jobs use the saved listing as source-of-truth at
        # execution time, not the snapshot baked in at queue time.  This means
        # any benchmark / comp / setting changes the host makes after queuing
        # are still reflected in tonight's report.
        #
        # Manual and rerun jobs keep snapshot semantics — their input_attributes
        # represent the user's deliberate choices at the moment they clicked Run.
        if job.get("job_lane") == "nightly" and job.get("listing_id"):
            try:
                _reload_row = (
                    client.table("saved_listings")
                    .select("input_address, input_attributes, default_discount_policy")
                    .eq("id", job["listing_id"])
                    .single()
                    .execute()
                )
                _fresh = _reload_row.data or {}
                if _fresh:
                    job = dict(job)  # shallow copy — do not mutate the original
                    if _fresh.get("input_address"):
                        job["input_address"] = _fresh["input_address"]
                    if _fresh.get("input_attributes") is not None:
                        _fresh_attrs = _fresh["input_attributes"] or {}
                        job["input_attributes"] = _fresh_attrs
                        # Keep input_listing_url in sync with refreshed attributes
                        job["input_listing_url"] = (
                            _fresh_attrs.get("listingUrl")
                            or _fresh_attrs.get("listing_url")
                            or job.get("input_listing_url")
                        )
                    if _fresh.get("default_discount_policy") is not None:
                        job["discount_policy"] = _fresh["default_discount_policy"]
                    logger.info(
                        f"[{report_id}] Nightly live-reload: refreshed inputs "
                        f"from saved_listing {job['listing_id']}"
                    )
            except Exception as _reload_exc:
                logger.warning(
                    f"[{report_id}] Nightly live-reload failed (non-fatal, using snapshot): "
                    f"{_reload_exc}"
                )

        address = job.get("input_address", "")
        attributes = job.get("input_attributes") or {}
        start_date = str(job.get("input_date_start", ""))
        end_date = str(job.get("input_date_end", ""))
        discount_policy = job.get("discount_policy") or {}
        listing_url = _get_listing_url(job)
        input_mode = attributes.get("inputMode", "criteria")
        # Extract preferred comps list — only keep items that are enabled
        preferred_comps_raw = attributes.get("preferredComps")
        preferred_comps: Optional[list] = None
        primary_benchmark_url: Optional[str] = None
        if isinstance(preferred_comps_raw, list):
            enabled = [
                pc for pc in preferred_comps_raw
                if isinstance(pc, dict) and pc.get("enabled", True)
            ]
            preferred_comps = enabled if enabled else None
        if preferred_comps:
            urls_preview = ", ".join(pc.get("listingUrl", "?") for pc in preferred_comps)
            logger.info(f"[{job.get('id', '?')}] Preferred comps ({len(preferred_comps)}): {urls_preview}")
            # The first enabled preferred comp is the primary benchmark
            first_url = str(preferred_comps[0].get("listingUrl") or "").strip()
            if first_url:
                primary_benchmark_url = first_url
                logger.info(f"[{job.get('id', '?')}] Primary benchmark URL: {primary_benchmark_url}")

        # Secondary benchmark URLs — preferredComps[1:] used for consensus signal only
        secondary_benchmark_urls: List[str] = [
            str(pc.get("listingUrl") or "").strip()
            for pc in (preferred_comps or [])[1:]
            if isinstance(pc, dict) and str(pc.get("listingUrl") or "").strip()
        ]
        if secondary_benchmark_urls:
            logger.info(
                f"[{job.get('id', '?')}] Secondary benchmark URLs "
                f"({len(secondary_benchmark_urls)}): {', '.join(secondary_benchmark_urls)}"
            )
        finalized_input_attributes = dict(attributes)
        finalized_input_attributes["inputMode"] = input_mode
        finalized_input_attributes["listingUrl"] = listing_url or None
        bypass_precache = _should_bypass_precache_for_url_mode(
            str(input_mode), listing_url
        )

        # Check cache first (except URL mode where we must scrape to extract specs,
        # or explicit force_rerun jobs submitted from the dashboard).
        # For nightly jobs the queued cache_key was computed from the snapshot
        # at queue time — after live-reload, execution inputs may differ so we
        # must recompute from the actual values used.  Manual/rerun jobs keep
        # the original key (snapshot semantics, no reload).
        if job.get("job_lane") == "nightly":
            cache_key = compute_cache_key(
                address, attributes, start_date, end_date, discount_policy, listing_url, input_mode
            )
        else:
            cache_key = job.get("cache_key") or compute_cache_key(
                address, attributes, start_date, end_date, discount_policy, listing_url, input_mode
            )
        force_rerun = bool((job.get("result_core_debug") or {}).get("force_rerun"))
        cached = None if (bypass_precache or force_rerun) else get_cached(client, cache_key)
        if force_rerun:
            logger.info(f"[{report_id}] force_rerun=true — skipping cache lookup")
        if cached:
            summary, calendar = cached
            logger.info(f"[{report_id}] Cache hit for key={cache_key[:12]}...")
            # Live price is time-sensitive — always re-capture fresh even on a cache hit.
            # Shallow-copy summary so we don't mutate the cached object.
            summary = dict(summary)
            if listing_url:
                from datetime import datetime as _dt, timedelta as _td
                _live_checkin = start_date
                try:
                    _live_checkout = (
                        _dt.strptime(start_date, "%Y-%m-%d") + _td(days=1)
                    ).strftime("%Y-%m-%d")
                except Exception:
                    _live_checkout = end_date
                try:
                    from worker.scraper.target_extractor import capture_target_live_price
                    _cache_live_info = capture_target_live_price(
                        listing_url=listing_url,
                        checkin=_live_checkin,
                        checkout=_live_checkout,
                        cdp_url=CDP_URL,
                        cdp_connect_timeout_ms=CDP_CONNECT_TIMEOUT_MS,
                    )
                    logger.info(
                        f"[{report_id}] Cache-hit live price: "
                        f"status={_cache_live_info.get('livePriceStatus')} "
                        f"price={_cache_live_info.get('observedListingPrice')}"
                    )
                except Exception as _lpe:
                    logger.warning(f"[{report_id}] Cache-hit live price error (non-fatal): {_lpe}")
                    _cache_live_info = {
                        "livePriceStatus": "scrape_failed",
                        "livePriceStatusReason": str(_lpe)[:300],
                    }
                summary.update(_cache_live_info)
                _observed = _cache_live_info.get("observedListingPrice")
                if isinstance(_observed, (int, float)) and _observed > 0:
                    _market_median = summary.get("nightlyMedian")
                    _recommended = (summary.get("recommendedPrice") or {}).get("nightly")
                    if isinstance(_market_median, (int, float)) and _market_median > 0:
                        _obs_vs_mkt_diff = round(_observed - _market_median)
                        _obs_vs_mkt_pct = round((_observed / _market_median - 1) * 100)
                        summary["observedVsMarketDiff"] = _obs_vs_mkt_diff
                        summary["observedVsMarketDiffPct"] = _obs_vs_mkt_pct
                        if _obs_vs_mkt_pct < -3:
                            summary["pricingPosition"] = "below_market"
                        elif _obs_vs_mkt_pct > 3:
                            summary["pricingPosition"] = "above_market"
                        else:
                            summary["pricingPosition"] = "at_market"
                    if isinstance(_recommended, (int, float)) and _recommended > 0:
                        _obs_vs_rec_diff = round(_observed - _recommended)
                        _obs_vs_rec_pct = round((_observed / _recommended - 1) * 100)
                        summary["observedVsRecommendedDiff"] = _obs_vs_rec_diff
                        summary["observedVsRecommendedDiffPct"] = _obs_vs_rec_pct
                        if _obs_vs_rec_diff > 10:
                            summary["pricingAction"] = "lower"
                            summary["pricingActionTarget"] = int(round(_recommended))
                        elif _obs_vs_rec_diff < -10:
                            summary["pricingAction"] = "raise"
                            summary["pricingActionTarget"] = int(round(_recommended))
                        else:
                            summary["pricingAction"] = "keep"
                            summary["pricingActionTarget"] = int(round(_observed))
            else:
                summary["livePriceStatus"] = "no_listing_url"
                summary["livePriceStatusReason"] = "No Airbnb listing URL configured for this property"
            _is_nightly = job.get("job_lane") == "nightly"
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
                # For nightly jobs: write all refreshed execution inputs back to the
                # report row so it fully reflects actual inputs, not queued snapshot.
                input_address=address if _is_nightly else None,
                # write_input_listing_url=True allows explicit NULL-clear when no URL.
                input_listing_url=listing_url if _is_nightly else None,
                write_input_listing_url=_is_nightly,
                discount_policy=discount_policy if _is_nightly else None,
                # Sync the recomputed execution cache key to the report row.
                cache_key=cache_key if _is_nightly else None,
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

        # ── Phase 3A: Resolve target coordinates ─────────────────────────
        # Fetch existing coords from the saved listing, or geocode the address
        # if they are missing.  Best-effort: failure is logged and skipped.
        _job_target_lat: Optional[float] = None
        _job_target_lng: Optional[float] = None
        _geocoded_now = False  # True if we just geocoded (need to write back)
        _db_timezone: Optional[str] = None

        _listing_id_for_geocode = job.get("listing_id")
        if _listing_id_for_geocode:
            try:
                _geo_row = (
                    client.table("saved_listings")
                    .select("target_lat, target_lng, listing_timezone")
                    .eq("id", _listing_id_for_geocode)
                    .single()
                    .execute()
                )
                _geo_data = (_geo_row.data or {})
                _db_lat = _geo_data.get("target_lat")
                _db_lng = _geo_data.get("target_lng")
                _db_timezone = _geo_data.get("listing_timezone")
                if _db_lat is not None and _db_lng is not None:
                    _job_target_lat = float(_db_lat)
                    _job_target_lng = float(_db_lng)
                    logger.info(
                        f"[{report_id}] Target coords from DB: "
                        f"({_job_target_lat:.5f}, {_job_target_lng:.5f})"
                    )
                else:
                    # Coords not yet stored — geocode the input address
                    from worker.core.geocoding import geocode_address
                    _gc = geocode_address(address)
                    if _gc:
                        _job_target_lat, _job_target_lng = _gc
                        _geocoded_now = True
                        logger.info(
                            f"[{report_id}] Geocoded target: "
                            f"({_job_target_lat:.5f}, {_job_target_lng:.5f})"
                        )
            except Exception as _geo_exc:
                logger.warning(
                    f"[{report_id}] Target coord resolution failed (non-fatal): {_geo_exc}"
                )

        # ── Phase 3B: Adaptive radius selection ──────────────────────────
        _job_radius_km: float = 30.0  # default; overwritten below if pool data available
        if _listing_id_for_geocode:
            try:
                from worker.core.geo_radius import select_adaptive_radius
                _pool_rows = (
                    client.table("comparable_pool_entries")
                    .select("distance_to_target_km")
                    .eq("saved_listing_id", _listing_id_for_geocode)
                    .eq("status", "active")
                    .execute()
                )
                _pool_distances = [
                    r.get("distance_to_target_km")
                    for r in (_pool_rows.data or [])
                ]
                _pool_active_size = len([d for d in _pool_distances if d is not None])
                _job_radius_km, _radius_reason = select_adaptive_radius(
                    pool_distances=_pool_distances,
                    active_pool_size=_pool_active_size or None,
                )
                logger.info(
                    f"[{report_id}] Adaptive radius: {_job_radius_km:.0f} km — {_radius_reason}"
                )
            except Exception as _radius_exc:
                logger.warning(
                    f"[{report_id}] Radius selection failed (using 30 km default): {_radius_exc}"
                )

        _mode_c_succeeded = False
        # benchmarkInfo is preserved here even when Mode C falls back to Mode B/A.
        # Without this, a failed-but-attempted benchmark run would lose all transparency.
        _saved_benchmark_info: Optional[Dict[str, Any]] = None

        if primary_benchmark_url and input_mode in ("criteria", "criteria-by-city", "criteria-by-zip"):
            # Mode C: Benchmark-first — use pinned comp as primary anchor.
            # Only for criteria modes; URL mode already has its own listing to scrape.
            logger.info(
                f"[{report_id}] Mode C (benchmark-first): {primary_benchmark_url}"
            )
            _progress(10, "fetching_benchmark", "Fetching benchmark listing data...")
            try:
                from worker.scraper.price_estimator import run_benchmark_scrape

                daily_results, transparent_result = run_benchmark_scrape(
                    benchmark_url=primary_benchmark_url,
                    checkin=start_date,
                    checkout=end_date,
                    cdp_url=CDP_URL,
                    max_scroll_rounds=MAX_SCROLL_ROUNDS,
                    max_cards=MAX_CARDS,
                    max_runtime_seconds=MAX_RUNTIME_SECONDS,
                    rate_limit_seconds=RATE_LIMIT_SECONDS,
                    cdp_connect_timeout_ms=CDP_CONNECT_TIMEOUT_MS,
                    secondary_benchmark_urls=secondary_benchmark_urls or None,
                    user_attributes=attributes,
                    target_lat=_job_target_lat,
                    target_lng=_job_target_lng,
                    max_radius_km=_job_radius_km,
                    progress_callback=_make_day_callback(15, 75, "searching_comps", "Searching comparable listings"),
                )

                # Save benchmark transparency now — before any fallback overwrites transparent_result.
                _saved_benchmark_info = (transparent_result or {}).get("benchmarkInfo") or None

                valid_prices = [r["median_price"] for r in daily_results if r.get("median_price")]
                if daily_results and valid_prices:
                    result = _build_scrape_calendar(
                        daily_results, start_date, end_date, discount_policy, transparent_result,
                    )
                    if result[0] is not None and result[1] is not None:
                        summary, calendar = result
                        core_version = WORKER_VERSION + "+benchmark"
                        _mode_c_succeeded = True
                    else:
                        logger.warning(
                            f"[{report_id}] Benchmark pipeline returned no valid prices, "
                            "falling back to criteria search"
                        )
                else:
                    logger.warning(
                        f"[{report_id}] Benchmark pipeline returned empty results, "
                        "falling back to criteria search"
                    )

            except Exception as exc:
                logger.warning(
                    f"[{report_id}] Benchmark pipeline failed ({exc}), "
                    "falling back to criteria search"
                )
                transparent_result = None

        if not _mode_c_succeeded and listing_url:
            # Mode A: URL scrape — user provided a listing URL
            logger.info(f"[{report_id}] Mode A (URL scrape): {listing_url}")
            _progress(10, "extracting_target", "Extracting listing details...")
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
                    preferred_comps=preferred_comps,
                    target_lat=_job_target_lat,
                    target_lng=_job_target_lng,
                    max_radius_km=_job_radius_km,
                    progress_callback=_make_day_callback(15, 75, "searching_comps", "Searching comparable listings"),
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
                logger.exception(f"[{report_id}] ValueError in URL mode")
                _fail(str(exc), str(exc))
                return

            except Exception as exc:
                _fail(
                    "Service is busy. An error occurred during analysis — please try again later.",
                    str(exc),
                )
                return

        elif not _mode_c_succeeded and input_mode in ("criteria", "criteria-by-city", "criteria-by-zip"):
            # Mode B: Criteria search — find best matching listing, then scrape comps
            logger.info(f"[{report_id}] Mode B (criteria search, mode={input_mode}): {address}")
            _progress(10, "searching_comps", "Searching for comparable listings...")
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
                    preferred_comps=preferred_comps,
                    target_lat=_job_target_lat,
                    target_lng=_job_target_lng,
                    max_radius_km=_job_radius_km,
                    progress_callback=_make_day_callback(15, 75, "searching_comps", "Searching comparable listings"),
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
                logger.exception(f"[{report_id}] ValueError in criteria mode")
                _fail(str(exc), str(exc))
                return

            except Exception as exc:
                _fail(
                    "Service is busy. An error occurred during analysis — please try again later.",
                    str(exc),
                )
                return

        elif not _mode_c_succeeded:
            # No listing URL and no criteria — cannot proceed
            _fail(
                "Please provide either a listing URL or search criteria.",
                "No listing URL and input mode is not criteria",
            )
            return

        _progress(80, "pricing", "Computing final pricing estimates...")

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
            # Use benchmarkInfo from transparent_result if present; otherwise use the
            # one saved before Mode C fell back (preserves benchmark data across fallbacks).
            bm_info = transparent_result.get("benchmarkInfo") or _saved_benchmark_info
            if bm_info:
                summary["benchmarkInfo"] = bm_info

        # ── Live price capture (target listing) ──────────────────────────────
        # Attempt to read the host's current listed nightly price from Airbnb
        # for the first night of the report window.
        # Date basis: checkin = start_date, checkout = start_date + 1 day.
        # This is the price a guest would see booking that specific night.
        # Non-fatal: failure is logged and recorded in summary but does not
        # block the job from completing.
        if listing_url:
            from datetime import datetime as _dt, timedelta as _td
            _live_checkin = start_date
            try:
                _live_checkout = (
                    _dt.strptime(start_date, "%Y-%m-%d") + _td(days=1)
                ).strftime("%Y-%m-%d")
            except Exception:
                _live_checkout = end_date

            _progress(85, "capturing_live_price", "Capturing your current listing price from Airbnb...")
            try:
                from worker.scraper.target_extractor import capture_target_live_price
                live_price_info = capture_target_live_price(
                    listing_url=listing_url,
                    checkin=_live_checkin,
                    checkout=_live_checkout,
                    cdp_url=CDP_URL,
                    cdp_connect_timeout_ms=CDP_CONNECT_TIMEOUT_MS,
                )
                logger.info(
                    f"[{report_id}] Live price capture: "
                    f"status={live_price_info.get('livePriceStatus')} "
                    f"price={live_price_info.get('observedListingPrice')} "
                    f"confidence={live_price_info.get('observedListingPriceConfidence')}"
                )
            except Exception as _lpe:
                logger.warning(f"[{report_id}] Live price capture error (non-fatal): {_lpe}")
                live_price_info = {
                    "livePriceStatus": "scrape_failed",
                    "livePriceStatusReason": str(_lpe)[:300],
                }

            # Merge live price fields into summary
            summary.update(live_price_info)

            # Compute comparison intelligence when observed price is available
            _observed = live_price_info.get("observedListingPrice")
            if isinstance(_observed, (int, float)) and _observed > 0:
                _market_median = summary.get("nightlyMedian")
                _recommended = (summary.get("recommendedPrice") or {}).get("nightly")

                if isinstance(_market_median, (int, float)) and _market_median > 0:
                    _obs_vs_mkt_diff = round(_observed - _market_median)
                    _obs_vs_mkt_pct = round((_observed / _market_median - 1) * 100)
                    summary["observedVsMarketDiff"] = _obs_vs_mkt_diff
                    summary["observedVsMarketDiffPct"] = _obs_vs_mkt_pct
                    if _obs_vs_mkt_pct < -3:
                        summary["pricingPosition"] = "below_market"
                    elif _obs_vs_mkt_pct > 3:
                        summary["pricingPosition"] = "above_market"
                    else:
                        summary["pricingPosition"] = "at_market"

                if isinstance(_recommended, (int, float)) and _recommended > 0:
                    _obs_vs_rec_diff = round(_observed - _recommended)
                    _obs_vs_rec_pct = round((_observed / _recommended - 1) * 100)
                    summary["observedVsRecommendedDiff"] = _obs_vs_rec_diff
                    summary["observedVsRecommendedDiffPct"] = _obs_vs_rec_pct

                    # Pricing action: >$10 from recommendation triggers suggest
                    if _obs_vs_rec_diff > 10:
                        summary["pricingAction"] = "lower"
                        summary["pricingActionTarget"] = int(round(_recommended))
                    elif _obs_vs_rec_diff < -10:
                        summary["pricingAction"] = "raise"
                        summary["pricingActionTarget"] = int(round(_recommended))
                    else:
                        summary["pricingAction"] = "keep"
                        summary["pricingActionTarget"] = int(round(_observed))
        else:
            # No listing URL configured
            summary["livePriceStatus"] = "no_listing_url"
            summary["livePriceStatusReason"] = "No Airbnb listing URL configured for this property"

        _progress(90, "saving_results", "Saving results...")

        # Write results
        _is_nightly = job.get("job_lane") == "nightly"
        db_helpers.complete_job(
            client, report_id, worker_token,
            summary=summary,
            calendar=calendar,
            core_version=core_version,
            debug=debug,
            input_attributes=finalized_input_attributes,
            # For nightly jobs: write all refreshed execution inputs back to the
            # report row so it fully reflects actual inputs, not queued snapshot.
            input_address=address if _is_nightly else None,
            # write_input_listing_url=True allows explicit NULL-clear when no URL.
            input_listing_url=listing_url if _is_nightly else None,
            write_input_listing_url=_is_nightly,
            discount_policy=discount_policy if _is_nightly else None,
            # Sync the recomputed execution cache key to the report row.
            cache_key=cache_key if _is_nightly else None,
        )

        if listing_url:
            try:
                db_helpers.sync_linked_listing_attributes(
                    client, report_id, finalized_input_attributes
                )
            except Exception as exc:
                logger.warning(f"[{report_id}] Failed to sync linked listing attributes: {exc}")

        # Write back geocoded target coords to saved_listings (Phase 3A)
        if _geocoded_now and _listing_id_for_geocode and _job_target_lat is not None:
            try:
                client.table("saved_listings").update({
                    "target_lat": _job_target_lat,
                    "target_lng": _job_target_lng,
                }).eq("id", _listing_id_for_geocode).execute()
                logger.info(
                    f"[{report_id}] Saved geocoded coords to listing {_listing_id_for_geocode}"
                )
            except Exception as exc:
                logger.warning(f"[{report_id}] Failed to write geocoded coords (non-fatal): {exc}")

        # Phase 3B: write back page-extracted coords when the listing page gave us
        # better coordinates than geocoding (URL mode and benchmark mode).
        # _final_lat/_final_lng track the best coords known after all sources;
        # they start from geocoded/DB coords and are superseded by page coords
        # when available — this drives the timezone enrichment below.
        _final_lat: Optional[float] = _job_target_lat
        _final_lng: Optional[float] = _job_target_lng

        if _listing_id_for_geocode and transparent_result:
            _page_coords = (transparent_result or {}).get("pageExtractedCoords")
            if _page_coords and _page_coords.get("source") == "page":
                _pc_lat = _page_coords.get("lat")
                _pc_lng = _page_coords.get("lng")
                if _pc_lat is not None and _pc_lng is not None:
                    try:
                        client.table("saved_listings").update({
                            "target_lat": _pc_lat,
                            "target_lng": _pc_lng,
                        }).eq("id", _listing_id_for_geocode).execute()
                        logger.info(
                            f"[{report_id}] Saved page-extracted coords "
                            f"({_pc_lat:.5f}, {_pc_lng:.5f}) to listing {_listing_id_for_geocode}"
                        )
                        # Page coords are more trustworthy — use them as final coords
                        _final_lat = _pc_lat
                        _final_lng = _pc_lng
                    except Exception as exc:
                        logger.warning(
                            f"[{report_id}] Failed to write page-extracted coords (non-fatal): {exc}"
                        )

        # Write back listing timezone using the best coords known this run.
        # Runs after all coord sources (geocoded AND page-extracted) so that
        # page-extracted coordinates also trigger same-run timezone persistence.
        # Does not overwrite an already-stored timezone.
        if (
            _listing_id_for_geocode
            and _final_lat is not None
            and _final_lng is not None
            and not _db_timezone
        ):
            try:
                from timezonefinder import TimezoneFinder
                _tf = TimezoneFinder()
                _resolved_tz = _tf.timezone_at(lat=_final_lat, lng=_final_lng)
                if _resolved_tz:
                    client.table("saved_listings").update({
                        "listing_timezone": _resolved_tz,
                    }).eq("id", _listing_id_for_geocode).execute()
                    logger.info(
                        f"[{report_id}] Saved listing_timezone={_resolved_tz} "
                        f"to listing {_listing_id_for_geocode} "
                        f"(coords source: {'page' if _final_lat != _job_target_lat else 'geocode/db'})"
                    )
            except Exception as exc:
                logger.warning(f"[{report_id}] Failed to resolve/write listing timezone (non-fatal): {exc}")

        # Phase 3B: write back the adaptive radius used this run
        if _listing_id_for_geocode:
            try:
                client.table("saved_listings").update({
                    "comp_pool_target_radius_km": _job_radius_km,
                }).eq("id", _listing_id_for_geocode).execute()
                logger.debug(
                    f"[{report_id}] Saved comp_pool_target_radius_km={_job_radius_km} "
                    f"to listing {_listing_id_for_geocode}"
                )
            except Exception as exc:
                logger.warning(
                    f"[{report_id}] Failed to write target radius (non-fatal): {exc}"
                )

        # Seed comparable pool (Phase 2) — non-fatal, runs after job is marked ready
        _listing_id = job.get("listing_id")
        if _listing_id and transparent_result:
            try:
                from worker.core.pool_seeding import seed_pool_from_report
                seed_pool_from_report(
                    client,
                    saved_listing_id=_listing_id,
                    comparable_listings=transparent_result.get("comparableListings") or [],
                )
            except Exception as exc:
                logger.warning(f"[{report_id}] Pool seeding failed (non-fatal): {exc}")

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
            # Strip live-price fields before caching — they are time-sensitive
            # and must be re-captured fresh on every job run (including cache hits).
            _LIVE_PRICE_KEYS = {
                "observedListingPrice", "observedListingPriceDate",
                "observedListingPriceCapturedAt", "observedListingPriceSource",
                "observedListingPriceConfidence", "observedVsMarketDiff",
                "observedVsMarketDiffPct", "observedVsRecommendedDiff",
                "observedVsRecommendedDiffPct", "pricingPosition",
                "pricingAction", "pricingActionTarget",
                "livePriceStatus", "livePriceStatusReason",
            }
            _cache_safe_summary = {k: v for k, v in summary.items() if k not in _LIVE_PRICE_KEYS}
            set_cached(client, cache_key, _cache_safe_summary, calendar, meta=meta)
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
    logger.info(f"  env={WORKER_ENV}, lane={WORKER_LANE}, poll={POLL_SECONDS}s, stale={STALE_MINUTES}min, max_attempts={MAX_ATTEMPTS}")
    logger.info(f"  heartbeat={HEARTBEAT_SECONDS}s, max_runtime={MAX_RUNTIME_SECONDS}s")
    logger.info(f"  CDP={CDP_URL}, connect_timeout={CDP_CONNECT_TIMEOUT_MS}ms")

    client = db_helpers.get_client()
    backoff = POLL_SECONDS
    max_backoff = POLL_SECONDS * 12  # 60s at default

    while not _shutdown_event.is_set():
        try:
            worker_token = uuid.uuid4()
            job = db_helpers.claim_job(client, worker_token, STALE_MINUTES, WORKER_ENV, WORKER_LANE)

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
