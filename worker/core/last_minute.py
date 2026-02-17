from __future__ import annotations

from datetime import date
from typing import Optional

from worker.core.dynamic_pricing import (
    compute_demand_adjustment,
    compute_time_multiplier,
)


def compute_last_minute_multiplier(
    checkin_date: date,
    target_date: date,
    occupancy_signal: Optional[float] = None,
) -> float:
    """
    Backward-compatible wrapper over the unified dynamic pricing helpers.

    Uses occupancy_signal as a proxy for demandScore when provided.
    """
    time_multiplier = compute_time_multiplier(checkin_date, target_date)
    demand_score = 0.6 if occupancy_signal is None else float(occupancy_signal)
    demand_adjustment = compute_demand_adjustment(demand_score)
    final_multiplier = time_multiplier * demand_adjustment
    if final_multiplier < 0.65:
        return 0.65
    if final_multiplier > 1.05:
        return 1.05
    return round(final_multiplier, 3)
