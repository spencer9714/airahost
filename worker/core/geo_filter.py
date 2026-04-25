"""
Geographic distance filter — Phase 3A (V2 Spec).

Computes haversine distance between two lat/lng points and filters
comparable candidates that are beyond max_radius_km from the target.

Design rules:
  - Comps WITHOUT coordinates always pass through — we never reject a comp
    solely because we couldn't determine its location.
  - If the target has no coordinates, the filter is a complete no-op.
  - Distance (km) is stored on the comp's ListingSpec for downstream use
    (pool seeding, display).

Max-radius defaults
  Phase 3A uses a single conservative default of 30 km.
  This is deliberately permissive — urban markets will typically have
  many comps well within 10 km, and 30 km prevents false exclusions
  in mid-density or resort markets.  Phase 3B can add dynamic radius.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("worker.core.geo_filter")

# Single hardcoded default for Phase 3A.
# Empirically safe for urban, suburban, and resort markets.
DEFAULT_MAX_RADIUS_KM: float = 30.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Haversine great-circle distance between two WGS-84 points, in km.

    Accurate to ~0.3% for typical distances; more than sufficient for
    the approximate coordinates extracted from Airbnb search pages.
    """
    R = 6371.0  # Earth mean radius in km
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))  # clamp to avoid domain errors


def apply_geo_filter(
    comps,  # List[ListingSpec] — avoids circular import; typed structurally
    target_lat: float,
    target_lng: float,
    max_radius_km: float = DEFAULT_MAX_RADIUS_KM,
) -> Tuple[list, int]:
    """
    Filter comps beyond max_radius_km from the target.

    Comps without coordinates (lat == None or lng == None) are always
    retained — missing coordinates are treated as "location unknown",
    not "too far".

    Side-effect: sets `comp.distance_to_target_km` on every comp that
    HAS coordinates, whether retained or excluded.

    Returns:
        (retained_comps, geo_excluded_count)
    """
    retained = []
    excluded = 0

    for comp in comps:
        comp_lat = getattr(comp, "lat", None)
        comp_lng = getattr(comp, "lng", None)

        if comp_lat is None or comp_lng is None:
            # No coords — pass through without filtering
            retained.append(comp)
            continue

        dist = haversine_km(target_lat, target_lng, comp_lat, comp_lng)
        comp.distance_to_target_km = dist

        if dist <= max_radius_km:
            retained.append(comp)
        else:
            excluded += 1

    if excluded:
        logger.info(
            f"[geo_filter] Distance filter: {excluded} comps excluded "
            f"(>{max_radius_km} km), {len(retained)} retained"
        )

    return retained, excluded
