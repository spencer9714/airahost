"""
Phase 3B: Adaptive geographic radius selection.

Picks the right search radius for a pricing run based on the existing
comparable pool's observed distances.  Falls back to the default 30 km
when pool data is absent or insufficient.

Rules (in priority order):
  1. No pool data at all           → 30 km (default)
  2. active_pool_size < 5          → 50 km (relax to gather more candidates)
  3. median_dist < 5 km AND size ≥ 10  → 15 km (dense urban market)
  4. otherwise                     → 30 km (default)

The returned radius is a single value for the *current run* only.
The caller is responsible for writing it back to
saved_listings.comp_pool_target_radius_km.
"""

from __future__ import annotations

import logging
import statistics
from typing import List, Optional, Tuple

from worker.core.geo_filter import DEFAULT_MAX_RADIUS_KM

logger = logging.getLogger("worker.core.geo_radius")

# Radius options
RADIUS_TIGHT_KM: float = 15.0          # dense urban markets
RADIUS_DEFAULT_KM: float = DEFAULT_MAX_RADIUS_KM  # 30 km
RADIUS_RELAXED_KM: float = 50.0        # sparse / rural markets

# Thresholds
_TIGHT_MEDIAN_DIST_KM: float = 5.0     # median comp distance for "tight" signal
_TIGHT_MIN_POOL_SIZE: int = 10         # minimum active entries to trust the tight signal
_SPARSE_POOL_SIZE: int = 5             # below this → relax to catch more comps


def select_adaptive_radius(
    pool_distances: Optional[List[Optional[float]]] = None,
    active_pool_size: Optional[int] = None,
) -> Tuple[float, str]:
    """
    Choose the best geographic filter radius for this pricing run.

    Args:
        pool_distances:  distance_to_target_km values from active pool entries.
                         May be None or contain None elements (skipped).
        active_pool_size: total active entries from saved_listings.comp_pool_active_size.
                         If None, falls back to len(pool_distances).

    Returns:
        (radius_km, reason)  where reason is a short human-readable log string.

    Never raises — all edge cases return RADIUS_DEFAULT_KM.
    """
    try:
        return _select(pool_distances, active_pool_size)
    except Exception as exc:
        logger.warning(f"[geo_radius] Error in radius selection (using default): {exc}")
        return RADIUS_DEFAULT_KM, "default (error in selection)"


def _select(
    pool_distances: Optional[List[Optional[float]]],
    active_pool_size: Optional[int],
) -> Tuple[float, str]:
    valid_distances = [
        d for d in (pool_distances or [])
        if d is not None and d >= 0.0
    ]

    # Resolve effective pool size
    if active_pool_size is not None:
        size = int(active_pool_size)
    elif valid_distances:
        size = len(valid_distances)
    else:
        return RADIUS_DEFAULT_KM, "default (no pool data)"

    # Rule 2: sparse pool → relax
    if size < _SPARSE_POOL_SIZE:
        return RADIUS_RELAXED_KM, (
            f"relaxed ({RADIUS_RELAXED_KM:.0f} km): "
            f"pool_size={size} < {_SPARSE_POOL_SIZE}"
        )

    # Rule 3: dense urban signal → tighten
    if valid_distances and size >= _TIGHT_MIN_POOL_SIZE:
        med = statistics.median(valid_distances)
        if med < _TIGHT_MEDIAN_DIST_KM:
            return RADIUS_TIGHT_KM, (
                f"tight ({RADIUS_TIGHT_KM:.0f} km): "
                f"median_dist={med:.1f} km < {_TIGHT_MEDIAN_DIST_KM} km, "
                f"pool_size={size}"
            )

    return RADIUS_DEFAULT_KM, (
        f"default ({RADIUS_DEFAULT_KM:.0f} km): "
        f"pool_size={size}"
    )
