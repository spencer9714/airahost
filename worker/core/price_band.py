"""
Phase 3B: Price-band-aware comparable filtering.

Comps whose nightly price falls outside the anchor band are excluded from
both pricing and the displayed comparable listings.

Anchor selection (in priority order):
  1. Explicit anchor_price (caller-supplied — benchmark price or preferred comp price)
  2. Majority band derived from the comp pool itself (IQR-based, n ≥ 4 required)
  3. No anchor → no filtering (all comps pass through)

Band definition:
  - Anchor known:    [anchor × (1 - lower_pct), anchor × (1 + upper_pct)]
                     Default ±30%: [anchor × 0.70, anchor × 1.30]
  - Majority band:   [Q1 × 0.70, Q3 × 1.30]  (IQR expanded by the same margins)
  - No anchor/data:  no filter applied
"""

from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List, Optional, Tuple

from worker.scraper.target_extractor import ListingSpec

logger = logging.getLogger("worker.core.price_band")

# Default ±30% band around the anchor price
PRICE_BAND_LOWER_PCT: float = 0.30
PRICE_BAND_UPPER_PCT: float = 0.30

# Minimum comps required to derive a majority band from IQR
_MAJORITY_BAND_MIN_COMPS: int = 4


def make_anchor_band(
    anchor_price: float,
    lower_pct: float = PRICE_BAND_LOWER_PCT,
    upper_pct: float = PRICE_BAND_UPPER_PCT,
) -> Tuple[float, float]:
    """Return (lower, upper) price band around a known anchor price."""
    return anchor_price * (1.0 - lower_pct), anchor_price * (1.0 + upper_pct)


def find_majority_band(prices: List[float]) -> Optional[Tuple[float, float]]:
    """
    Derive a price band from comp prices using the interquartile range.

    Returns (Q1 × 0.70, Q3 × 1.30) when n ≥ 4, otherwise None.
    Non-positive prices are ignored.
    """
    valid = [p for p in prices if p > 0]
    if len(valid) < _MAJORITY_BAND_MIN_COMPS:
        return None
    q = statistics.quantiles(valid, n=4)
    q1, q3 = q[0], q[2]
    lower = q1 * (1.0 - PRICE_BAND_LOWER_PCT)
    upper = q3 * (1.0 + PRICE_BAND_UPPER_PCT)
    return lower, upper


def apply_price_band_filter(
    comps_with_scores: List[Tuple[ListingSpec, float]],
    anchor_price: Optional[float] = None,
) -> Tuple[
    List[Tuple[ListingSpec, float]],
    List[Tuple[ListingSpec, float]],
    Dict[str, Any],
]:
    """
    Split comps into (in_band, out_of_band) based on a price band.

    Comps without a nightly price always pass through (cannot evaluate band).

    Args:
        comps_with_scores: List of (ListingSpec, similarity_score) tuples.
        anchor_price:      Known anchor (benchmark price or preferred comp price).
                           If None, the band is derived from comp prices (majority band).

    Returns:
        in_band:      Comps within the price band → proceed to pricing + display.
        out_of_band:  Comps outside the band → excluded entirely.
        band_info:    Dict with keys: lower, upper, anchor_mode, anchor_price.
                      anchor_mode: "anchor" | "majority" | "none"
    """
    _no_filter = {"anchor_mode": "none", "anchor_price": anchor_price, "lower": None, "upper": None}

    if not comps_with_scores:
        return [], [], _no_filter

    # Determine the band
    band: Optional[Tuple[float, float]] = None
    anchor_mode = "none"

    if anchor_price is not None and anchor_price > 0:
        band = make_anchor_band(anchor_price)
        anchor_mode = "anchor"
    else:
        prices = [
            c.nightly_price
            for c, _ in comps_with_scores
            if c.nightly_price and c.nightly_price > 0
        ]
        band = find_majority_band(prices)
        if band is not None:
            anchor_mode = "majority"

    if band is None:
        return list(comps_with_scores), [], _no_filter

    lower, upper = band

    in_band: List[Tuple[ListingSpec, float]] = []
    out_of_band: List[Tuple[ListingSpec, float]] = []

    for comp, score in comps_with_scores:
        price = comp.nightly_price
        if price is None or price <= 0:
            # No price → pass through (cannot evaluate)
            in_band.append((comp, score))
        elif lower <= price <= upper:
            in_band.append((comp, score))
        else:
            out_of_band.append((comp, score))

    return in_band, out_of_band, {
        "anchor_mode": anchor_mode,
        "anchor_price": anchor_price,
        "lower": round(lower, 2),
        "upper": round(upper, 2),
    }
