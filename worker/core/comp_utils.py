"""
Shared comparable-listing utilities.

Pure helpers used by both the standard day-query pipeline (day_query.py)
and the benchmark-first pipeline (benchmark.py).  No Playwright, no DB.

Centralising these here eliminates the parallel implementations that
previously lived as inner functions or inline code in each pipeline.

Public API
----------
build_comp_id(url)                    — extract Airbnb room ID from URL
build_comp_prices_dict(comps)         — build room_id → nightly_price map
compute_price_distribution(prices)    — min/max/median/p25/p75 dict
to_comparable_payload(spec, score, …) — unified comparable payload dict
"""

from __future__ import annotations

import re
import statistics
import time
from typing import Any, Dict, List, Optional

from worker.scraper.target_extractor import ListingSpec

_ROOM_ID_RE = re.compile(r"/rooms/(\d+)")


# ---------------------------------------------------------------------------
# ID helper
# ---------------------------------------------------------------------------

def build_comp_id(url: str) -> str:
    """
    Extract the Airbnb room ID from *url* (e.g. '/rooms/12345678' → '12345678').
    Falls back to the raw URL string when no room-ID segment is found, and to a
    millisecond-based sentinel when the URL is blank.

    Callers should treat the sentinel value as a non-deduplicable key.
    """
    m = _ROOM_ID_RE.search(url or "")
    if m:
        return m.group(1)
    return url or f"comp-{int(time.time() * 1000)}"


# ---------------------------------------------------------------------------
# comp_prices dict builder
# ---------------------------------------------------------------------------

def build_comp_prices_dict(comps: List[ListingSpec]) -> Dict[str, float]:
    """
    Build a ``room_id → nightly_price`` map for all priced comps.

    Used to populate ``priceByDate`` for every comp in ``comparableListings``,
    not just the ``top_k`` entries that appear in ``top_comps``.

    Comps with a missing or zero nightly_price are skipped.
    """
    result: Dict[str, float] = {}
    for c in comps:
        cid = build_comp_id(c.url or "")
        if cid and c.nightly_price:
            result[cid] = round(float(c.nightly_price), 2)
    return result


# ---------------------------------------------------------------------------
# Price distribution
# ---------------------------------------------------------------------------

def compute_price_distribution(
    prices: List[float],
    *,
    prepend: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute a ``{min, max, median, p25, p75}`` distribution dict.

    Args:
        prices:  Base price list (market comps, day medians, etc.).
        prepend: Optional single price prepended before statistics are computed
                 (benchmark path uses this to include the anchor price in the
                 distribution without mixing it into the main ``prices`` list).

    Returns:
        Dict with keys ``min``, ``max``, ``median``, ``p25``, ``p75``.
        All values are rounded to 2 decimal places; ``p25``/``p75`` are
        ``None`` when fewer than 4 prices are available.
    """
    all_prices: List[float] = prices.copy()
    if prepend is not None:
        all_prices = [prepend] + all_prices

    dist: Dict[str, Any] = {
        "min": round(min(all_prices), 2) if all_prices else None,
        "max": round(max(all_prices), 2) if all_prices else None,
        "median": round(statistics.median(all_prices), 2) if all_prices else None,
        "p25": None,
        "p75": None,
    }
    if len(all_prices) >= 4:
        q = statistics.quantiles(all_prices, n=4)
        dist["p25"] = round(q[0], 2)
        dist["p75"] = round(q[2], 2)
    return dist


# ---------------------------------------------------------------------------
# Comparable payload builder
# ---------------------------------------------------------------------------

def to_comparable_payload(
    spec: ListingSpec,
    score: float,
    *,
    target: ListingSpec,
    include_geo: bool = False,
) -> Dict[str, Any]:
    """
    Build the standard comparable listing payload dict from a ``ListingSpec``.

    This is the shared base for both pipeline paths:

    * Standard day-query path: ``include_geo=True`` (coordinates available
      after coord extraction in ``collect_search_comps``).
    * Benchmark market-comp path: ``include_geo=False`` (coordinates not
      extracted in the benchmark search).  Callers add ``isPinnedBenchmark``
      as needed — it is NOT included here to preserve exact backward
      compatibility with the day-query path which never emitted that field.

    Args:
        spec:        Parsed comp ``ListingSpec``.
        score:       Similarity score (0–1).
        target:      Target listing spec (used for property-type fallback).
        include_geo: When True, adds ``distanceKm``, ``lat``, ``lng`` if
                     the spec carries coordinate data.

    Returns:
        Payload dict suitable for inclusion in ``comparableListings``.
    """
    def _safe_num(v: Any) -> float:
        return float(v) if isinstance(v, (int, float)) else 0.0

    comp_id = build_comp_id(spec.url or "")

    payload: Dict[str, Any] = {
        "id": comp_id,
        "title": spec.title or "Comparable listing",
        "propertyType": spec.property_type or target.property_type or "entire_home",
        "accommodates": int(spec.accommodates) if isinstance(spec.accommodates, (int, float)) else None,
        "bedrooms": int(spec.bedrooms) if isinstance(spec.bedrooms, (int, float)) else None,
        "baths": round(float(spec.baths), 1) if isinstance(spec.baths, (int, float)) else None,
        "nightlyPrice": round(_safe_num(spec.nightly_price), 2),
        "currency": spec.currency or "USD",
        "similarity": round(float(score), 3),
        "rating": round(float(spec.rating), 2) if isinstance(spec.rating, (int, float)) else None,
        "reviews": int(spec.reviews) if isinstance(spec.reviews, (int, float)) else None,
        "location": spec.location or None,
        "url": spec.url or None,
    }

    # scrape_nights > 1 means this listing's price was a trip total divided per-night
    # (e.g. "for 2 nights" on a 2-night minimum listing). queryNights helps the
    # priceByDate expansion in _build_daily_transparent_result cover both nights.
    if spec.scrape_nights > 1:
        payload["queryNights"] = spec.scrape_nights

    if include_geo:
        if spec.distance_to_target_km is not None:
            payload["distanceKm"] = round(spec.distance_to_target_km, 2)
        if spec.lat is not None and spec.lng is not None:
            payload["lat"] = round(spec.lat, 6)
            payload["lng"] = round(spec.lng, 6)

    return payload
