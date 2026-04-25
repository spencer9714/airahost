"""
worker/alerts.py — Nightly pricing alert evaluation and email delivery.

Entry point: run_alert_evaluation(job, summary, client, listing_url, calendar, ...)

SAFETY INVARIANT:
  This module MUST only be called for job_lane="nightly" jobs.
  Manual and rerun jobs must never reach run_alert_evaluation().
  The caller in main.py enforces this guard; this module also asserts it
  as a second layer of protection.

ALERT ELIGIBILITY INVARIANT:
  Alert emails are only sent when saved_listings.pricing_alerts_enabled == true.
  This is checked in Phase C before any live price capture occurs.

Flow:
  Phase B  — Build near-term alert window (D0–D4) and capture live prices
  Phase C  — Eligibility check (alerts enabled, URL present)
  Phase D  — Per-night threshold evaluation (vs recommendedDailyPrice + market ref)
  Phase D2 — Sellability filter (exclude booked/unavailable nights)
  Phase D3 — Bundle: collect actionable sellable nights; suppress if none
  Phase E  — Cooldown / dedupe suppression using primary night's data
  Phase F  — Build and send one bundled alert email per run
  Phase G  — Log to pricing_alert_log + update state on saved_listings
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("worker.alerts")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Both vs_recommended_pct AND vs_market_pct must exceed this (in absolute %)
# in the same direction for a night to be considered actionable.
ALERT_THRESHOLD_PCT: float = 10.0

# Minimum absolute dollar difference vs recommended price.
# Prevents noisy alerts on low-priced listings where 10% is only a few dollars.
ALERT_MIN_DOLLAR_DIFF: float = 5.0

# Same-direction cooldown: if an alert was sent in the same direction within
# this many hours, suppress unless price changed by >= PRICE_CHANGE_MIN.
COOLDOWN_HOURS: int = 48

# Minimum price change (vs last alert price) to break the cooldown.
PRICE_CHANGE_MIN: float = 3.0

# Near-term alert evaluation window (D0 = report start date, inclusive).
# Only nights within this window are evaluated for alert eligibility.
# Keeps alerts actionable: near-term dates are both reliable and sellable.
ALERT_WINDOW_DAYS: int = 5

# Resend configuration (set in worker/.env or environment)
RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
RESEND_FROM: str = os.getenv("RESEND_FROM", "alerts@airahost.com")
DASHBOARD_URL: str = os.getenv("NEXT_PUBLIC_APP_URL", "https://airahost.com")

# ---------------------------------------------------------------------------
# Local dev force-send (LOCAL MODE ONLY — never active in production)
# ---------------------------------------------------------------------------
#
# Set in worker/.env for local testing only:
#   WORKER_ENV=local
#   ALERT_FORCE_SEND=true
#   ALERT_FORCE_TO_EMAIL=you@example.com   (optional; falls back to listing owner)
#
# Both WORKER_ENV=local AND ALERT_FORCE_SEND=true must be set.
# If either is absent or WORKER_ENV != "local", force-send is silently inactive.

_WORKER_ENV: str = os.getenv("WORKER_ENV", "production").lower()

# Evaluated once at import time; False in all non-local environments.
ALERT_FORCE_SEND: bool = (
    _WORKER_ENV == "local"
    and os.getenv("ALERT_FORCE_SEND", "").lower() == "true"
)

# Override recipient for force-send. Empty string means use listing owner email.
ALERT_FORCE_TO_EMAIL: str = os.getenv("ALERT_FORCE_TO_EMAIL", "").strip()


# ---------------------------------------------------------------------------
# Phase B helpers — Alert window construction and multi-night live price capture
# ---------------------------------------------------------------------------


def _build_alert_window_dates(start_date: str, window_days: int) -> List[str]:
    """
    Return a list of YYYY-MM-DD strings starting at start_date for window_days nights.

    Example: start_date="2025-05-03", window_days=5
      → ["2025-05-03", "2025-05-04", "2025-05-05", "2025-05-06", "2025-05-07"]
    """
    try:
        base = datetime.strptime(start_date, "%Y-%m-%d")
    except Exception:
        return [start_date]
    return [
        (base + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(window_days)
    ]


def _build_calendar_index(calendar: Optional[List[Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Build a date-keyed dict from the report calendar for O(1) lookups.

    Handles both dict-style and object-style calendar entries.
    Keys are YYYY-MM-DD strings.
    """
    index: Dict[str, Dict[str, Any]] = {}
    if not calendar:
        return index
    for entry in calendar:
        if isinstance(entry, dict):
            date = entry.get("date")
            if date:
                index[date] = entry
        else:
            date = getattr(entry, "date", None)
            if date:
                index[date] = {
                    "date": date,
                    "recommendedDailyPrice": getattr(entry, "recommendedDailyPrice", None),
                    "baseDailyPrice": getattr(entry, "baseDailyPrice", None),
                    "basePrice": getattr(entry, "basePrice", None),
                }
    return index


def _get_night_prices(
    cal_entry: Optional[Dict[str, Any]],
    summary_fallback_rec: Optional[float],
    summary_fallback_mkt: Optional[float],
) -> Tuple[Optional[float], Optional[float]]:
    """
    Extract (recommended_price, market_price) for a single calendar night.

    Priority for recommended_price:
      1. cal_entry.recommendedDailyPrice  (canonical: baseDailyPrice × demandAdjustment)
      2. cal_entry.baseDailyPrice         (market reference)
      3. cal_entry.basePrice              (legacy)
      4. summary_fallback_rec             (summary-level recommendedPrice.nightly)

    Priority for market_price:
      1. cal_entry.baseDailyPrice
      2. cal_entry.basePrice
      3. summary_fallback_mkt             (summary.nightlyMedian)
    """
    if cal_entry:
        rec = (
            cal_entry.get("recommendedDailyPrice")
            or cal_entry.get("baseDailyPrice")
            or cal_entry.get("basePrice")
        )
        mkt = (
            cal_entry.get("baseDailyPrice")
            or cal_entry.get("basePrice")
        )
    else:
        rec = None
        mkt = None

    if not isinstance(rec, (int, float)) or rec <= 0:
        rec = summary_fallback_rec
    if not isinstance(mkt, (int, float)) or mkt <= 0:
        mkt = summary_fallback_mkt or rec

    return (
        float(rec) if isinstance(rec, (int, float)) and rec > 0 else None,
        float(mkt) if isinstance(mkt, (int, float)) and mkt > 0 else None,
    )


def _capture_window_live_prices(
    listing_url: str,
    dates: List[str],
    minimum_booking_nights: int,
    cdp_url: str,
    cdp_connect_timeout_ms: int,
    d0_reuse_price: Optional[float] = None,
    d0_reuse_status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Capture live prices for a list of dates using one shared Playwright session.

    Reuses d0_reuse_price / d0_reuse_status for dates[0] when provided and valid,
    avoiding a redundant browser navigation for the date already captured by the
    main nightly job.

    For each date, attempts minimum_booking_nights first, then a 2-night fallback
    when minimum_booking_nights==1 and no price is found.

    Returns a list of dicts, one per date:
      {
        "date":         YYYY-MM-DD str
        "live_price":   float | None
        "status":       "available" | "unavailable_or_booked" | "scrape_failed"
        "nights_used":  int | None
        "reused":       bool   (True when D0 job price was reused)
      }
    """
    from datetime import datetime as _dt, timedelta as _td
    from worker.scraper.target_extractor import (
        extract_nightly_price_from_listing_page,
        normalize_airbnb_url,
    )

    listing_url = normalize_airbnb_url(listing_url)
    results: List[Dict[str, Any]] = []

    # D0 reuse — avoid a redundant navigation when the main job already captured it.
    if (
        dates
        and d0_reuse_status == "available"
        and isinstance(d0_reuse_price, (int, float))
        and d0_reuse_price > 0
        and minimum_booking_nights == 1
    ):
        results.append({
            "date": dates[0],
            "live_price": float(d0_reuse_price),
            "status": "available",
            "nights_used": 1,
            "reused": True,
        })
        remaining_dates = dates[1:]
    else:
        remaining_dates = dates

    if not remaining_dates:
        return results

    # Open one shared browser session for all remaining captures.
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=cdp_connect_timeout_ms)
            context = browser.new_context(
                locale="en-US",
                timezone_id="America/Los_Angeles",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )

            for date_str in remaining_dates:
                page = context.new_page()
                try:
                    checkin = date_str
                    checkout_primary = (
                        _dt.strptime(date_str, "%Y-%m-%d") + _td(days=minimum_booking_nights)
                    ).strftime("%Y-%m-%d")

                    price, confidence = extract_nightly_price_from_listing_page(
                        page, listing_url, checkin, checkout_primary
                    )

                    if isinstance(price, (int, float)) and price > 0:
                        results.append({
                            "date": date_str,
                            "live_price": float(price),
                            "status": "available",
                            "nights_used": minimum_booking_nights,
                            "reused": False,
                        })
                    elif confidence == "failed":
                        results.append({
                            "date": date_str,
                            "live_price": None,
                            "status": "scrape_failed",
                            "nights_used": None,
                            "reused": False,
                        })
                    else:
                        # no_price_found: listing is unavailable/booked unless we
                        # can get a price with a 2-night fallback (min_stay=2 listing).
                        if minimum_booking_nights == 1:
                            checkout_2n = (
                                _dt.strptime(date_str, "%Y-%m-%d") + _td(days=2)
                            ).strftime("%Y-%m-%d")
                            price_fb, conf_fb = extract_nightly_price_from_listing_page(
                                page, listing_url, checkin, checkout_2n
                            )
                            if isinstance(price_fb, (int, float)) and price_fb > 0:
                                results.append({
                                    "date": date_str,
                                    "live_price": float(price_fb),
                                    "status": "available",
                                    "nights_used": 2,
                                    "reused": False,
                                })
                            elif conf_fb == "failed":
                                results.append({
                                    "date": date_str,
                                    "live_price": None,
                                    "status": "scrape_failed",
                                    "nights_used": None,
                                    "reused": False,
                                })
                            else:
                                results.append({
                                    "date": date_str,
                                    "live_price": None,
                                    "status": "unavailable_or_booked",
                                    "nights_used": None,
                                    "reused": False,
                                })
                        else:
                            results.append({
                                "date": date_str,
                                "live_price": None,
                                "status": "unavailable_or_booked",
                                "nights_used": None,
                                "reused": False,
                            })

                except Exception as exc:
                    logger.warning(
                        f"[alerts] Live price capture failed for {date_str}: {exc}"
                    )
                    results.append({
                        "date": date_str,
                        "live_price": None,
                        "status": "scrape_failed",
                        "nights_used": None,
                        "reused": False,
                    })
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

            try:
                context.close()
            except Exception:
                pass

    except Exception as exc:
        logger.warning(f"[alerts] Browser session for window capture failed: {exc}")
        # Fill remaining dates as scrape_failed
        captured = {r["date"] for r in results}
        for date_str in remaining_dates:
            if date_str not in captured:
                results.append({
                    "date": date_str,
                    "live_price": None,
                    "status": "scrape_failed",
                    "nights_used": None,
                    "reused": False,
                })

    return results


# ---------------------------------------------------------------------------
# Phase D helpers — Per-night actionability evaluation
# ---------------------------------------------------------------------------


def _evaluate_night_actionability(
    live_price: float,
    recommended_price: float,
    market_price: float,
    threshold_pct: float,
    min_dollar_diff: float,
) -> Tuple[bool, Optional[str], float, float]:
    """
    Determine whether a night is actionable and in which direction.

    Both anchors (vs_recommended and vs_market) must agree and both must
    exceed threshold_pct for an alert to be considered actionable.
    Dollar floor prevents trivial alerts on very cheap listings.

    Returns:
      (is_actionable, direction_or_None, vs_recommended_pct, vs_market_pct)
      direction: "PRICED_HIGH" | "PRICED_LOW" | None
    """
    vs_rec = (live_price / recommended_price - 1.0) * 100.0
    vs_mkt = (live_price / market_price - 1.0) * 100.0
    dollar_diff = abs(live_price - recommended_price)

    if vs_rec > threshold_pct and vs_mkt > threshold_pct:
        direction: Optional[str] = "PRICED_HIGH"
    elif vs_rec < -threshold_pct and vs_mkt < -threshold_pct:
        direction = "PRICED_LOW"
    else:
        return False, None, round(vs_rec, 2), round(vs_mkt, 2)

    if dollar_diff < min_dollar_diff:
        return False, None, round(vs_rec, 2), round(vs_mkt, 2)

    return True, direction, round(vs_rec, 2), round(vs_mkt, 2)


# ---------------------------------------------------------------------------
# Cooldown / dedupe helpers
# ---------------------------------------------------------------------------

_FALLBACK_TZ_NAME: str = "America/Los_Angeles"


def _resolve_tz(listing_timezone: Optional[str]) -> Any:
    """
    Return a ZoneInfo object for the given IANA timezone string.

    Fallback policy (applied in order):
      1. listing_timezone — if non-empty and recognised by zoneinfo
      2. America/Los_Angeles — always used when listing_timezone is missing or invalid

    Never raises: if even the hardcoded fallback somehow fails (broken installation),
    the exception propagates deliberately rather than masking the problem.
    """
    from zoneinfo import ZoneInfo

    tz_name = (listing_timezone or "").strip() or _FALLBACK_TZ_NAME
    try:
        return ZoneInfo(tz_name)
    except Exception:
        # listing_timezone was invalid — fall back deterministically to LA
        return ZoneInfo(_FALLBACK_TZ_NAME)


def _get_local_date(listing_timezone: Optional[str]) -> str:
    """Return today's date string (YYYY-MM-DD) in the listing's local timezone.

    Uses _resolve_tz() so the fallback is always America/Los_Angeles,
    consistent with the suppression check in _should_suppress().
    """
    return datetime.now(_resolve_tz(listing_timezone)).strftime("%Y-%m-%d")


def _should_suppress(
    saved_listing: Dict[str, Any],
    live_price: float,
    direction: str,
) -> Tuple[bool, str]:
    """
    Check alert cooldown / dedupe rules.

    Rules (applied in order):
      1. At most one alert per listing per local-day (uses listing timezone).
      2. 48-hour same-direction cooldown, UNLESS price changed >= $3.
      3. Direction flip resets cooldown immediately (not suppressed).

    Returns (suppressed: bool, reason: str).

    Timezone handling: _resolve_tz() is called once and the resulting object
    is reused for both "today" and "last_alert_sent_at" conversions.
    This guarantees both dates are computed in the same zone — no silent
    fallback divergence, no bare except that drops same-day suppression.
    """
    listing_tz: Optional[str] = saved_listing.get("listing_timezone")
    # Resolve the effective timezone once; reuse for all date comparisons below.
    tz_obj = _resolve_tz(listing_tz)
    local_today = datetime.now(tz_obj).strftime("%Y-%m-%d")

    last_sent_at_raw: Optional[str] = saved_listing.get("last_alert_sent_at")
    last_direction: Optional[str] = saved_listing.get("last_alert_direction")
    last_price_raw = saved_listing.get("last_alert_live_price")

    if not last_sent_at_raw:
        # No prior alert — send freely
        return False, ""

    try:
        sent_dt = datetime.fromisoformat(last_sent_at_raw.replace("Z", "+00:00"))
    except Exception:
        return False, ""

    # Rule 1: one alert per local day.
    # Uses tz_obj already resolved above — no second ZoneInfo construction,
    # no bare except that could silently skip suppression.
    sent_local_date = sent_dt.astimezone(tz_obj).strftime("%Y-%m-%d")
    if sent_local_date == local_today:
        return True, "already_sent_today"

    # Rule 2: same-direction 48-hour cooldown
    if last_direction == direction:
        elapsed = datetime.now(timezone.utc) - sent_dt
        if elapsed < timedelta(hours=COOLDOWN_HOURS):
            # Price change check — break cooldown if price moved >= $3
            if last_price_raw is not None:
                try:
                    if abs(live_price - float(last_price_raw)) >= PRICE_CHANGE_MIN:
                        # Price changed materially — send despite cooldown
                        return False, ""
                except (TypeError, ValueError):
                    pass
            return True, f"cooldown_{direction.lower()}_no_material_change"

    # Direction flipped or cooldown expired — send
    return False, ""


# ---------------------------------------------------------------------------
# Phase F — Email builders
# ---------------------------------------------------------------------------


def _fmt_date(date_str: str) -> str:
    """Format YYYY-MM-DD as 'Mon May 3' for human-readable display (cross-platform)."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        # Avoid platform-specific %-d / %#d zero-stripping; str(d.day) is portable.
        return d.strftime("%a %b ") + str(d.day)
    except Exception:
        return date_str


def _build_alert_range_meta(alertable_nights: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build future-ready range metadata from the set of actionable nights.

    This metadata is logged for observability and intended to support future
    co-host auto-apply workflows that operate over date ranges rather than
    individual nights.  No DB schema changes are needed; this is debug/logging
    metadata only in the current phase.

    Fields:
      firstActionableDate  — earliest date in the alertable set (YYYY-MM-DD)
      lastActionableDate   — latest date in the alertable set (YYYY-MM-DD)
      actionableNightCount — number of sellable actionable nights
      recPriceMin          — minimum recommendedDailyPrice across included nights
      recPriceMax          — maximum recommendedDailyPrice across included nights
      livePriceMin         — minimum live price across included nights
      livePriceMax         — maximum live price across included nights
      nightsContiguous     — True when dates form an unbroken consecutive run
      actionableDates      — sorted list of YYYY-MM-DD strings included in the alert
    """
    if not alertable_nights:
        return {}

    dates = sorted(x["date"] for x in alertable_nights)
    rec_prices = [x["recommended_price"] for x in alertable_nights]
    live_prices = [x["live_price"] for x in alertable_nights]

    contiguous = True
    for i in range(1, len(dates)):
        d1 = datetime.strptime(dates[i - 1], "%Y-%m-%d")
        d2 = datetime.strptime(dates[i], "%Y-%m-%d")
        if (d2 - d1).days != 1:
            contiguous = False
            break

    return {
        "firstActionableDate": dates[0],
        "lastActionableDate": dates[-1],
        "actionableNightCount": len(dates),
        "recPriceMin": round(min(rec_prices), 2),
        "recPriceMax": round(max(rec_prices), 2),
        "livePriceMin": round(min(live_prices), 2),
        "livePriceMax": round(max(live_prices), 2),
        "nightsContiguous": contiguous,
        "actionableDates": dates,
    }


# ---------------------------------------------------------------------------
# Force-send helpers (LOCAL MODE ONLY)
# ---------------------------------------------------------------------------


def _build_force_send_nights(
    start_date: str,
    calendar: Optional[List[Any]],
    summary: Dict[str, Any],
    window_days: int,
) -> List[Dict[str, Any]]:
    """
    Build a synthetic alertable_nights list for force-send local testing.

    Uses real recommendedDailyPrice from the calendar when available,
    with summary-level fallbacks.  Live prices are synthesized at 15% above
    recommendation (PRICED_HIGH scenario) to produce a realistic email preview.

    For D0, uses summary.observedListingPrice when available (the real captured
    price from the main job), giving the most realistic possible D0 data.

    Returns a (possibly empty) list of alertable-night dicts compatible with
    _build_bundled_email() and _build_alert_range_meta().
    """
    window_dates = _build_alert_window_dates(start_date, window_days)
    cal_index = _build_calendar_index(calendar)

    summary_rec_fallback: Optional[float] = None
    _rec_raw = (summary.get("recommendedPrice") or {}).get("nightly")
    if isinstance(_rec_raw, (int, float)) and _rec_raw > 0:
        summary_rec_fallback = float(_rec_raw)

    summary_mkt_fallback: Optional[float] = None
    _mkt_raw = summary.get("nightlyMedian")
    if isinstance(_mkt_raw, (int, float)) and _mkt_raw > 0:
        summary_mkt_fallback = float(_mkt_raw)

    # D0 real observed price (from main job) — most realistic live price for preview
    d0_observed: Optional[float] = None
    _obs_raw = summary.get("observedListingPrice")
    if isinstance(_obs_raw, (int, float)) and _obs_raw > 0:
        d0_observed = float(_obs_raw)

    nights: List[Dict[str, Any]] = []
    for i, date_str in enumerate(window_dates):
        cal_entry = cal_index.get(date_str)
        rec_price, mkt_price = _get_night_prices(
            cal_entry, summary_rec_fallback, summary_mkt_fallback
        )
        if rec_price is None:
            continue

        effective_mkt = mkt_price if mkt_price else rec_price

        # D0: use real observed price when available; others: synthesize at +15%
        if i == 0 and d0_observed is not None:
            live_price = d0_observed
        else:
            live_price = round(rec_price * 1.15)

        vs_rec = round((live_price / rec_price - 1.0) * 100.0, 2)
        vs_mkt = round((live_price / effective_mkt - 1.0) * 100.0, 2)
        dollar_diff = round(abs(live_price - rec_price), 2)
        direction = "PRICED_HIGH" if live_price > rec_price else "PRICED_LOW"

        nights.append({
            "date": date_str,
            "live_price": live_price,
            "recommended_price": rec_price,
            "market_price": effective_mkt,
            "direction": direction,
            "vs_rec_pct": vs_rec,
            "vs_mkt_pct": vs_mkt,
            "dollar_diff": dollar_diff,
            "nights_used": 1,
        })

    # _build_bundled_email requires all nights share one direction — filter to dominant
    if nights:
        high = [n for n in nights if n["direction"] == "PRICED_HIGH"]
        low = [n for n in nights if n["direction"] == "PRICED_LOW"]
        if high and low:
            nights = high if len(high) >= len(low) else low

    return nights


def _run_force_send(
    job: Dict[str, Any],
    summary: Dict[str, Any],
    client: Any,
    calendar: Optional[List[Any]],
    saved_listing: Dict[str, Any],
    listing_id: str,
    report_id: str,
    start_date: str,
    real_alertable_nights: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Execute force-send email delivery for local dev/testing.

    Prefers real alertable nights from the current nightly run when available.
    Falls back to synthetic preview nights (built from calendar/summary) when
    the current run produced no actionable nights.

    preview_source values:
      "real_alertable_nights" — email built from live-scraped, threshold-passing nights
      "synthetic"             — email built from synthesized +15% mock prices

    SAFETY CONSTRAINTS:
    - Active only when WORKER_ENV=local AND ALERT_FORCE_SEND=true.
    - Never updates saved_listings alert state.
    - Writes pricing_alert_log with suppression_reason="force_send_local_mode".
    - Logs at WARNING level so force-send activity is clearly visible.
    """
    logger.warning(
        f"[alerts/{report_id}] *** FORCE-SEND LOCAL MODE ACTIVE ***"
    )

    # ── Choose real nights or fall back to synthetic ─────────────────────
    if real_alertable_nights:
        # Apply the same direction-dominance filter as Phase D4 so
        # _build_bundled_email receives a single-direction list.
        high = [n for n in real_alertable_nights if n["direction"] == "PRICED_HIGH"]
        low = [n for n in real_alertable_nights if n["direction"] == "PRICED_LOW"]
        if high and low:
            high_score = sum(n["dollar_diff"] for n in high)
            low_score = sum(n["dollar_diff"] for n in low)
            force_nights: List[Dict[str, Any]] = high if high_score >= low_score else low
        else:
            force_nights = real_alertable_nights
        preview_source = "real_alertable_nights"
    else:
        force_nights = _build_force_send_nights(
            start_date=start_date,
            calendar=calendar,
            summary=summary,
            window_days=ALERT_WINDOW_DAYS,
        )
        preview_source = "synthetic"

    logger.warning(
        f"[alerts/{report_id}] Force-send preview_source={preview_source} "
        f"nights={len(force_nights)}"
    )

    if not force_nights:
        logger.warning(
            f"[alerts/{report_id}] Force-send: no nights available "
            f"(preview_source={preview_source}, no calendar data or recommended prices) — skipping"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="force_send_no_data",
        )
        return

    # Recipient: ALERT_FORCE_TO_EMAIL override, else listing owner email
    to_email: Optional[str] = ALERT_FORCE_TO_EMAIL or None
    if not to_email:
        user_id: Optional[str] = saved_listing.get("user_id")
        if user_id:
            try:
                user_resp = client.auth.admin.get_user_by_id(user_id)
                to_email = (
                    user_resp.user.email
                    if (user_resp and user_resp.user)
                    else None
                )
            except Exception as exc:
                logger.warning(
                    f"[alerts/{report_id}] Force-send: could not fetch user email: {exc}"
                )

    if not to_email:
        logger.warning(
            f"[alerts/{report_id}] Force-send: no recipient email — "
            f"set ALERT_FORCE_TO_EMAIL or ensure user has an email address"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="force_send_no_recipient",
        )
        return

    share_id = job.get("share_id") or ""
    listing_name = saved_listing.get("name") or "your listing"

    subject, html_body, text_body = _build_bundled_email(
        alertable_nights=force_nights,
        listing_name=listing_name,
        report_share_id=share_id,
        dashboard_url=DASHBOARD_URL,
    )

    subject = f"[FORCE-SEND TEST] {subject}"
    html_body = (
        f'<div style="max-width:540px;margin:0 auto 12px;'
        f'background:#fffbeb;border:1px solid #fcd34d;border-radius:10px;'
        f'padding:12px 16px;font-family:-apple-system,BlinkMacSystemFont,'
        f"\'Segoe UI\',sans-serif;\">"
        f'<p style="margin:0 0 4px;font-size:12px;font-weight:700;'
        f'color:#92400e;text-transform:uppercase;letter-spacing:0.05em;">'
        f'Local Force-Send Test</p>'
        f'<p style="margin:0;font-size:13px;color:#78350f;">'
        f'preview_source={preview_source} &middot; not a production alert'
        f'</p>'
        f'</div>'
        f'{html_body}'
    )
    text_body = (
        f"*** LOCAL FORCE-SEND TEST — NOT A PRODUCTION ALERT ***\n"
        f"preview_source={preview_source}\n\n"
        f"{text_body}"
    )

    primary = max(force_nights, key=lambda x: x["dollar_diff"])
    direction = force_nights[0]["direction"]
    # "force_send_real" or "force_send_synthetic" — distinguishes source in log rows
    log_live_price_status = (
        "force_send_real" if preview_source == "real_alertable_nights" else "force_send_synthetic"
    )

    logger.warning(
        f"[alerts/{report_id}] Force-send: sending to {to_email} | "
        f"preview_source={preview_source} direction={direction} "
        f"nights={len(force_nights)} subject={subject!r}"
    )

    try:
        email_provider_id = _send_via_resend(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except Exception as exc:
        logger.error(f"[alerts/{report_id}] Force-send: email send failed: {exc}")
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=primary["date"],
            suppressed=True,
            suppression_reason="force_send_email_failed",
            alert_direction=direction,
            live_price=primary["live_price"],
            live_price_status=log_live_price_status,
            recommended_price=primary["recommended_price"],
            email_sent_to=to_email,
        )
        return

    # Log to pricing_alert_log — DO NOT update saved_listings state
    _log_evaluation(
        client,
        saved_listing_id=listing_id,
        pricing_report_id=report_id,
        evaluation_date_basis=primary["date"],
        suppressed=False,
        suppression_reason="force_send_local_mode",
        alert_direction=direction,
        live_price=primary["live_price"],
        live_price_status=log_live_price_status,
        market_median=primary["market_price"],
        recommended_price=primary["recommended_price"],
        vs_recommended_pct=primary["vs_rec_pct"],
        vs_market_pct=primary["vs_mkt_pct"],
        email_sent_to=to_email,
        email_provider_id=email_provider_id,
        sent_at=datetime.now(timezone.utc).isoformat(),
        booking_nights_basis=primary.get("nights_used"),
    )

    logger.warning(
        f"[alerts/{report_id}] Force-send complete: preview_source={preview_source} "
        f"resend_id={email_provider_id} to={to_email} "
        f"*** saved_listings state NOT updated ***"
    )


def _build_bundled_email(
    alertable_nights: List[Dict[str, Any]],
    listing_name: str,
    report_share_id: str,
    dashboard_url: str,
) -> Tuple[str, str, str]:
    """
    Build (subject, html_body, text_body) for a bundled near-term pricing alert.

    Email structure (recommendation-first, action-first):
      A. Action headline + sublines (rec range, affected dates)  ← most prominent
      B. Per-night table (date / your price / recommended)
      C. Market reference                                        ← supporting context
      D. Brief explanation (why)
      E. CTA → 30-day pricing plan

    Copy varies by three cases:
      n == 1            — single night: specific date, single rec price
      n > 1, contiguous — date-range wording: "Sat May 3–Mon May 5"
      n > 1, scattered  — count-based wording: "3 upcoming nights" +
                          explicit affected-dates subline

    alertable_nights: list of per-night dicts, each containing:
      date, live_price, recommended_price, market_price, direction,
      vs_rec_pct, vs_mkt_pct, dollar_diff, nights_used

    All nights must share the same direction (caller ensures this).
    """
    assert alertable_nights, "alertable_nights must be non-empty"

    direction = alertable_nights[0]["direction"]
    is_high = direction == "PRICED_HIGH"
    n = len(alertable_nights)

    dates_sorted = sorted(x["date"] for x in alertable_nights)

    # ── Contiguity check ─────────────────────────────────────────────────
    contiguous = True
    for i in range(1, len(dates_sorted)):
        d1 = datetime.strptime(dates_sorted[i - 1], "%Y-%m-%d")
        d2 = datetime.strptime(dates_sorted[i], "%Y-%m-%d")
        if (d2 - d1).days != 1:
            contiguous = False
            break

    # ── Price aggregates ─────────────────────────────────────────────────
    avg_live = round(sum(x["live_price"] for x in alertable_nights) / n)
    avg_mkt = round(sum(x["market_price"] for x in alertable_nights) / n)
    min_rec = round(min(x["recommended_price"] for x in alertable_nights))
    max_rec = round(max(x["recommended_price"] for x in alertable_nights))

    # Primary = highest absolute dollar diff; used for supporting % context
    primary = max(alertable_nights, key=lambda x: x["dollar_diff"])
    vs_mkt_display = abs(round(primary["vs_mkt_pct"], 1))

    # ── Date labels ───────────────────────────────────────────────────────
    if n == 1:
        # Single: exact date
        date_label = _fmt_date(dates_sorted[0])
        header_context = date_label
    elif contiguous:
        # Contiguous multi: date range
        date_label = f"{_fmt_date(dates_sorted[0])}\u2013{_fmt_date(dates_sorted[-1])}"
        header_context = f"{date_label} &middot; {n} nights"
    else:
        # Non-contiguous multi: count-based, no implied range
        date_label = f"{n} upcoming nights"
        header_context = f"{n} selected nights"

    # ── Recommended-price subline ─────────────────────────────────────────
    # Multi-night: show a range when nights differ, single value when uniform.
    if n == 1:
        rec_subline: Optional[str] = None          # headline already states the price
    elif min_rec == max_rec:
        rec_subline = f"Recommended: ${min_rec}/night"
    else:
        rec_subline = f"Recommended range: ${min_rec}\u2013${max_rec}/night"

    # ── Affected-dates subline (non-contiguous only) ──────────────────────
    # ALERT_WINDOW_DAYS = 5 so at most 5 dates; list all.
    if n > 1 and not contiguous:
        affected_dates_str = ", ".join(_fmt_date(d) for d in dates_sorted)
        affected_dates_subline: Optional[str] = f"Affected dates: {affected_dates_str}"
    else:
        affected_dates_subline = None

    # ── Direction-specific copy ───────────────────────────────────────────
    if is_high:
        action_verb = "Lower"
        callout_bg = "#fef2f2"
        callout_border = "#fecaca"
        current_price_color = "#dc2626"
        if n == 1:
            action_headline = f"Lower your price to ${min_rec}/night"
            why_text = (
                f"This night is priced {vs_mkt_display}% above local comparables. "
                f"Lowering your price may improve your booking rate for this date."
            )
        elif contiguous:
            action_headline = f"Lower prices for {date_label}"
            why_text = (
                f"These {n} consecutive nights are priced {vs_mkt_display}% above "
                f"local comparables. Lowering your price for this period may improve "
                f"your booking rate."
            )
        else:
            action_headline = f"Lower prices for {n} upcoming nights"
            why_text = (
                f"These {n} nights are individually priced {vs_mkt_display}% above "
                f"local comparables. Only the dates listed below need changes — "
                f"other nights in your calendar are fine."
            )
    else:
        action_verb = "Raise"
        callout_bg = "#fffbeb"
        callout_border = "#fde68a"
        current_price_color = "#d97706"
        if n == 1:
            action_headline = f"Raise your price to ${min_rec}/night"
            why_text = (
                f"This night is priced {vs_mkt_display}% below local comparables. "
                f"Raising your price could capture additional revenue without "
                f"hurting bookings."
            )
        elif contiguous:
            action_headline = f"Raise prices for {date_label}"
            why_text = (
                f"These {n} consecutive nights are priced {vs_mkt_display}% below "
                f"local comparables. Raising your price for this period could "
                f"capture additional revenue."
            )
        else:
            action_headline = f"Raise prices for {n} upcoming nights"
            why_text = (
                f"These {n} nights are individually priced {vs_mkt_display}% below "
                f"local comparables. Only the dates listed below are underpriced — "
                f"other nights in your calendar are fine."
            )

    # ── Subject line ──────────────────────────────────────────────────────
    # Single:          "Lower your price for Sat May 3 — Beach House"
    # Contiguous:      "Lower prices for Sat May 3–Mon May 5 — Beach House"
    # Non-contiguous:  "Lower prices for 3 upcoming nights — Beach House"
    if n == 1:
        subject = f"{action_verb} your price for {date_label} — {listing_name}"
    else:
        subject = f"{action_verb} prices for {date_label} — {listing_name}"

    report_url = f"{dashboard_url}/r/{report_share_id}" if report_share_id else dashboard_url
    settings_url = f"{dashboard_url}/dashboard"

    # ── Plain text ────────────────────────────────────────────────────────
    night_rows_text = "\n".join(
        f"  {_fmt_date(x['date']):<14}  Current: ${round(x['live_price']):<6}"
        f"  Recommended: ${round(x['recommended_price'])}"
        for x in alertable_nights
    )

    # Sublines block for plain text
    text_sublines = ""
    if affected_dates_subline:
        text_sublines += f"  {affected_dates_subline}\n"
    if rec_subline:
        text_sublines += f"  {rec_subline}\n"

    text_body = f"""{action_headline}

{listing_name} · {date_label}
{text_sublines}
{night_rows_text}

  Your current avg:  ${avg_live}/night
  Market reference:  ${avg_mkt}/night

{why_text}

Review your 30-day pricing plan:
{report_url}

Manage alert settings:
{settings_url}

The Airahost Team

You're receiving this because pricing alerts are enabled for this listing.
To disable: {settings_url}
"""

    # ── Per-night table rows (HTML) ───────────────────────────────────────
    table_rows_html = ""
    for x in alertable_nights:
        table_rows_html += (
            f'<tr style="border-top:1px solid #f3f4f6;">'
            f'<td style="padding:8px 0;color:#374151;font-size:13px;font-weight:500;">'
            f'{_fmt_date(x["date"])}</td>'
            f'<td style="padding:8px 0;text-align:right;font-weight:700;font-size:14px;'
            f'color:{current_price_color};">'
            f'${round(x["live_price"])}'
            f'<span style="font-size:11px;font-weight:400;color:#9ca3af;">/night</span>'
            f'</td>'
            f'<td style="padding:8px 0;text-align:right;font-weight:700;font-size:14px;'
            f'color:#111827;">'
            f'${round(x["recommended_price"])}'
            f'<span style="font-size:11px;font-weight:400;color:#9ca3af;">/night</span>'
            f'</td>'
            f'</tr>'
        )

    # ── Callout sublines (HTML) ───────────────────────────────────────────
    # Affected-dates line (non-contiguous only), then rec-range line (multi only).
    callout_sublines_html = ""
    if affected_dates_subline:
        callout_sublines_html += (
            f'<p style="margin:5px 0 0;font-size:12px;color:#6b7280;">'
            f'{affected_dates_subline}</p>'
        )
    if rec_subline:
        callout_sublines_html += (
            f'<p style="margin:4px 0 0;font-size:13px;font-weight:600;color:#374151;">'
            f'{rec_subline}</p>'
        )

    # ── HTML ──────────────────────────────────────────────────────────────
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Pricing Alert</title>
</head>
<body style="margin:0;padding:20px;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:540px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">

  <!-- Header: listing name + date context -->
  <div style="background:#111827;padding:22px 28px;">
    <p style="margin:0;color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;">Pricing Alert · Airahost</p>
    <h1 style="margin:6px 0 0;color:#fff;font-size:17px;font-weight:700;line-height:1.3;">{listing_name}</h1>
    <p style="margin:4px 0 0;color:#6b7280;font-size:13px;">{header_context}</p>
  </div>

  <!-- A. Action callout: recommendation FIRST, most prominent -->
  <div style="padding:20px 28px;background:{callout_bg};border-bottom:1px solid {callout_border};">
    <p style="margin:0 0 4px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.08em;">Recommended action</p>
    <p style="margin:0 0 2px;font-size:22px;font-weight:800;color:#111827;line-height:1.2;">{action_headline}</p>
    {callout_sublines_html}
    <p style="margin:8px 0 0;font-size:13px;color:#6b7280;">
      Your current avg: <span style="font-weight:600;color:{current_price_color};">${avg_live}/night</span>
    </p>
  </div>

  <!-- B. Per-night breakdown table -->
  <div style="padding:20px 28px 0;">
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr>
          <th style="padding:0 0 8px;text-align:left;font-size:11px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:0.05em;">Date</th>
          <th style="padding:0 0 8px;text-align:right;font-size:11px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:0.05em;">Current Price</th>
          <th style="padding:0 0 8px;text-align:right;font-size:11px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:0.05em;">Recommended</th>
        </tr>
      </thead>
      <tbody>
        {table_rows_html}
      </tbody>
    </table>
  </div>

  <!-- C. Market reference: supporting evidence, not headline -->
  <div style="padding:12px 28px 0;">
    <p style="margin:0;font-size:12px;color:#9ca3af;border-top:1px solid #f3f4f6;padding-top:12px;">
      Market reference: <strong style="color:#6b7280;">${avg_mkt}/night avg</strong>
    </p>
  </div>

  <!-- D. Brief why + E. CTA -->
  <div style="padding:16px 28px 24px;">
    <p style="margin:0 0 20px;font-size:13px;color:#6b7280;line-height:1.5;">{why_text}</p>

    <!--[if mso]>
    <table role="presentation" width="484" cellspacing="0" cellpadding="0" border="0" align="center">
      <tr>
        <td>
          <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml"
                       xmlns:w="urn:schemas-microsoft-com:office:word"
                       href="{report_url}"
                       style="height:46px;v-text-anchor:middle;width:484px;"
                       arcsize="17%"
                       fillcolor="#111827"
                       strokecolor="#111827">
            <w:anchorlock/>
            <center style="color:#ffffff;font-family:Arial,sans-serif;font-size:14px;font-weight:bold;">
              Review your 30-day pricing plan
            </center>
          </v:roundrect>
        </td>
      </tr>
    </table>
    <![endif]-->
    <!--[if !mso]><!-->
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr>
        <td align="center" style="border-radius:8px;background:#111827;">
          <a href="{report_url}"
             target="_blank"
             style="display:inline-block;background:#111827;color:#ffffff;text-decoration:none;
                    text-align:center;padding:13px 24px;border-radius:8px;
                    font-size:14px;font-weight:600;font-family:Arial,sans-serif;
                    width:100%;box-sizing:border-box;mso-padding-alt:13px 24px;">
            Review your 30-day pricing plan
          </a>
        </td>
      </tr>
    </table>
    <!--<![endif]-->

    <p style="margin:12px 0 0;text-align:center;font-size:12px;color:#9ca3af;">
      <a href="{report_url}" style="color:#6b7280;text-decoration:underline;">Open your report</a>
    </p>
  </div>

  <!-- Footer -->
  <div style="padding:14px 28px;border-top:1px solid #f3f4f6;background:#f9fafb;">
    <p style="margin:0;font-size:11px;color:#9ca3af;text-align:center;">
      Pricing alerts are enabled for this listing. &#183;
      <a href="{settings_url}" style="color:#6b7280;text-decoration:underline;">Manage alert settings</a>
    </p>
  </div>

</div>
</body>
</html>"""

    return subject, html_body, text_body


def _send_via_resend(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> Optional[str]:
    """
    Send a transactional email via the Resend API.

    Returns the Resend message ID on success.
    Returns None if RESEND_API_KEY is not configured (dev/test mode).
    Raises on HTTP errors so the caller can handle and avoid updating state.
    """
    if not RESEND_API_KEY:
        logger.info("[alerts] RESEND_API_KEY not set — email send skipped (dev mode)")
        return None

    payload = json.dumps({
        "from": RESEND_FROM,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "airahost-worker/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        response_data = json.loads(resp.read().decode("utf-8"))
        return response_data.get("id")


# ---------------------------------------------------------------------------
# Phase G — DB logging helpers
# ---------------------------------------------------------------------------


def _update_live_price_status(client: Any, listing_id: str, status: str) -> None:
    """Write the most recent alert-pass live price status back to saved_listings."""
    try:
        client.table("saved_listings").update(
            {"last_live_price_status": status}
        ).eq("id", listing_id).execute()
    except Exception as exc:
        logger.warning(f"[alerts] Failed to update last_live_price_status on {listing_id}: {exc}")


def _log_evaluation(
    client: Any,
    *,
    saved_listing_id: str,
    pricing_report_id: str,
    evaluation_date_basis: str,
    suppressed: bool,
    suppression_reason: Optional[str],
    alert_direction: Optional[str] = None,
    live_price: Optional[float] = None,
    live_price_status: Optional[str] = None,
    market_median: Optional[float] = None,
    recommended_price: Optional[float] = None,
    vs_recommended_pct: Optional[float] = None,
    vs_market_pct: Optional[float] = None,
    email_sent_to: Optional[str] = None,
    email_provider_id: Optional[str] = None,
    sent_at: Optional[str] = None,
    booking_nights_basis: Optional[int] = None,
) -> None:
    """Insert one row into pricing_alert_log."""
    try:
        client.table("pricing_alert_log").insert({
            "saved_listing_id": saved_listing_id,
            "pricing_report_id": pricing_report_id,
            "evaluation_date_basis": evaluation_date_basis,
            "suppressed": suppressed,
            "suppression_reason": suppression_reason,
            "alert_direction": alert_direction,
            "live_price": live_price,
            "live_price_status": live_price_status,
            "market_median": market_median,
            "recommended_price": recommended_price,
            "vs_recommended_pct": vs_recommended_pct,
            "vs_market_pct": vs_market_pct,
            "email_sent_to": email_sent_to,
            "email_provider_id": email_provider_id,
            "sent_at": sent_at,
            "booking_nights_basis": booking_nights_basis,
        }).execute()
    except Exception as exc:
        logger.warning(f"[alerts] Failed to write pricing_alert_log row: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_alert_evaluation(
    job: Dict[str, Any],
    summary: Dict[str, Any],
    client: Any,
    listing_url: Optional[str] = None,
    calendar: Optional[List[Any]] = None,
    *,
    cdp_url: str = "http://127.0.0.1:9222",
    cdp_connect_timeout_ms: int = 15000,
) -> None:
    """
    Run pricing alert evaluation for a completed nightly job.

    MUST only be called for job_lane="nightly" jobs (enforced by caller +
    this function's own guard).  Manual and rerun jobs must never trigger
    alert emails.

    ELIGIBILITY INVARIANT: no email is sent unless
    saved_listings.pricing_alerts_enabled == true (checked in Phase C).

    Evaluates a near-term window (D0–D{ALERT_WINDOW_DAYS-1}) from the report
    start date. Only nights that are:
      (a) still sellable (listing is available, not booked/blocked), AND
      (b) materially mispriced vs recommendedDailyPrice + market reference
    are included in the bundled alert email.

    At most one alert email is sent per nightly run.

    Args:
        job         — the claimed pricing_reports row (dict)
        summary     — the completed result_summary dict
        client      — Supabase service-role client
        listing_url — resolved listing URL (already extracted by caller)
        calendar    — list of CalendarDay dicts from the nightly report;
                      used for per-night recommendedDailyPrice lookups.
                      When None, summary-level prices are used for all nights.
        cdp_url     — Playwright CDP endpoint
        cdp_connect_timeout_ms — CDP connection timeout
    """
    report_id = job.get("id", "?")
    start_date = str(job.get("input_date_start", ""))
    listing_id = job.get("listing_id")

    # ── Safety guard — only nightly jobs ─────────────────────────────────
    if job.get("job_lane") != "nightly":
        logger.error(
            f"[alerts/{report_id}] run_alert_evaluation called for non-nightly job "
            f"(lane={job.get('job_lane')!r}) — this is a bug. Aborting."
        )
        return

    if not listing_id:
        return

    if not start_date:
        logger.warning(f"[alerts/{report_id}] No input_date_start — skipping alert evaluation")
        return

    # ── Load saved listing row ────────────────────────────────────────────
    try:
        row_resp = (
            client.table("saved_listings")
            .select(
                "id, name, user_id, pricing_alerts_enabled, listing_timezone, "
                "last_alert_sent_at, last_alert_direction, last_alert_live_price, "
                "minimum_booking_nights"
            )
            .eq("id", listing_id)
            .single()
            .execute()
        )
        saved_listing: Dict[str, Any] = row_resp.data or {}
    except Exception as exc:
        logger.warning(f"[alerts/{report_id}] Could not load saved_listing {listing_id}: {exc}")
        return

    if not saved_listing:
        logger.info(f"[alerts/{report_id}] Saved listing {listing_id} not found — skipping")
        return

    # ── Phase C: Eligibility check ────────────────────────────────────────
    # ALERT ELIGIBILITY INVARIANT: gate on pricing_alerts_enabled FIRST,
    # before any live price capture or processing occurs.
    if not saved_listing.get("pricing_alerts_enabled"):
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="alerts_disabled",
        )
        return

    if not listing_url:
        logger.info(f"[alerts/{report_id}] No listing URL — cannot capture live price")
        _update_live_price_status(client, listing_id, "no_listing_url")
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="no_listing_url",
            live_price_status="no_listing_url",
        )
        return

    minimum_booking_nights: int = int(saved_listing.get("minimum_booking_nights") or 1)

    # ── Phase B: Build alert window and capture live prices ───────────────
    window_dates = _build_alert_window_dates(start_date, ALERT_WINDOW_DAYS)
    cal_index = _build_calendar_index(calendar)

    # Summary-level fallback prices (used when calendar has no entry for a date)
    summary_rec_fallback: Optional[float] = None
    _rec_raw = (summary.get("recommendedPrice") or {}).get("nightly")
    if isinstance(_rec_raw, (int, float)) and _rec_raw > 0:
        summary_rec_fallback = float(_rec_raw)

    summary_mkt_fallback: Optional[float] = None
    _mkt_raw = summary.get("nightlyMedian")
    if isinstance(_mkt_raw, (int, float)) and _mkt_raw > 0:
        summary_mkt_fallback = float(_mkt_raw)

    # D0 reuse: avoid recapturing what the main job already captured.
    existing_status = summary.get("livePriceStatus")
    existing_price = summary.get("observedListingPrice")
    d0_reuse_price: Optional[float] = None
    d0_reuse_status: Optional[str] = None
    if (
        minimum_booking_nights == 1
        and existing_status == "captured"
        and isinstance(existing_price, (int, float))
        and existing_price > 0
    ):
        d0_reuse_price = float(existing_price)
        d0_reuse_status = "available"
        logger.info(
            f"[alerts/{report_id}] D0 live price reused from job: "
            f"${d0_reuse_price} (status=available)"
        )

    logger.info(
        f"[alerts/{report_id}] Alert window: {window_dates[0]}–{window_dates[-1]} "
        f"({ALERT_WINDOW_DAYS} nights) | min_booking_nights={minimum_booking_nights}"
    )

    try:
        window_captures = _capture_window_live_prices(
            listing_url=listing_url,
            dates=window_dates,
            minimum_booking_nights=minimum_booking_nights,
            cdp_url=cdp_url,
            cdp_connect_timeout_ms=cdp_connect_timeout_ms,
            d0_reuse_price=d0_reuse_price,
            d0_reuse_status=d0_reuse_status,
        )
    except Exception as exc:
        logger.warning(f"[alerts/{report_id}] Window live price capture failed: {exc}")
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="window_capture_failed",
            live_price_status="scrape_failed",
        )
        return

    # ── Window capture quality check ─────────────────────────────────────
    # _capture_window_live_prices() absorbs browser session failures internally
    # and returns per-night scrape_failed rather than raising.  Detect whether
    # the entire window effectively failed at the session level (all non-reused
    # nights came back scrape_failed) so suppression reason is precise.
    reused_dates = {window_dates[0]} if d0_reuse_price else set()
    non_reused_captures = [c for c in window_captures if c["date"] not in reused_dates]
    _all_non_reused_failed = bool(
        non_reused_captures
        and all(c["status"] == "scrape_failed" for c in non_reused_captures)
    )
    if _all_non_reused_failed:
        logger.warning(
            f"[alerts/{report_id}] All {len(non_reused_captures)} non-reused window "
            f"captures returned scrape_failed — browser session likely degraded"
        )

    # Update last_live_price_status from D0 (the most representative date).
    d0_capture = next((c for c in window_captures if c["date"] == start_date), None)
    if d0_capture:
        _update_live_price_status(client, listing_id, d0_capture["status"])
        if d0_capture["status"] == "available":
            try:
                client.table("saved_listings").update({
                    "listing_url_validation_status": "valid",
                    "listing_url_validated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", listing_id).execute()
            except Exception as exc:
                logger.warning(
                    f"[alerts/{report_id}] Failed to update listing_url_validation_status: {exc}"
                )

    # ── Phase D: Per-night threshold evaluation + sellability filter ──────
    #
    # For each night in the window:
    #   1. Skip if price data unavailable (scrape_failed → ambiguous, not actionable)
    #   2. Skip if unavailable_or_booked → not a sellable night, excluded from alert
    #   3. Evaluate vs recommendedDailyPrice + market reference
    #   4. Apply threshold + dollar floor
    #   5. Nights passing all checks → alertable
    #
    # This implements the product rule: booked/unavailable nights never trigger alerts.

    alertable_nights: List[Dict[str, Any]] = []
    window_debug: List[Dict[str, Any]] = []

    for capture in window_captures:
        date_str = capture["date"]
        live_price = capture["live_price"]
        live_status = capture["status"]

        cal_entry = cal_index.get(date_str)
        rec_price, mkt_price = _get_night_prices(
            cal_entry, summary_rec_fallback, summary_mkt_fallback
        )

        if live_status == "unavailable_or_booked":
            # Booked/blocked night — not sellable, must not trigger an alert.
            window_debug.append({
                "date": date_str,
                "outcome": "excluded",
                "reason": "unavailable_or_booked",
                "live_price": None,
            })
            continue

        if live_status == "scrape_failed" or live_price is None:
            # Cannot determine availability or price — skip conservatively.
            window_debug.append({
                "date": date_str,
                "outcome": "skipped",
                "reason": "scrape_failed",
                "live_price": None,
            })
            continue

        if rec_price is None:
            window_debug.append({
                "date": date_str,
                "outcome": "skipped",
                "reason": "no_recommended_price",
                "live_price": live_price,
            })
            continue

        # mkt_price falls back to rec_price when market data is absent.
        effective_mkt = mkt_price if mkt_price else rec_price

        is_actionable, night_direction, vs_rec, vs_mkt = _evaluate_night_actionability(
            live_price=live_price,
            recommended_price=rec_price,
            market_price=effective_mkt,
            threshold_pct=ALERT_THRESHOLD_PCT,
            min_dollar_diff=ALERT_MIN_DOLLAR_DIFF,
        )

        debug_entry: Dict[str, Any] = {
            "date": date_str,
            "live_price": live_price,
            "recommended_price": rec_price,
            "market_price": effective_mkt,
            "vs_rec_pct": vs_rec,
            "vs_mkt_pct": vs_mkt,
            "nights_used": capture.get("nights_used"),
        }

        if not is_actionable:
            debug_entry["outcome"] = "below_threshold"
            debug_entry["reason"] = "threshold_not_met_or_dollar_floor"
            window_debug.append(debug_entry)
            continue

        debug_entry["outcome"] = "actionable"
        debug_entry["direction"] = night_direction
        debug_entry["dollar_diff"] = round(abs(live_price - rec_price), 2)
        window_debug.append(debug_entry)

        alertable_nights.append({
            "date": date_str,
            "live_price": live_price,
            "recommended_price": rec_price,
            "market_price": effective_mkt,
            "direction": night_direction,
            "vs_rec_pct": vs_rec,
            "vs_mkt_pct": vs_mkt,
            "dollar_diff": round(abs(live_price - rec_price), 2),
            "nights_used": capture.get("nights_used"),
        })

    # ── Phase D3: Log window summary and check if anything is actionable ──
    available_count = sum(1 for c in window_captures if c["status"] == "available")
    booked_count = sum(
        1 for c in window_captures if c["status"] == "unavailable_or_booked"
    )
    failed_count = sum(1 for c in window_captures if c["status"] == "scrape_failed")

    logger.info(
        f"[alerts/{report_id}] Window summary: {ALERT_WINDOW_DAYS} nights | "
        f"available={available_count} booked/unavailable={booked_count} "
        f"scrape_failed={failed_count} actionable={len(alertable_nights)}"
    )
    # ── Local dev force-send intercept ────────────────────────────────────
    # Fires after live price capture + threshold evaluation so real alertable
    # nights (if any) can be used.  Passes them to _run_force_send; empty list
    # triggers the synthetic fallback inside that function.
    # Bypasses D3 suppression, D4 direction filter, Phase E cooldown, and
    # Phase G state updates.  Never touches saved_listings.
    if ALERT_FORCE_SEND:
        _run_force_send(
            job=job,
            summary=summary,
            client=client,
            calendar=calendar,
            saved_listing=saved_listing,
            listing_id=listing_id,
            report_id=report_id,
            start_date=start_date,
            real_alertable_nights=alertable_nights if alertable_nights else None,
        )
        return

    if not alertable_nights:
        # No sellable actionable nights — no email warranted.
        # Pick the most informative suppression reason given the window state.
        if booked_count == len(window_captures):
            suppression_reason = "all_nights_unavailable"
        elif _all_non_reused_failed and available_count == 0:
            suppression_reason = "window_all_scrape_failed"
        else:
            suppression_reason = "no_actionable_sellable_nights"
        logger.info(
            f"[alerts/{report_id}] Suppressed ({suppression_reason}): "
            f"0 actionable sellable nights | available={available_count} "
            f"booked={booked_count} scrape_failed={failed_count}"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason=suppression_reason,
            live_price_status=d0_capture["status"] if d0_capture else None,
        )
        return

    # ── Phase D4: Mixed-direction handling ────────────────────────────────
    # When the window has both PRICED_HIGH and PRICED_LOW nights, keep only
    # the dominant direction.  A contradictory email would be confusing.
    high_nights = [n for n in alertable_nights if n["direction"] == "PRICED_HIGH"]
    low_nights = [n for n in alertable_nights if n["direction"] == "PRICED_LOW"]

    if high_nights and low_nights:
        # Mixed direction — keep the dominant side (more nights or higher dollar diff)
        high_score = sum(n["dollar_diff"] for n in high_nights)
        low_score = sum(n["dollar_diff"] for n in low_nights)
        alertable_nights = high_nights if high_score >= low_score else low_nights
        logger.info(
            f"[alerts/{report_id}] Mixed-direction window: "
            f"keeping {'PRICED_HIGH' if high_score >= low_score else 'PRICED_LOW'} "
            f"(high_score={high_score:.0f} low_score={low_score:.0f})"
        )

    direction = alertable_nights[0]["direction"]

    # Primary night = highest absolute dollar diff (most urgent for cooldown comparison)
    primary = max(alertable_nights, key=lambda x: x["dollar_diff"])
    primary_live_price = primary["live_price"]

    # ── Range metadata (future-ready, logged for observability) ──────────
    # Intended to support future co-host auto-apply over date ranges.
    # No DB write yet; logged at INFO so it is visible in nightly job logs.
    alert_range_meta = _build_alert_range_meta(alertable_nights)
    logger.info(
        f"[alerts/{report_id}] Alert range meta: {alert_range_meta}"
    )

    # ── Phase E: Cooldown / dedupe check ─────────────────────────────────
    suppressed, suppression_reason = _should_suppress(
        saved_listing, primary_live_price, direction
    )
    if suppressed:
        logger.info(
            f"[alerts/{report_id}] Suppressed ({suppression_reason}): "
            f"{direction} primary_live=${round(primary_live_price)} "
            f"alertable_nights={len(alertable_nights)}"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=primary["date"],
            suppressed=True,
            suppression_reason=suppression_reason,
            alert_direction=direction,
            live_price=primary_live_price,
            live_price_status="available",
            market_median=primary["market_price"],
            recommended_price=primary["recommended_price"],
            vs_recommended_pct=primary["vs_rec_pct"],
            vs_market_pct=primary["vs_mkt_pct"],
            booking_nights_basis=primary.get("nights_used"),
        )
        return

    # ── Phase F: Fetch user email and send bundled alert ─────────────────
    user_id: Optional[str] = saved_listing.get("user_id")
    if not user_id:
        logger.warning(f"[alerts/{report_id}] No user_id on saved_listing — cannot send")
        return

    to_email: Optional[str] = None
    try:
        user_resp = client.auth.admin.get_user_by_id(user_id)
        to_email = user_resp.user.email if (user_resp and user_resp.user) else None
    except Exception as exc:
        logger.warning(f"[alerts/{report_id}] Could not fetch user email for {user_id}: {exc}")

    if not to_email:
        logger.warning(
            f"[alerts/{report_id}] No email address found for user {user_id} — skipping"
        )
        return

    share_id = job.get("share_id") or ""
    listing_name = saved_listing.get("name") or "your listing"

    subject, html_body, text_body = _build_bundled_email(
        alertable_nights=alertable_nights,
        listing_name=listing_name,
        report_share_id=share_id,
        dashboard_url=DASHBOARD_URL,
    )

    try:
        email_provider_id = _send_via_resend(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except Exception as exc:
        logger.error(f"[alerts/{report_id}] Email send failed: {exc}")
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=primary["date"],
            suppressed=True,
            suppression_reason="email_send_failed",
            alert_direction=direction,
            live_price=primary_live_price,
            live_price_status="available",
            market_median=primary["market_price"],
            recommended_price=primary["recommended_price"],
            vs_recommended_pct=primary["vs_rec_pct"],
            vs_market_pct=primary["vs_mkt_pct"],
            email_sent_to=to_email,
            booking_nights_basis=primary.get("nights_used"),
        )
        return

    if email_provider_id is None:
        # No API key configured — dev/test mode, don't update state
        logger.info(
            f"[alerts/{report_id}] Email skipped (no RESEND_API_KEY): "
            f"{direction} alertable_nights={len(alertable_nights)} "
            f"primary_live=${round(primary_live_price)} rec=${round(primary['recommended_price'])}"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=primary["date"],
            suppressed=True,
            suppression_reason="no_resend_api_key",
            alert_direction=direction,
            live_price=primary_live_price,
            live_price_status="available",
            market_median=primary["market_price"],
            recommended_price=primary["recommended_price"],
            vs_recommended_pct=primary["vs_rec_pct"],
            vs_market_pct=primary["vs_mkt_pct"],
            email_sent_to=to_email,
            booking_nights_basis=primary.get("nights_used"),
        )
        return

    # ── Phase G: Update state + log success ──────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        client.table("saved_listings").update({
            "last_alert_sent_at": now_iso,
            "last_alert_direction": direction,
            "last_alert_live_price": round(primary_live_price, 2),
            "last_alert_report_id": report_id,
            "last_live_price_status": "available",
        }).eq("id", listing_id).execute()
    except Exception as exc:
        logger.error(
            f"[alerts/{report_id}] Failed to update alert state on listing {listing_id}: {exc}"
        )

    _log_evaluation(
        client,
        saved_listing_id=listing_id,
        pricing_report_id=report_id,
        evaluation_date_basis=primary["date"],
        suppressed=False,
        suppression_reason=None,
        alert_direction=direction,
        live_price=primary_live_price,
        live_price_status="available",
        market_median=primary["market_price"],
        recommended_price=primary["recommended_price"],
        vs_recommended_pct=primary["vs_rec_pct"],
        vs_market_pct=primary["vs_mkt_pct"],
        email_sent_to=to_email,
        email_provider_id=email_provider_id,
        sent_at=now_iso,
        booking_nights_basis=primary.get("nights_used"),
    )

    alertable_dates = [n["date"] for n in alertable_nights]
    logger.info(
        f"[alerts/{report_id}] Alert sent to {to_email}: {direction} "
        f"alertable_nights={len(alertable_nights)} dates={alertable_dates} "
        f"primary_live=${round(primary_live_price)} rec=${round(primary['recommended_price'])} "
        f"({round(primary['vs_rec_pct'], 1)}%) / mkt=${round(primary['market_price'])} "
        f"({round(primary['vs_mkt_pct'], 1)}%) resend_id={email_provider_id}"
    )
