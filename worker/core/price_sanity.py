"""
Price Sanity / Outlier Rejection — Layer 1 (V2 Spec)

Per-day check applied after the SIMILARITY_FLOOR gate, before comps enter
the similarity-weighted pricing formula.  Structural similarity and price
sanity are treated as orthogonal dimensions:

  - similarity_score  → structural match (stable, time-invariant)
  - price sanity      → market plausibility (volatile, recomputed per day)

Mixing price into similarity_score is intentionally avoided:  a perfectly
matching comp should not lose structural credit because it ran a promotion.

Algorithm (MAD-based):
  MAD is chosen over IQR because it is more robust at the small sample sizes
  typical in this pipeline (n = 5–15 comps per day).

  normalized_deviation = |price - median| / MAD

  nd <= full_threshold          → full weight  (1.0)
  full_threshold < nd <= excl   → half weight  (0.5, "downweighted")
  nd > excl_threshold           → excluded     (0.0, record as outlier)

Market-dispersion protection:
  If the comp set is genuinely price-dispersed (high CV), tightening the
  thresholds would incorrectly reject legitimate comps.  The thresholds
  widen proportionally with market_cv, and are skipped entirely when the
  market is very dispersed AND the sample is small.

2-night price adjustment:
  Prices from 2-night queries are divided by 2 for per-night comparison but
  may carry a ~5% systematic downward bias from multi-night discounts.
  Before computing the normalized deviation, 2-night prices are scaled up
  by _TWO_NIGHT_ADJ to avoid unfairly flagging them as low-price outliers.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from worker.scraper.target_extractor import ListingSpec

# ── Thresholds ────────────────────────────────────────────────────

# Standard market (market_cv <= 0.5)
_FULL_WEIGHT_ND: float = 2.5
_EXCLUDE_ND: float = 4.0

# Wide market (market_cv > 0.5)
_WIDE_FULL_WEIGHT_ND: float = 3.5
_WIDE_EXCLUDE_ND: float = 5.5

# CV thresholds for market-dispersion detection
_WIDE_CV: float = 0.5        # widen thresholds above this
_VERY_WIDE_CV: float = 0.8   # skip sanity entirely above this (if n < 8)

# Minimum comps required to run outlier detection
_MIN_N: int = 5

# When MAD == 0, fall back to a percentage band around the median
_MAD_ZERO_BAND_PCT: float = 0.40  # ±40% of median treated as band=1.0 unit

# Uplift applied to 2-night per-night prices before deviation calculation
_TWO_NIGHT_ADJ: float = 1.05

# ── Result types ──────────────────────────────────────────────────

# Weight outcomes
WEIGHT_FULL = 1.0
WEIGHT_HALF = 0.5
WEIGHT_EXCLUDED = 0.0

# Outcome labels (for logging / debug)
OUTCOME_FULL = "full"
OUTCOME_DOWNWEIGHTED = "downweighted"
OUTCOME_EXCLUDED = "excluded"
OUTCOME_SKIPPED = "skipped"       # n < _MIN_N or very-wide market
OUTCOME_NO_PRICE = "no_price"     # comp has no nightly_price


@dataclass
class CompSanityResult:
    """Price sanity verdict for a single comparable listing."""

    comp: ListingSpec
    sim_score: float
    weight: float                         # 1.0 / 0.5 / 0.0
    normalized_deviation: Optional[float]  # None when sanity was skipped
    outcome: str                           # one of the OUTCOME_* constants


# ── Public API ────────────────────────────────────────────────────

def apply_price_sanity(
    comps_with_scores: List[Tuple[ListingSpec, float]],
) -> Tuple[List[CompSanityResult], int, int]:
    """
    Apply Layer 1 price sanity to a set of per-day comparable listings.

    Args:
        comps_with_scores: (comp, raw_similarity_score) pairs already filtered
                           by SIMILARITY_FLOOR.

    Returns:
        (results, excluded_count, downweighted_count)

        results: one CompSanityResult per input comp.
        excluded_count: comps with weight == 0.0 (severe outliers).
        downweighted_count: comps with weight == 0.5 (mild outliers).

    Comps with weight == 0.0 should be removed from the pricing pool.
    Comps with weight == 0.5 remain but carry half the influence in the
    similarity-weighted mean.
    """
    n = len(comps_with_scores)

    # ── Not enough comps to judge — accept all at full weight ──────
    if n < _MIN_N:
        return (
            [CompSanityResult(c, s, WEIGHT_FULL, None, OUTCOME_SKIPPED)
             for c, s in comps_with_scores],
            0,
            0,
        )

    prices = [c.nightly_price for c, _ in comps_with_scores if c.nightly_price and c.nightly_price > 0]
    if len(prices) < _MIN_N:
        return (
            [CompSanityResult(c, s, WEIGHT_FULL, None, OUTCOME_SKIPPED)
             for c, s in comps_with_scores],
            0,
            0,
        )

    # ── MAD computation (done first; also used for robust CV) ─────
    # Compute MAD before CV so that CV is based on the same robust
    # statistics — this prevents a single outlier from inflating
    # pstdev/mean CV and triggering the "skip sanity" protection.
    median_price = statistics.median(prices)
    abs_deviations = [abs(p - median_price) for p in prices]
    mad = statistics.median(abs_deviations)

    # ── Market-dispersion check (robust CV = MAD / median) ────────
    # Using MAD/median instead of pstdev/mean so that the outlier
    # itself cannot drive CV above the skip-sanity threshold.
    robust_cv = (mad / median_price) if median_price > 0 else 0.0

    if robust_cv > _VERY_WIDE_CV and n < 8:
        # Genuinely dispersed market + small sample → skip outlier checks
        return (
            [CompSanityResult(c, s, WEIGHT_FULL, None, OUTCOME_SKIPPED)
             for c, s in comps_with_scores],
            0,
            0,
        )

    full_thresh = _WIDE_FULL_WEIGHT_ND if robust_cv > _WIDE_CV else _FULL_WEIGHT_ND
    excl_thresh = _WIDE_EXCLUDE_ND    if robust_cv > _WIDE_CV else _EXCLUDE_ND

    # If MAD is zero, every price is the same (or near-identical).
    # Use a percentage fallback so the band is never trivially narrow.
    mad_zero = mad == 0.0
    band_fallback = median_price * _MAD_ZERO_BAND_PCT  # 1 "unit" = this many dollars

    # ── Per-comp verdict ──────────────────────────────────────────
    results: List[CompSanityResult] = []
    excluded = 0
    downweighted = 0

    for comp, sim_score in comps_with_scores:
        if not comp.nightly_price or comp.nightly_price <= 0:
            results.append(CompSanityResult(comp, sim_score, WEIGHT_EXCLUDED, None, OUTCOME_NO_PRICE))
            excluded += 1
            continue

        # Apply 2-night uplift before deviation check
        check_price = comp.nightly_price
        if getattr(comp, "scrape_nights", 1) == 2:
            check_price *= _TWO_NIGHT_ADJ

        if mad_zero:
            # Band fallback: |price - median| normalised by band_fallback
            nd = abs(check_price - median_price) / max(band_fallback, 1.0)
        else:
            nd = abs(check_price - median_price) / mad

        if nd > excl_thresh:
            results.append(CompSanityResult(comp, sim_score, WEIGHT_EXCLUDED, nd, OUTCOME_EXCLUDED))
            excluded += 1
        elif nd > full_thresh:
            results.append(CompSanityResult(comp, sim_score, WEIGHT_HALF, nd, OUTCOME_DOWNWEIGHTED))
            downweighted += 1
        else:
            results.append(CompSanityResult(comp, sim_score, WEIGHT_FULL, nd, OUTCOME_FULL))

    return results, excluded, downweighted


def build_price_sanity_weights(results: List[CompSanityResult]) -> Dict[int, float]:
    """
    Return a dict keyed by id(comp) → weight for use in recommend_price().

    Only includes comps with weight > 0 (excluded comps are dropped before
    calling recommend_price, so they will not appear in the dict).
    """
    return {id(r.comp): r.weight for r in results if r.weight > 0}
