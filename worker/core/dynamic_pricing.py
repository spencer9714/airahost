from __future__ import annotations

from datetime import date
from statistics import median
from typing import Any, Dict, List, Optional


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except Exception:
        return None


def compute_time_multiplier(today: date, target_date: date) -> float:
    days_away = (target_date - today).days
    if days_away > 30:
        return 1.00
    if days_away > 14:
        return 0.97
    if days_away > 7:
        return 0.92
    if days_away > 3:
        return 0.85
    return 0.75


def compute_demand_adjustment(demand_score: float) -> float:
    adjustment = 1.0 - (0.6 - demand_score) * 0.10
    return round(_clamp(adjustment, 0.90, 1.05), 3)


def compute_market_demand_v2(calendar_days: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    medians: List[Optional[float]] = [
        _to_float((day.get("priceDistribution") or {}).get("median"))
        for day in calendar_days
    ]
    known_medians = [m for m in medians if m is not None]
    global_median = median(known_medians) if known_medians else None

    out: List[Dict[str, Any]] = []
    for idx, day in enumerate(calendar_days):
        day_date: date = day["date"]
        flags = [str(f).lower() for f in (day.get("flags") or [])]
        dist = day.get("priceDistribution") or {}
        reasons: List[str] = []

        day_median = _to_float(dist.get("median"))

        start = max(0, idx - 3)
        end = min(len(calendar_days), idx + 4)
        window_vals = [
            medians[j]
            for j in range(start, end)
            if j != idx and medians[j] is not None
        ]
        baseline = median(window_vals) if window_vals else global_median

        if day_median is not None and baseline is not None and baseline > 0:
            premium_ratio = day_median / baseline
            premium_index = _clamp((premium_ratio - 1.0) / 0.25, -1.0, 1.0)
            premium_delta_pct = round((premium_ratio - 1.0) * 100)
            if abs(premium_delta_pct) >= 5:
                sign = "+" if premium_delta_pct > 0 else ""
                reasons.append(f"Median {sign}{premium_delta_pct}% vs surrounding days")
        else:
            premium_index = 0.0

        p25 = _to_float(dist.get("p25"))
        p75 = _to_float(dist.get("p75"))
        if day_median is not None and p25 is not None and p75 is not None:
            tightness = (p75 - p25) / max(day_median, 1.0)
            tightness_index = _clamp((0.18 - tightness) / 0.18, -1.0, 1.0)
            if tightness_index >= 0.2:
                reasons.append("Tight market spread")
            elif tightness_index <= -0.2:
                reasons.append("Wide market spread")
        else:
            tightness_index = 0.0

        weekday = day_date.weekday()
        if weekday in (4, 5):
            weekend_boost = 0.08
            reasons.append("Weekend boost")
        elif weekday == 6:
            weekend_boost = 0.04
            reasons.append("Weekend boost")
        else:
            weekend_boost = 0.0

        if "peak" in flags or "event" in flags:
            flag_boost = 0.15
            reasons.append("Peak/event signal")
        elif "low_demand" in flags:
            flag_boost = -0.15
            reasons.append("Low-demand signal")
        else:
            flag_boost = 0.0

        demand_score = _clamp(
            0.50
            + 0.20 * premium_index
            + 0.15 * tightness_index
            + weekend_boost
            + flag_boost,
            0.0,
            1.0,
        )

        comps_used = int(day.get("compsUsed") or 0)
        if comps_used >= 25 and day_median is not None:
            confidence = "high"
        elif comps_used >= 12:
            confidence = "medium"
        else:
            confidence = "low"
            reasons.append("Low comps count (confidence low)")

        if not reasons:
            reasons.append("Neutral demand signal")

        out.append(
            {
                "date": day_date,
                "demandScore": round(demand_score, 3),
                "confidence": confidence,
                "reasons": reasons,
            }
        )

    return out


def compute_dynamic_pricing_adjustment(
    today: date,
    calendar_days: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    demand_rows = compute_market_demand_v2(calendar_days)
    demand_by_date = {row["date"]: row for row in demand_rows}

    out: List[Dict[str, Any]] = []
    for day in calendar_days:
        day_date: date = day["date"]
        base_price = _to_float(day.get("baseDailyPrice"))
        flags = list(day.get("flags") or [])
        demand = demand_by_date[day_date]

        time_multiplier = compute_time_multiplier(today, day_date)
        demand_adjustment = compute_demand_adjustment(demand["demandScore"])
        final_multiplier = round(
            _clamp(time_multiplier * demand_adjustment, 0.65, 1.05), 3
        )

        reasons = list(demand["reasons"])
        if time_multiplier < 1.0:
            reasons.insert(0, "Last-minute window")

        if base_price is None:
            if "missing_data" not in flags:
                flags.append("missing_data")
            price_after_time = None
        else:
            price_after_time = round(base_price * final_multiplier)
            if final_multiplier < 1.0 and "last_minute_discount" not in flags:
                flags.append("last_minute_discount")

        out.append(
            {
                "date": day_date,
                "baseDailyPrice": round(base_price) if base_price is not None else None,
                "dynamicAdjustment": {
                    "demandScore": demand["demandScore"],
                    "confidence": demand["confidence"],
                    "timeMultiplier": round(time_multiplier, 3),
                    "demandAdjustment": demand_adjustment,
                    "finalMultiplier": final_multiplier,
                    "reasons": reasons,
                },
                "priceAfterTimeAdjustment": price_after_time,
                "flags": flags,
            }
        )

    return out
