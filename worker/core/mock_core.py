"""
Hash-based deterministic mock pricing.

This is the fallback when no listing URL is available for scraping.
Produces reproducible summary + calendar based on input attributes,
keeping the product usable while the real scraper is unavailable.

Mirrors the TypeScript pricingCore.ts logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from worker.core.discounts import (
    apply_discount,
    average_refundable_price_for_stay,
    build_stay_length_averages,
)

CORE_VERSION = "mock-v1.0.0"

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _simple_hash(s: str) -> int:
    h = 5381
    for ch in s:
        h = ((h * 33) ^ ord(ch)) & 0xFFFFFFFF
    return h


def _seeded_random(seed: int):
    """Linear congruential generator matching the TS version."""
    s = seed

    def _next() -> float:
        nonlocal s
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        return s / 0x7FFFFFFF

    return _next


def _get_base_multiplier(attrs: Dict[str, Any]) -> float:
    type_mult = {
        "entire_home": 1.0,
        "private_room": 0.55,
        "shared_room": 0.3,
        "hotel_room": 0.7,
    }
    base = type_mult.get(attrs.get("propertyType", "entire_home"), 1.0)
    base += attrs.get("bedrooms", 1) * 0.15
    base += (attrs.get("bathrooms", 1) - 1) * 0.08
    base += max(0, attrs.get("maxGuests", 2) - 2) * 0.03
    amenities = attrs.get("amenities") or []
    base += len(amenities) * 0.02
    return base


def generate_mock_report(
    address: str,
    attributes: Dict[str, Any],
    start_date: str,
    end_date: str,
    discount_policy: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Generate a deterministic pricing report from property attributes.

    Returns (summary, calendar, debug_info).
    """
    seed_str = address + attributes.get("propertyType", "") + str(attributes.get("bedrooms", 1))
    seed = _simple_hash(seed_str)
    rand = _seeded_random(seed)

    multiplier = _get_base_multiplier(attributes)
    base_nightly = round(60 + multiplier * 90 + rand() * 40)

    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    total_days = max(1, (end - start).days)

    # Build base price schedule
    raw_days: List[Dict[str, Any]] = []
    for i in range(total_days):
        d = start + timedelta(days=i)
        dow = d.weekday()  # 0=Mon
        is_weekend = dow >= 4  # Fri=4, Sat=5
        daily_var = round((rand() - 0.5) * 20)
        weekend_boost = round(base_nightly * 0.15) if is_weekend else 0
        base_price = base_nightly + daily_var + weekend_boost

        raw_days.append({
            "date": d.strftime("%Y-%m-%d"),
            "dayOfWeek": DAY_NAMES[dow],
            "isWeekend": is_weekend,
            "basePrice": base_price,
        })

    # Apply discounts
    calendar: List[Dict[str, Any]] = []
    for day in raw_days:
        disc = apply_discount(day["basePrice"], total_days, discount_policy)
        calendar.append({
            **day,
            "refundablePrice": disc["refundablePrice"],
            "nonRefundablePrice": disc["nonRefundablePrice"],
        })

    # Compute summary stats
    base_prices = [d["basePrice"] for d in calendar]
    sorted_prices = sorted(base_prices)
    median = sorted_prices[len(sorted_prices) // 2]
    min_p = sorted_prices[0]
    max_p = sorted_prices[-1]

    weekday_prices = [d["basePrice"] for d in calendar if not d["isWeekend"]]
    weekend_prices = [d["basePrice"] for d in calendar if d["isWeekend"]]

    weekday_avg = round(sum(weekday_prices) / len(weekday_prices)) if weekday_prices else base_nightly
    weekend_avg = round(sum(weekend_prices) / len(weekend_prices)) if weekend_prices else base_nightly

    occupancy_pct = round(55 + rand() * 30)
    selected_range_avg = average_refundable_price_for_stay(
        [d["basePrice"] for d in calendar], total_days, discount_policy
    )
    est_monthly_revenue = round(selected_range_avg * 30 * (occupancy_pct / 100))

    market_median = round(median * (0.9 + rand() * 0.3))
    price_diff = market_median - median
    if price_diff > 5:
        headline = f"You may be underpricing by ~${price_diff} per night."
    elif price_diff < -5:
        headline = f"You're pricing ${abs(price_diff)} above the local median — consider if your amenities justify this."
    else:
        headline = "Your pricing is well-aligned with the local market."

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
        "occupancyPct": occupancy_pct,
        "weekdayAvg": weekday_avg,
        "weekendAvg": weekend_avg,
        "estimatedMonthlyRevenue": est_monthly_revenue,
        "weeklyStayAvgNightly": weekly_avg,
        "monthlyStayAvgNightly": monthly_avg,
        "selectedRangeNights": total_days,
        "selectedRangeAvgNightly": selected_range_avg,
        "stayLengthAverages": stay_length_averages,
    }

    debug = {
        "source": "mock",
        "core_version": CORE_VERSION,
        "base_nightly": base_nightly,
        "total_days": total_days,
        "cache_hit": False,
    }

    return summary, calendar, debug
