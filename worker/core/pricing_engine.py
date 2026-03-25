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

from worker.core.similarity import SIMILARITY_FLOOR, comp_urls_match, similarity_score
from worker.scraper.target_extractor import ListingSpec

# Type alias: maps id(comp) -> price-sanity weight multiplier (1.0 / 0.5)
PriceSanityWeights = Dict[int, float]

# ── Preferred comp boost constants ───────────────────────────────

_PINNED_MULTIPLIER: float = 2.0   # boost factor applied to similarity score for ranking
_PINNED_MAX_SCORE: float = 0.98   # hard cap to prevent rank distortion


def recommend_price(
    target: ListingSpec,
    comps: List[ListingSpec],
    *,
    top_k: int = 15,
    new_listing_discount: float = 0.10,
    preferred_comp_urls: Optional[List[str]] = None,
    price_sanity_weights: Optional[PriceSanityWeights] = None,
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Pick top-K similar comps and compute a recommended nightly price.

    Formula: similarity-weighted mean across top-K comps above SIMILARITY_FLOOR.
    Each comp contributes proportionally to its raw similarity score, so highly
    similar comps have more influence without any single comp dominating.

    Pinned/preferred comps receive a score boost for ranking only; the pricing
    weights always use raw (unboosted) similarity scores.

    price_sanity_weights (optional): dict keyed by id(comp) → multiplier in
    {1.0, 0.5}.  Excluded comps (weight 0.0) must be stripped from `comps`
    before calling this function; they should not appear in this dict.
    When provided, the pricing weight for each comp becomes:

        effective_weight = raw_similarity_score * price_sanity_weight

    This keeps structural similarity as the primary signal while allowing
    mild price outliers to have reduced influence without full exclusion.
    """
    comps = [c for c in comps if c.nightly_price and c.nightly_price > 0]
    if not comps:
        return None, {"reason": "No comparable prices collected."}

    def _effective_score(c: ListingSpec) -> float:
        base = similarity_score(target, c)
        if preferred_comp_urls and c.url:
            if any(comp_urls_match(c.url, pref) for pref in preferred_comp_urls):
                return min(base * _PINNED_MULTIPLIER, _PINNED_MAX_SCORE)
        return base

    # Rank by effective (possibly boosted) score, then slice top_k.
    ranked = sorted(comps, key=_effective_score, reverse=True)
    picked = ranked[: max(3, top_k)]

    # Apply similarity floor using RAW scores.
    # Boosted scores are for ranking only and must not inflate pricing weights.
    picked_with_scores = [(c, similarity_score(target, c)) for c in picked]
    below_floor = sum(1 for _, s in picked_with_scores if s < SIMILARITY_FLOOR)
    picked_with_scores = [(c, s) for c, s in picked_with_scores if s >= SIMILARITY_FLOOR]

    if not picked_with_scores:
        return None, {
            "reason": "No comps above similarity floor.",
            "picked_n": 0,
            "below_floor": below_floor,
            "low_comp_confidence": False,
        }

    # Build effective weights: raw similarity * price-sanity multiplier.
    # Price-sanity multiplier is 1.0 (full) or 0.5 (downweighted mild outlier).
    # Excluded comps (multiplier 0.0) are already stripped from picked_with_scores
    # by the caller before recommend_price() is invoked.
    effective_weights = []
    for c, s in picked_with_scores:
        ps_mult = price_sanity_weights.get(id(c), 1.0) if price_sanity_weights else 1.0
        effective_weights.append(s * ps_mult)

    prices = [c.nightly_price for c, _ in picked_with_scores]
    total_weight = sum(effective_weights)

    if total_weight <= 0:
        return None, {"reason": "Zero total weight.", "picked_n": 0}

    # Similarity-weighted mean: each comp's price weighted by structural match
    # and optionally scaled by a price-sanity multiplier.
    wm = sum(p * w for p, w in zip(prices, effective_weights)) / total_weight
    rec = wm * (1.0 - max(0.0, min(0.35, new_listing_discount)))

    low_comp_confidence = len(picked_with_scores) <= 2

    debug: Dict[str, Any] = {
        "picked_n": len(picked_with_scores),
        "weighted_mean": round(wm, 2),
        "discount_applied": new_listing_discount,
        "recommended_nightly": round(rec, 2),
        "below_floor": below_floor,
        "low_comp_confidence": low_comp_confidence,
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
            "median": rec_debug.get("weighted_mean"),
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
