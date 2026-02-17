"""
Day-by-day 1-night query engine.

WHY 1-NIGHT QUERIES:
Airbnb search cards display *total trip prices* for multi-night stays, not
nightly prices.  For example, a 30-night search shows "$4,500" on a card that
actually costs $150/night.  The previous pipeline treated that total as a
nightly rate, inflating every price by the stay length.

By querying one night at a time (checkin=day_i, checkout=day_i+1), Airbnb
cards show the actual nightly price.  We collect these correct per-night
prices, then apply our own discount policy (weekly/monthly/non-refundable)
on top.
"""

from __future__ import annotations

import logging
import math
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from worker.core.pricing_engine import recommend_price
from worker.core.similarity import filter_similar_candidates, similarity_score
from worker.scraper.comparable_collector import (
    build_search_url,
    parse_card_to_spec,
    scroll_and_collect,
)
from worker.scraper.target_extractor import ListingSpec

logger = logging.getLogger("worker.scraper.day_query")
ROOM_ID_RE = re.compile(r"/rooms/(\d+)")

# ── Configurable constants ───────────────────────────────────────

MAX_NIGHTS = int(os.getenv("MAX_NIGHTS_PER_REPORT", "30"))
SAMPLE_THRESHOLD = int(os.getenv("SAMPLE_THRESHOLD_NIGHTS", "14"))
MAX_SAMPLE_QUERIES = 20
PER_DAY_TIMEOUT_S = 15
PER_DAY_MAX_RETRIES = 2
DAY_SCROLL_ROUNDS = int(os.getenv("DAY_QUERY_SCROLL_ROUNDS", "2"))
DAY_MAX_CARDS = int(os.getenv("DAY_QUERY_MAX_CARDS", "30"))


# ── Data structures ──────────────────────────────────────────────

@dataclass
class DayResult:
    """Result of a single 1-night query (or an interpolated placeholder)."""

    date: str                                        # "YYYY-MM-DD"
    median_price: Optional[float] = None             # weighted median from comps
    comps_collected: int = 0
    comps_used: int = 0
    filter_stage: str = ""
    flags: List[str] = field(default_factory=list)   # peak, low_demand, missing_data, interpolated
    is_sampled: bool = True                          # False if interpolated
    is_weekend: bool = False
    price_distribution: Dict[str, Any] = field(default_factory=dict)
    top_comps: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


# ── Date utilities ───────────────────────────────────────────────

def daterange_nights(start_date: date, end_date: date) -> List[date]:
    """
    Return each night in [start_date, end_date).
    E.g., start=Mar 1, end=Mar 4 => [Mar 1, Mar 2, Mar 3].
    """
    nights: List[date] = []
    current = start_date
    while current < end_date:
        nights.append(current)
        current += timedelta(days=1)
    return nights


def compute_sample_dates(total_nights: int, max_queries: int = MAX_SAMPLE_QUERIES) -> List[int]:
    """
    For ranges > SAMPLE_THRESHOLD, return ~max_queries evenly-spaced indices.
    Always includes index 0 (first night) and total_nights-1 (last night).
    """
    if total_nights <= max_queries:
        return list(range(total_nights))

    step = math.ceil(total_nights / max_queries)
    indices: set[int] = {0, total_nights - 1}
    i = 0
    while i < total_nights:
        indices.add(i)
        i += step
    return sorted(indices)


# ── Single-day query ─────────────────────────────────────────────

def estimate_base_price_for_date(
    page,
    target: ListingSpec,
    base_origin: str,
    date_i: date,
    adults: int,
    *,
    max_scroll_rounds: int = DAY_SCROLL_ROUNDS,
    max_cards: int = DAY_MAX_CARDS,
    rate_limit_seconds: float = 1.0,
    top_k: int = 10,
) -> DayResult:
    """
    Execute a 1-night Airbnb search for date_i -> date_i+1.
    Collect cards, filter by similarity to target, compute price distribution.
    """
    def _safe_num(v: Any, fallback: Optional[float]) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(fallback, (int, float)):
            return float(fallback)
        return 0.0

    def _to_comparable_payload(spec: ListingSpec, score: float) -> Dict[str, Any]:
        room_match = ROOM_ID_RE.search(spec.url or "")
        comp_id = room_match.group(1) if room_match else (spec.url or f"comp-{int(time.time() * 1000)}")
        return {
            "id": comp_id,
            "title": spec.title or "Comparable listing",
            "propertyType": spec.property_type or target.property_type or "entire_home",
            "accommodates": int(_safe_num(spec.accommodates, target.accommodates or 1)),
            "bedrooms": int(_safe_num(spec.bedrooms, target.bedrooms or 1)),
            "baths": round(_safe_num(spec.baths, target.baths or 1), 1),
            "nightlyPrice": round(_safe_num(spec.nightly_price, None), 2),
            "currency": spec.currency or "USD",
            "similarity": round(float(score), 3),
            "rating": round(float(spec.rating), 2) if isinstance(spec.rating, (int, float)) else None,
            "reviews": int(spec.reviews) if isinstance(spec.reviews, (int, float)) else None,
            "location": target.location or None,
            "url": spec.url or None,
        }

    checkin_str = date_i.isoformat()
    next_date = date_i + timedelta(days=1)
    checkout_str = next_date.isoformat()
    is_weekend = date_i.weekday() >= 4  # Fri=4, Sat=5

    search_url = build_search_url(
        base_origin, target.location, checkin_str, checkout_str, adults,
    )
    logger.info(f"[day_query] {checkin_str}: 1-night search")

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=PER_DAY_TIMEOUT_S * 1000)
        page.wait_for_timeout(700)

        # Dismiss modals
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        raw_cards = scroll_and_collect(
            page,
            max_rounds=max_scroll_rounds,
            max_cards=max_cards,
            pause_ms=600,
            rate_limit_seconds=rate_limit_seconds,
        )

        comps = [parse_card_to_spec(c) for c in raw_cards]
        comps = [c for c in comps if c.url and c.nightly_price and c.nightly_price > 0]
        comps_collected = len(comps)

        if comps_collected == 0:
            return DayResult(
                date=checkin_str,
                comps_collected=0,
                filter_stage="empty",
                flags=["missing_data"],
                is_sampled=True,
                is_weekend=is_weekend,
                error="No comps found",
            )

        filtered_comps, filter_debug = filter_similar_candidates(target, comps)

        # Score and rank
        comps_scored = [(c, similarity_score(target, c)) for c in filtered_comps]
        comps_scored.sort(key=lambda x: x[1], reverse=True)

        # No new_listing_discount per-day — discount applied at calendar level
        rec_price, rec_debug = recommend_price(
            target,
            [c for c, _ in comps_scored],
            top_k=top_k,
            new_listing_discount=0.0,
        )

        prices = [c.nightly_price for c, _ in comps_scored if c.nightly_price]
        comps_used = rec_debug.get("picked_n", 0)
        top_comps_scored = comps_scored[: min(max(3, top_k), len(comps_scored))]
        top_comps = [_to_comparable_payload(c, s) for c, s in top_comps_scored]

        # Determine flags
        flags: List[str] = []
        if rec_price is not None and len(prices) >= 3:
            overall_median = statistics.median(prices)
            if rec_price > overall_median * 1.25:
                flags.append("peak")
            elif rec_price < overall_median * 0.75:
                flags.append("low_demand")

        # Price distribution
        dist: Dict[str, Any] = {
            "min": round(min(prices), 2) if prices else None,
            "max": round(max(prices), 2) if prices else None,
            "median": round(statistics.median(prices), 2) if prices else None,
            "p25": None,
            "p75": None,
        }
        if len(prices) >= 4:
            q = statistics.quantiles(prices, n=4)
            dist["p25"] = round(q[0], 2)
            dist["p75"] = round(q[2], 2)

        logger.info(
            f"[day_query] {checkin_str}: comps={comps_collected} filtered={len(filtered_comps)} "
            f"used={comps_used} median=${dist['median']}"
        )

        return DayResult(
            date=checkin_str,
            median_price=round(rec_price, 2) if rec_price else None,
            comps_collected=comps_collected,
            comps_used=comps_used,
            filter_stage=filter_debug.get("stage", "unknown"),
            flags=flags,
            is_sampled=True,
            is_weekend=is_weekend,
            price_distribution=dist,
            top_comps=top_comps,
        )

    except Exception as exc:
        logger.warning(f"[day_query] {checkin_str}: error: {exc}")
        return DayResult(
            date=checkin_str,
            filter_stage="error",
            flags=["missing_data"],
            is_sampled=True,
            is_weekend=is_weekend,
            error=str(exc)[:200],
        )


# ── Discount evidence (debug only) ──────────────────────────────

def detect_discount_evidence(
    page,
    base_origin: str,
    target: ListingSpec,
    start_date: str,
    end_date: str,
    adults: int,
    *,
    rate_limit_seconds: float = 1.0,
) -> Dict[str, Any]:
    """
    Run ONE full-stay query to detect discount signals.
    This is for debug/transparency only — NOT used for pricing.

    The prices extracted here are total-trip prices (the bug we're fixing).
    We divide by nights to show what the "per-night from total" would be.
    """
    try:
        search_url = build_search_url(
            base_origin, target.location, start_date, end_date, adults,
        )

        time.sleep(rate_limit_seconds)
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(700)

        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        raw_cards = scroll_and_collect(
            page, max_rounds=2, max_cards=20, pause_ms=600,
            rate_limit_seconds=rate_limit_seconds,
        )
        comps = [parse_card_to_spec(c) for c in raw_cards]
        prices = [c.nightly_price for c in comps if c.nightly_price and c.nightly_price > 0]

        if not prices:
            return {
                "fullStayPricesRaw": [],
                "fullStayNights": 0,
                "perNightFromTotal": [],
                "avgPerNightFromTotal": None,
                "note": "No prices found in full-stay query",
                "error": None,
            }

        from datetime import datetime as dt
        d_start = dt.strptime(start_date, "%Y-%m-%d").date()
        d_end = dt.strptime(end_date, "%Y-%m-%d").date()
        nights = (d_end - d_start).days
        per_night = [round(p / nights, 2) for p in prices] if nights > 0 else prices

        return {
            "fullStayPricesRaw": prices[:10],
            "fullStayNights": nights,
            "perNightFromTotal": per_night[:10],
            "avgPerNightFromTotal": round(statistics.mean(per_night), 2) if per_night else None,
            "note": "Full-stay query for discount evidence only. These are total-trip prices divided by nights.",
            "error": None,
        }

    except Exception as exc:
        return {
            "fullStayPricesRaw": [],
            "fullStayNights": 0,
            "perNightFromTotal": [],
            "avgPerNightFromTotal": None,
            "note": None,
            "error": str(exc)[:200],
        }


# ── Interpolation ────────────────────────────────────────────────

def interpolate_missing_days(
    sampled_results: List[DayResult],
    all_nights: List[date],
) -> List[DayResult]:
    """
    Fill in unsampled/failed days by linear interpolation between nearest
    valid anchors.  Returns a complete list — one DayResult per night.
    """
    # Map of date-str -> DayResult for sampled days WITH valid prices
    valid_map: Dict[str, DayResult] = {}
    for r in sampled_results:
        if r.median_price is not None:
            valid_map[r.date] = r

    # Map of all sampled (including failed)
    all_sampled: Dict[str, DayResult] = {r.date: r for r in sampled_results}

    # Sorted anchors for interpolation
    anchors: List[Tuple[str, float]] = sorted(
        [(d, r.median_price) for d, r in valid_map.items()],
        key=lambda x: x[0],
    )

    result: List[DayResult] = []

    for night in all_nights:
        ds = night.isoformat()
        is_weekend = night.weekday() >= 4

        # Case 1: sampled with valid price — use as-is
        if ds in valid_map:
            result.append(valid_map[ds])
            continue

        # Case 2: needs interpolation
        interp_price = _interpolate(ds, anchors)

        flags = ["interpolated"]
        if ds in all_sampled:
            flags.append("missing_data")

        if interp_price is not None:
            result.append(DayResult(
                date=ds,
                median_price=round(interp_price, 2),
                filter_stage="interpolated",
                flags=flags,
                is_sampled=False,
                is_weekend=is_weekend,
            ))
        else:
            result.append(DayResult(
                date=ds,
                filter_stage="no_data",
                flags=["missing_data"],
                is_sampled=False,
                is_weekend=is_weekend,
                error="No valid anchors for interpolation",
            ))

    return result


def _interpolate(target_ds: str, anchors: List[Tuple[str, float]]) -> Optional[float]:
    """Linear interpolation between two nearest price anchors."""
    if not anchors:
        return None
    if len(anchors) == 1:
        return anchors[0][1]

    before: Optional[Tuple[str, float]] = None
    after: Optional[Tuple[str, float]] = None

    for ds, price in anchors:
        if ds <= target_ds:
            before = (ds, price)
        if ds >= target_ds and after is None:
            after = (ds, price)

    if before is None and after is not None:
        return after[1]
    if after is None and before is not None:
        return before[1]
    if before is None and after is None:
        return None

    if before[0] == after[0]:
        return before[1]

    b_date = date.fromisoformat(before[0])
    a_date = date.fromisoformat(after[0])
    t_date = date.fromisoformat(target_ds)

    span = (a_date - b_date).days
    offset = (t_date - b_date).days
    ratio = offset / span

    return before[1] + ratio * (after[1] - before[1])
