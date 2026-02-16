"""
Discount calculation logic.

Mirrors the TypeScript pricingCore.ts `applyDiscount` so that the worker
produces results consistent with the frontend contract.
"""

from __future__ import annotations

from typing import Any, Dict, List


def apply_discount(
    base_price: float,
    stay_length: int,
    policy: Dict[str, Any],
) -> Dict[str, float]:
    """
    Apply length-of-stay and non-refundable discounts according to the policy.

    Returns {"refundablePrice": ..., "nonRefundablePrice": ...}
    """
    weekly_pct = policy.get("weeklyDiscountPct", 0)
    monthly_pct = policy.get("monthlyDiscountPct", 0)
    refundable = policy.get("refundable", True)
    non_ref_pct = policy.get("nonRefundableDiscountPct", 0)
    stacking = policy.get("stackingMode", "compound")
    max_total = policy.get("maxTotalDiscountPct", 40)

    # Determine length-of-stay discount
    length_discount = 0.0
    if stay_length >= 28 and monthly_pct > 0:
        length_discount = monthly_pct / 100
    elif stay_length >= 7 and weekly_pct > 0:
        length_discount = weekly_pct / 100

    non_ref_discount = 0.0 if refundable else non_ref_pct / 100

    # Apply stacking mode
    if stacking == "best_only":
        refundable_discount = length_discount
        non_refundable_discount = max(length_discount, non_ref_discount)
    elif stacking == "additive":
        refundable_discount = length_discount
        non_refundable_discount = min(
            length_discount + non_ref_discount,
            max_total / 100,
        )
    else:  # compound (default)
        refundable_discount = length_discount
        non_refundable_discount = min(
            1 - (1 - length_discount) * (1 - non_ref_discount),
            max_total / 100,
        )

    refundable_discount = min(refundable_discount, max_total / 100)

    return {
        "refundablePrice": round(base_price * (1 - refundable_discount)),
        "nonRefundablePrice": round(base_price * (1 - non_refundable_discount)),
    }


def build_calendar(
    base_prices: List[Dict[str, Any]],
    stay_length: int,
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Given a list of {date, dayOfWeek, isWeekend, basePrice} dicts,
    apply discounts and return the full CalendarDay objects.
    """
    calendar = []
    for day in base_prices:
        discounted = apply_discount(day["basePrice"], stay_length, policy)
        calendar.append({
            "date": day["date"],
            "dayOfWeek": day["dayOfWeek"],
            "isWeekend": day["isWeekend"],
            "basePrice": day["basePrice"],
            "refundablePrice": discounted["refundablePrice"],
            "nonRefundablePrice": discounted["nonRefundablePrice"],
        })
    return calendar


def average_refundable_price_for_stay(
    base_prices: List[float],
    stay_length: int,
    policy: Dict[str, Any],
) -> int:
    """
    Average nightly refundable price for a specific stay length.
    """
    if not base_prices:
        return 0
    prices = [apply_discount(p, stay_length, policy)["refundablePrice"] for p in base_prices]
    return round(sum(prices) / len(prices))


def build_stay_length_averages(
    base_prices: List[float],
    total_days: int,
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Build representative stay-length price points within the selected date range.
    """
    if total_days < 1:
        return []

    points = {1, total_days}
    if total_days >= 7:
        points.add(7)
    if total_days >= 28:
        points.add(28)

    weekly_pct = int(policy.get("weeklyDiscountPct", 0) or 0)
    monthly_pct = int(policy.get("monthlyDiscountPct", 0) or 0)

    out: List[Dict[str, Any]] = []
    for stay_len in sorted(points):
        length_discount_pct = 0
        if stay_len >= 28 and monthly_pct > 0:
            length_discount_pct = monthly_pct
        elif stay_len >= 7 and weekly_pct > 0:
            length_discount_pct = weekly_pct

        out.append({
            "stayLength": stay_len,
            "avgNightly": average_refundable_price_for_stay(base_prices, stay_len, policy),
            "lengthDiscountPct": length_discount_pct,
        })
    return out
