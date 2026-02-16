"""
Similarity scoring and filtering for comparable listings.

Compares candidate listings against a target listing using weighted
feature matching (accommodates, bedrooms, beds, baths, property type,
amenities). Supports multi-tier filtering (strict/medium/fallback).

Extracted from price_estimator.py for modularity.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from worker.scraper.target_extractor import ListingSpec


def similarity_score(target: ListingSpec, cand: ListingSpec) -> float:
    """
    Compute a 0-1 similarity score between target and candidate listings.

    Weights:
      - accommodates: 2.2 (tolerance 3)
      - bedrooms:     2.6 (tolerance 2)
      - beds:         1.4 (tolerance 3)
      - baths:        2.0 (tolerance 1.5)
      - property_type: 1.8 (categorical match)
      - amenities:    1.2 (Jaccard overlap)
    """
    score = 0.0
    weight_sum = 0.0

    def add_num(t, c, w: float, tol: float):
        nonlocal score, weight_sum
        weight_sum += w
        if t is None or c is None:
            score += 0.35 * w
            return
        diff = abs(float(t) - float(c))
        s = max(0.0, 1.0 - diff / tol)
        score += s * w

    add_num(target.accommodates, cand.accommodates, w=2.2, tol=3.0)
    add_num(target.bedrooms, cand.bedrooms, w=2.6, tol=2.0)
    add_num(target.beds, cand.beds, w=1.4, tol=3.0)
    add_num(target.baths, cand.baths, w=2.0, tol=1.5)

    # Property-type match is a strong categorical signal.
    weight_sum += 1.8
    if target.property_type and cand.property_type:
        score += (1.0 if target.property_type == cand.property_type else 0.15) * 1.8
    else:
        score += 0.35 * 1.8

    # Amenity overlap provides a softer similarity signal.
    weight_sum += 1.2
    t_set = set(target.amenities or [])
    c_set = set(cand.amenities or [])
    if t_set and c_set:
        overlap = len(t_set & c_set) / max(1, len(t_set | c_set))
        score += overlap * 1.2
    else:
        score += 0.35 * 1.2

    if weight_sum <= 0:
        return 0.0
    return score / weight_sum


def _within_tolerance(
    target_val: Optional[float],
    cand_val: Optional[float],
    tol: float,
) -> bool:
    if target_val is None or cand_val is None:
        return True
    return abs(float(target_val) - float(cand_val)) <= tol


def filter_similar_candidates(
    target: ListingSpec,
    candidates: List[ListingSpec],
) -> Tuple[List[ListingSpec], Dict[str, Any]]:
    """
    Keep candidates structurally similar to the target listing.

    Uses a multi-tier approach:
      1) Strict: property_type match, tight numeric tolerances
      2) Medium: property_type match, relaxed tolerances
      3) Fallback: all candidates

    Returns (filtered_list, filter_metadata).
    """
    total = len(candidates)
    if total == 0:
        return [], {"stage": "empty", "total_candidates": 0, "filtered_candidates": 0}

    # Tier 1: Strict
    strict = candidates
    if target.property_type:
        strict = [c for c in strict if not c.property_type or c.property_type == target.property_type]
    strict = [
        c for c in strict
        if _within_tolerance(target.accommodates, c.accommodates, 2)
        and _within_tolerance(target.bedrooms, c.bedrooms, 1)
        and _within_tolerance(target.beds, c.beds, 2)
        and _within_tolerance(target.baths, c.baths, 1)
    ]

    if len(strict) >= 8:
        return strict, {
            "stage": "strict",
            "total_candidates": total,
            "filtered_candidates": len(strict),
        }

    # Tier 2: Medium
    medium = candidates
    if target.property_type:
        medium = [c for c in medium if not c.property_type or c.property_type == target.property_type]
    medium = [
        c for c in medium
        if _within_tolerance(target.accommodates, c.accommodates, 3)
        and _within_tolerance(target.bedrooms, c.bedrooms, 2)
        and _within_tolerance(target.baths, c.baths, 1.5)
    ]

    if len(medium) >= 5:
        return medium, {
            "stage": "medium",
            "total_candidates": total,
            "filtered_candidates": len(medium),
        }

    # Tier 3: Fallback — use all candidates
    return candidates, {
        "stage": "fallback_all",
        "total_candidates": total,
        "filtered_candidates": total,
    }
