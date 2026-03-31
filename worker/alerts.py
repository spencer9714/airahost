"""
worker/alerts.py — Nightly pricing alert evaluation and email delivery.

Entry point: run_alert_evaluation(job, summary, client, listing_url, ...)

SAFETY INVARIANT:
  This module MUST only be called for job_lane="nightly" jobs.
  Manual and rerun jobs must never reach run_alert_evaluation().
  The caller in main.py enforces this guard; this module also asserts it
  as a second layer of protection.

Flow:
  Phase B  — 1-night / 2-night live price capture
  Phase C  — eligibility check (alerts enabled, URL present, valid price)
  Phase D  — alert threshold evaluation (both anchors must agree)
  Phase E  — cooldown / dedupe suppression
  Phase F  — email delivery via Resend
  Phase G  — logging to pricing_alert_log + state update on saved_listings
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("worker.alerts")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Both vs_recommended_pct AND vs_market_pct must exceed this (in absolute %)
# in the same direction for an alert to fire.
ALERT_THRESHOLD_PCT: float = 10.0

# Minimum absolute dollar difference vs recommended price.
# Prevents noisy alerts on low-priced listings where 10% is only a few dollars.
ALERT_MIN_DOLLAR_DIFF: float = 5.0

# Same-direction cooldown: if an alert was sent in the same direction within
# this many hours, suppress unless price changed by >= PRICE_CHANGE_MIN.
COOLDOWN_HOURS: int = 48

# Minimum price change (vs last alert price) to break the cooldown.
PRICE_CHANGE_MIN: float = 3.0

# Resend configuration (set in worker/.env or environment)
RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
RESEND_FROM: str = os.getenv("RESEND_FROM", "alerts@airahost.com")
DASHBOARD_URL: str = os.getenv("NEXT_PUBLIC_APP_URL", "https://airahost.com")


# ---------------------------------------------------------------------------
# Phase B — Live price capture with 1-night / 2-night fallback
# ---------------------------------------------------------------------------


def _capture_alert_live_price(
    listing_url: str,
    start_date: str,
    minimum_booking_nights: int,
    cdp_url: str,
    cdp_connect_timeout_ms: int = 15000,
) -> Tuple[Optional[float], str, Optional[int]]:
    """
    Capture the host's live price for alert evaluation.

    Uses minimum_booking_nights as the primary checkout window
    (checkout = start_date + minimum_booking_nights).

    When minimum_booking_nights == 1 and the listing returns no price, a 2-night
    fallback is attempted in case the listing has a 2-night minimum stay.
    For minimum_booking_nights > 1 there is no fallback; no price means unavailable.

    The Airbnb booking widget always shows the per-night rate regardless of stay
    length, so no division is needed.

    Returns:
      (price_per_night_or_None, status, nights_used)
        status: "available" | "unavailable_or_booked" | "scrape_failed"
        nights_used: the nights value used for the successful capture, or None
    """
    from datetime import datetime as _dt, timedelta as _td
    from worker.scraper.target_extractor import capture_target_live_price

    checkin = start_date
    try:
        checkout_primary = (
            _dt.strptime(start_date, "%Y-%m-%d") + _td(days=minimum_booking_nights)
        ).strftime("%Y-%m-%d")
        checkout_fallback: Optional[str] = (
            (_dt.strptime(start_date, "%Y-%m-%d") + _td(days=2)).strftime("%Y-%m-%d")
            if minimum_booking_nights == 1
            else None
        )
    except Exception as exc:
        logger.warning(
            f"[alerts] Could not compute checkout dates from start_date={start_date}: {exc}"
        )
        return None, "scrape_failed", None

    # ── Primary attempt (minimum_booking_nights nights) ───────────────────
    try:
        result = capture_target_live_price(
            listing_url=listing_url,
            checkin=checkin,
            checkout=checkout_primary,
            cdp_url=cdp_url,
            cdp_connect_timeout_ms=cdp_connect_timeout_ms,
        )
        status = result.get("livePriceStatus")
        price = result.get("observedListingPrice")

        if status == "captured" and isinstance(price, (int, float)) and price > 0:
            return float(price), "available", minimum_booking_nights

        if status == "scrape_failed":
            logger.warning(
                f"[alerts] Primary scrape_failed (nights={minimum_booking_nights}): "
                f"{result.get('livePriceStatusReason', '')}"
            )
            return None, "scrape_failed", None

        # no_price_found — if multi-night primary, listing is likely unavailable
        if checkout_fallback is None:
            return None, "unavailable_or_booked", None

        # minimum_booking_nights == 1 and no price → try 2-night fallback
        logger.info(
            f"[alerts] 1-night no_price_found — trying 2-night fallback for {checkin}"
        )

    except Exception as exc:
        logger.warning(f"[alerts] Primary capture raised exception: {exc}")
        return None, "scrape_failed", None

    # ── 2-night fallback (only when minimum_booking_nights == 1) ─────────
    try:
        result_fb = capture_target_live_price(
            listing_url=listing_url,
            checkin=checkin,
            checkout=checkout_fallback,
            cdp_url=cdp_url,
            cdp_connect_timeout_ms=cdp_connect_timeout_ms,
        )
        status_fb = result_fb.get("livePriceStatus")
        price_fb = result_fb.get("observedListingPrice")

        if status_fb == "captured" and isinstance(price_fb, (int, float)) and price_fb > 0:
            return float(price_fb), "available", 2

        if status_fb == "scrape_failed":
            return None, "scrape_failed", None

        return None, "unavailable_or_booked", None

    except Exception as exc:
        logger.warning(f"[alerts] 2-night fallback raised exception: {exc}")
        return None, "scrape_failed", None


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
# Phase F — Email delivery
# ---------------------------------------------------------------------------


def _build_email(
    listing_name: str,
    date_basis: str,
    live_price: float,
    live_price_status: str,
    market_median: float,
    recommended_price: float,
    direction: str,
    vs_recommended_pct: float,
    report_share_id: str,
    dashboard_url: str,
    booking_nights_basis: Optional[int] = None,
    minimum_booking_nights: int = 1,
) -> Tuple[str, str, str]:
    """
    Build (subject, html_body, text_body) for a pricing alert email.

    direction: "PRICED_HIGH" | "PRICED_LOW"
    booking_nights_basis: nights used for the capture (may differ from minimum_booking_nights
                          when a 2-night fallback was used)
    """
    is_high = direction == "PRICED_HIGH"
    direction_word = "above" if is_high else "below"
    action_verb = "Lowering" if is_high else "Raising"
    pct_abs = abs(round(vs_recommended_pct, 1))
    status_color = "#dc2626" if is_high else "#16a34a"  # red / green

    min_stay_note_text = ""
    min_stay_note_html = ""
    # Show a note when the fallback used more nights than the configured minimum
    if booking_nights_basis is not None and booking_nights_basis > minimum_booking_nights:
        min_stay_note_text = (
            f"\nNote: A {minimum_booking_nights}-night booking was unavailable, "
            f"so this price is based on a {booking_nights_basis}-night booking "
            f"(per-night rate from Airbnb's booking widget). "
            f"Your listing likely has a {booking_nights_basis}-night minimum stay.\n"
        )
        min_stay_note_html = (
            '<p style="margin:0 0 16px;font-size:12px;color:#9ca3af;font-style:italic;">'
            f"Note: {minimum_booking_nights}-night booking unavailable — "
            f"price from a {booking_nights_basis}-night booking (per-night rate). "
            f"Your listing may have a {booking_nights_basis}-night minimum stay.</p>"
        )

    suggestion_text = (
        f"{action_verb} your price to around ${round(recommended_price)}/night "
        f"may {'reduce your competitiveness' if not is_high else 'improve your booking rate'}."
        if is_high
        else f"{action_verb} your price to around ${round(recommended_price)}/night "
        "could increase your earnings."
    )
    # Simpler suggestion
    if is_high:
        suggestion_text = (
            f"Consider lowering your price to around ${round(recommended_price)}/night "
            "to stay competitive."
        )
    else:
        suggestion_text = (
            f"You may be able to earn more by raising your price to around "
            f"${round(recommended_price)}/night."
        )

    subject = (
        f"Your {listing_name} may be priced too high — {date_basis}"
        if is_high
        else f"Your {listing_name} may be leaving money on the table — {date_basis}"
    )

    report_url = f"{dashboard_url}/r/{report_share_id}" if report_share_id else dashboard_url
    settings_url = f"{dashboard_url}/dashboard"

    # ── Plain text ────────────────────────────────────────────────────────
    text_body = f"""Hi,

We checked your listing price for {listing_name} for {date_basis}.

  YOUR LIVE PRICE:  ${round(live_price)}/night
  MARKET MEDIAN:    ${round(market_median)}/night
  RECOMMENDED:      ${round(recommended_price)}/night

You are priced {pct_abs}% {direction_word} our recommendation.

{suggestion_text}
{min_stay_note_text}
View your full market report:
{report_url}

Manage alert settings:
{settings_url}

—
The Airahost Team

You're receiving this because pricing alerts are enabled for this listing.
To disable alerts for this listing, visit your dashboard settings:
{settings_url}
"""

    # ── HTML ──────────────────────────────────────────────────────────────
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Pricing Alert</title>
</head>
<body style="margin:0;padding:20px;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">

  <!-- Header -->
  <div style="background:#111827;padding:24px 28px;">
    <p style="margin:0;color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:0.1em;">Pricing Alert · Airahost</p>
    <h1 style="margin:6px 0 0;color:#fff;font-size:18px;font-weight:700;line-height:1.3;">{listing_name}</h1>
    <p style="margin:4px 0 0;color:#6b7280;font-size:13px;">{date_basis}</p>
  </div>

  <!-- Price table -->
  <div style="padding:24px 28px 0;">
    <table style="width:100%;border-collapse:collapse;">
      <tr>
        <td style="padding:7px 0;color:#374151;font-size:14px;">Your live price</td>
        <td style="padding:7px 0;text-align:right;font-weight:700;font-size:17px;color:{status_color};">
          ${round(live_price)}<span style="font-size:12px;font-weight:400;color:#9ca3af;">/night</span>
        </td>
      </tr>
      <tr style="border-top:1px solid #f3f4f6;">
        <td style="padding:7px 0;color:#374151;font-size:14px;">Market median</td>
        <td style="padding:7px 0;text-align:right;font-weight:600;font-size:14px;color:#374151;">
          ${round(market_median)}<span style="font-size:12px;font-weight:400;color:#9ca3af;">/night</span>
        </td>
      </tr>
      <tr style="border-top:1px solid #f3f4f6;">
        <td style="padding:7px 0;color:#374151;font-size:14px;">Recommended</td>
        <td style="padding:7px 0;text-align:right;font-weight:600;font-size:14px;color:#374151;">
          ${round(recommended_price)}<span style="font-size:12px;font-weight:400;color:#9ca3af;">/night</span>
        </td>
      </tr>
    </table>
  </div>

  <!-- Verdict + suggestion -->
  <div style="padding:16px 28px 24px;">
    <p style="margin:0 0 12px;font-size:15px;color:#111827;">
      You are priced
      <strong style="color:{status_color};">{pct_abs}% {direction_word}</strong>
      our recommendation.
    </p>
    <p style="margin:0 0 20px;font-size:14px;color:#6b7280;">{suggestion_text}</p>
    {min_stay_note_html}
    <a href="{report_url}"
       style="display:block;background:#111827;color:#fff;text-decoration:none;text-align:center;
              padding:13px;border-radius:8px;font-size:14px;font-weight:600;">
      View Full Market Report →
    </a>
  </div>

  <!-- Footer -->
  <div style="padding:14px 28px;border-top:1px solid #f3f4f6;background:#f9fafb;">
    <p style="margin:0;font-size:11px;color:#9ca3af;text-align:center;">
      Pricing alerts are enabled for this listing. ·
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
    *,
    cdp_url: str = "http://127.0.0.1:9222",
    cdp_connect_timeout_ms: int = 15000,
) -> None:
    """
    Run pricing alert evaluation for a completed nightly job.

    MUST only be called for job_lane="nightly" jobs (enforced by caller +
    this function's own guard).  Manual and rerun jobs must never trigger
    alert emails.

    Args:
        job         — the claimed pricing_reports row (dict)
        summary     — the completed result_summary dict
        client      — Supabase service-role client
        listing_url — resolved listing URL (already extracted by caller)
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
        logger.debug(f"[alerts/{report_id}] No listing_id — skipping alert evaluation")
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
    if not saved_listing.get("pricing_alerts_enabled"):
        logger.debug(f"[alerts/{report_id}] pricing_alerts_enabled=false — skipping")
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

    # ── Phase B: Live price capture ───────────────────────────────────────
    minimum_booking_nights: int = int(saved_listing.get("minimum_booking_nights") or 1)
    nights_used: Optional[int] = None

    # Optimisation: reuse the job's own live price capture when it succeeded
    # with the same minimum_booking_nights (1-night job capture = 1-night alert).
    existing_status = summary.get("livePriceStatus")
    existing_price = summary.get("observedListingPrice")
    if (
        minimum_booking_nights == 1
        and existing_status == "captured"
        and isinstance(existing_price, (int, float))
        and existing_price > 0
    ):
        live_price: Optional[float] = float(existing_price)
        live_price_status = "available"
        nights_used = 1
        logger.info(
            f"[alerts/{report_id}] Reusing job live price: "
            f"${live_price} status=available nights_used=1"
        )
    else:
        logger.info(
            f"[alerts/{report_id}] Capturing alert live price "
            f"(minimum_booking_nights={minimum_booking_nights}) for {listing_url}"
        )
        try:
            live_price, live_price_status, nights_used = _capture_alert_live_price(
                listing_url=listing_url,
                start_date=start_date,
                minimum_booking_nights=minimum_booking_nights,
                cdp_url=cdp_url,
                cdp_connect_timeout_ms=cdp_connect_timeout_ms,
            )
        except Exception as exc:
            logger.warning(f"[alerts/{report_id}] Alert price capture exception: {exc}")
            live_price = None
            live_price_status = "scrape_failed"
            nights_used = None

    _update_live_price_status(client, listing_id, live_price_status)

    # listing_url_validation_status lifecycle (worker side):
    #
    #   "available"           → write "valid" + listing_url_validated_at=now()
    #                           The URL resolved to a live Airbnb booking widget with a price.
    #
    #   "unavailable_or_booked" → do NOT change validation status.
    #                           The listing exists and the URL is reachable; the listing is
    #                           simply booked or blocked for the requested dates.
    #
    #   "scrape_failed"       → do NOT change validation status.
    #                           This is a transient Playwright / browser failure, not a signal
    #                           that the URL itself is wrong.
    #
    # The "invalid" status is written only by the API route (route.ts) when the user
    # saves a URL that fails the airbnb.com/rooms/ format check, or clears the URL.
    # The worker never writes "invalid" because it cannot reliably distinguish a bad URL
    # from a transient scrape failure.
    if live_price_status == "available":
        try:
            client.table("saved_listings").update({
                "listing_url_validation_status": "valid",
                "listing_url_validated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", listing_id).execute()
        except Exception as exc:
            logger.warning(
                f"[alerts/{report_id}] Failed to update listing_url_validation_status: {exc}"
            )

    logger.info(
        f"[alerts/{report_id}] Alert live price: "
        f"status={live_price_status} price={live_price} nights_used={nights_used}"
    )

    if live_price is None or live_price_status != "available":
        # No usable price — do not send an alert (not a pricing mismatch)
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason=f"live_price_{live_price_status}",
            live_price_status=live_price_status,
        )
        return

    # ── Phase D: Alert threshold evaluation ───────────────────────────────
    market_median = summary.get("nightlyMedian")
    recommended_price = (summary.get("recommendedPrice") or {}).get("nightly")

    if not isinstance(market_median, (int, float)) or market_median <= 0:
        logger.info(f"[alerts/{report_id}] No valid market median — skipping alert")
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="no_market_median",
            live_price=live_price,
            live_price_status=live_price_status,
            booking_nights_basis=nights_used,
        )
        return

    if not isinstance(recommended_price, (int, float)) or recommended_price <= 0:
        logger.info(f"[alerts/{report_id}] No valid recommended price — skipping alert")
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="no_recommended_price",
            live_price=live_price,
            live_price_status=live_price_status,
            booking_nights_basis=nights_used,
        )
        return

    market_median_f = float(market_median)
    recommended_price_f = float(recommended_price)

    vs_recommended_pct = (live_price / recommended_price_f - 1.0) * 100.0
    vs_market_pct = (live_price / market_median_f - 1.0) * 100.0
    dollar_diff_from_rec = abs(live_price - recommended_price_f)

    # Both anchors must agree in direction, both must exceed the threshold
    if vs_recommended_pct > ALERT_THRESHOLD_PCT and vs_market_pct > ALERT_THRESHOLD_PCT:
        direction = "PRICED_HIGH"
    elif vs_recommended_pct < -ALERT_THRESHOLD_PCT and vs_market_pct < -ALERT_THRESHOLD_PCT:
        direction = "PRICED_LOW"
    else:
        # Anchors disagree or both within threshold — ambiguous, no alert
        logger.info(
            f"[alerts/{report_id}] Threshold not met: "
            f"vs_rec={vs_recommended_pct:.1f}% vs_mkt={vs_market_pct:.1f}%"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="threshold_not_met",
            live_price=live_price,
            live_price_status=live_price_status,
            market_median=market_median_f,
            recommended_price=recommended_price_f,
            vs_recommended_pct=round(vs_recommended_pct, 2),
            vs_market_pct=round(vs_market_pct, 2),
            booking_nights_basis=nights_used,
        )
        return

    # Dollar floor — avoid trivial alerts on cheap listings
    if dollar_diff_from_rec < ALERT_MIN_DOLLAR_DIFF:
        logger.info(
            f"[alerts/{report_id}] Below dollar floor: "
            f"${dollar_diff_from_rec:.2f} < ${ALERT_MIN_DOLLAR_DIFF}"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="below_dollar_floor",
            alert_direction=direction,
            live_price=live_price,
            live_price_status=live_price_status,
            market_median=market_median_f,
            recommended_price=recommended_price_f,
            vs_recommended_pct=round(vs_recommended_pct, 2),
            vs_market_pct=round(vs_market_pct, 2),
            booking_nights_basis=nights_used,
        )
        return

    # ── Phase E: Cooldown / dedupe check ─────────────────────────────────
    suppressed, suppression_reason = _should_suppress(saved_listing, live_price, direction)
    if suppressed:
        logger.info(
            f"[alerts/{report_id}] Suppressed ({suppression_reason}): "
            f"{direction} live=${round(live_price)}"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason=suppression_reason,
            alert_direction=direction,
            live_price=live_price,
            live_price_status=live_price_status,
            market_median=market_median_f,
            recommended_price=recommended_price_f,
            vs_recommended_pct=round(vs_recommended_pct, 2),
            vs_market_pct=round(vs_market_pct, 2),
            booking_nights_basis=nights_used,
        )
        return

    # ── Phase F: Fetch user email and send ───────────────────────────────
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
        logger.warning(f"[alerts/{report_id}] No email address found for user {user_id} — skipping")
        return

    share_id = job.get("share_id") or ""
    listing_name = saved_listing.get("name") or "your listing"

    subject, html_body, text_body = _build_email(
        listing_name=listing_name,
        date_basis=start_date,
        live_price=live_price,
        live_price_status=live_price_status,
        market_median=market_median_f,
        recommended_price=recommended_price_f,
        direction=direction,
        vs_recommended_pct=vs_recommended_pct,
        report_share_id=share_id,
        dashboard_url=DASHBOARD_URL,
        booking_nights_basis=nights_used,
        minimum_booking_nights=minimum_booking_nights,
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
        # Log the failed attempt but do NOT update last_alert_sent_at —
        # so the next run can retry.
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="email_send_failed",
            alert_direction=direction,
            live_price=live_price,
            live_price_status=live_price_status,
            market_median=market_median_f,
            recommended_price=recommended_price_f,
            vs_recommended_pct=round(vs_recommended_pct, 2),
            vs_market_pct=round(vs_market_pct, 2),
            email_sent_to=to_email,
            booking_nights_basis=nights_used,
        )
        return

    if email_provider_id is None:
        # No API key configured — dev/test mode, don't update state
        logger.info(
            f"[alerts/{report_id}] Email skipped (no RESEND_API_KEY): "
            f"{direction} live=${round(live_price)} rec=${round(recommended_price_f)}"
        )
        _log_evaluation(
            client,
            saved_listing_id=listing_id,
            pricing_report_id=report_id,
            evaluation_date_basis=start_date,
            suppressed=True,
            suppression_reason="no_resend_api_key",
            alert_direction=direction,
            live_price=live_price,
            live_price_status=live_price_status,
            market_median=market_median_f,
            recommended_price=recommended_price_f,
            vs_recommended_pct=round(vs_recommended_pct, 2),
            vs_market_pct=round(vs_market_pct, 2),
            email_sent_to=to_email,
            booking_nights_basis=nights_used,
        )
        return

    # ── Phase G: Update state + log success ──────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        client.table("saved_listings").update({
            "last_alert_sent_at": now_iso,
            "last_alert_direction": direction,
            "last_alert_live_price": round(live_price, 2),
            "last_alert_report_id": report_id,
            "last_live_price_status": live_price_status,
        }).eq("id", listing_id).execute()
    except Exception as exc:
        logger.error(
            f"[alerts/{report_id}] Failed to update alert state on listing {listing_id}: {exc}"
        )

    _log_evaluation(
        client,
        saved_listing_id=listing_id,
        pricing_report_id=report_id,
        evaluation_date_basis=start_date,
        suppressed=False,
        suppression_reason=None,
        alert_direction=direction,
        live_price=live_price,
        live_price_status=live_price_status,
        market_median=market_median_f,
        recommended_price=recommended_price_f,
        vs_recommended_pct=round(vs_recommended_pct, 2),
        vs_market_pct=round(vs_market_pct, 2),
        email_sent_to=to_email,
        email_provider_id=email_provider_id,
        sent_at=now_iso,
        booking_nights_basis=nights_used,
    )

    logger.info(
        f"[alerts/{report_id}] Alert sent to {to_email}: {direction} "
        f"live=${round(live_price)} rec=${round(recommended_price_f)} "
        f"({round(vs_recommended_pct, 1)}%) / mkt=${round(market_median_f)} "
        f"({round(vs_market_pct, 1)}%) nights_used={nights_used} "
        f"resend_id={email_provider_id}"
    )
