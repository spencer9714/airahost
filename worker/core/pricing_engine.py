"""
Pricing estimation and transparent result assembly.

Takes scored comparable listings and produces recommended prices
plus a structured transparent output including targetSpec,
queryCriteria, compsSummary, priceDistribution, and debug info.

Extracted from price_estimator.py for modularity.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Tuple

from worker.core.similarity import similarity_score
from worker.scraper.target_extractor import ListingSpec


def _weighted_median(
    values: List[float], weights: List[float]
) -> Optional[float]:
    if not values or not weights or len(values) != len(weights):
        return None
    pairs = sorted(zip(values, weights), key=lambda x: x[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        return None
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= total / 2:
            return v
    return pairs[-1][0]


def recommend_price(
    target: ListingSpec,
    comps: List[ListingSpec],
    *,
    top_k: int = 15,
    new_listing_discount: float = 0.10,
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Pick top-K similar comps and compute a recommended nightly price."""
    comps = [c for c in comps if c.nightly_price and c.nightly_price > 0]
    if not comps:
        return None, {"reason": "No comparable prices collected."}

    ranked = sorted(
        comps, key=lambda c: similarity_score(target, c), reverse=True
    )
    picked = ranked[: max(3, top_k)]

    prices = [c.nightly_price for c in picked if c.nightly_price]
    weights = [
        max(0.05, similarity_score(target, c))
        for c in picked
        if c.nightly_price
    ]

    wm = _weighted_median(prices, weights)
    if wm is None:
        wm = statistics.median(prices) if prices else None
    if wm is None:
        return None, {"reason": "Failed to compute median."}

    rec = wm * (1.0 - max(0.0, min(0.35, new_listing_discount)))

    debug: Dict[str, Any] = {
        "picked_n": len(picked),
        "weighted_median": round(wm, 2),
        "discount_applied": new_listing_discount,
        "recommended_nightly": round(rec, 2),
        "p25": (
            round(statistics.quantiles(prices, n=4)[0], 2)
            if len(prices) >= 4
            else None
        ),
        "p75": (
            round(statistics.quantiles(prices, n=4)[2], 2)
            if len(prices) >= 4
            else None
        ),
        "min": round(min(prices), 2) if prices else None,
        "max": round(max(prices), 2) if prices else None,
    }
    return rec, debug


def build_transparent_result(
    target: ListingSpec,
    query_criteria: Dict[str, Any],
    comps_collected: int,
    comps_filtered: int,
    comps_scored: List[Tuple[ListingSpec, float]],
    rec_price: Optional[float],
    rec_debug: Dict[str, Any],
    timings_ms: Dict[str, int],
    source: str,
    extraction_warnings: List[str],
) -> Dict[str, Any]:
    """
    Assemble the unified transparent result dict.

    This is the canonical output shape consumed by main.py and
    ultimately surfaced to the frontend.
    """
    # Similarity stats from scored comps
    scores = [s for _, s in comps_scored] if comps_scored else []
    top_scores = sorted(scores, reverse=True)[:5]
    top_sim = round(top_scores[0], 3) if top_scores else None
    avg_sim = round(sum(scores) / len(scores), 3) if scores else None

    used_for_pricing = rec_debug.get("picked_n", 0)

    return {
        "targetSpec": {
            "title": target.title or "",
            "location": target.location or "",
            "propertyType": target.property_type or "",
            "accommodates": target.accommodates,
            "bedrooms": target.bedrooms,
            "beds": target.beds,
            "baths": target.baths,
            "amenities": target.amenities or [],
            "rating": target.rating,
            "reviews": target.reviews,
        },
        "queryCriteria": query_criteria,
        "compsSummary": {
            "collected": comps_collected,
            "afterFiltering": comps_filtered,
            "usedForPricing": used_for_pricing,
            "filterStage": query_criteria.get("filterStage", "unknown"),
            "topSimilarity": top_sim,
            "avgSimilarity": avg_sim,
        },
        "priceDistribution": {
            "min": rec_debug.get("min"),
            "p25": rec_debug.get("p25"),
            "median": rec_debug.get("weighted_median"),
            "p75": rec_debug.get("p75"),
            "max": rec_debug.get("max"),
            "currency": "USD",
        },
        "recommendedPrice": {
            "nightly": round(rec_price, 2) if rec_price else None,
            "weekdayEstimate": round(rec_price) if rec_price else None,
            "weekendEstimate": round(rec_price * 1.15) if rec_price else None,
            "discountApplied": rec_debug.get("discount_applied", 0.0),
            "notes": rec_debug.get("reason", ""),
        },
        "debug": {
            "source": source,
            "extractionWarnings": extraction_warnings,
            "timingsMs": timings_ms,
            "similarityScoresSummary": {
                "topScore": top_sim,
                "avgScore": avg_sim,
                "scoreDistribution": [round(s, 3) for s in top_scores],
            },
        },
    }
