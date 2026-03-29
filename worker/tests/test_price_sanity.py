"""
Tests for worker.core.price_sanity — Layer 1 price outlier detection.

Covers:
  - Normal market: correct full / downweighted / excluded classification
  - Small sample (n < 5): all comps pass at full weight
  - MAD-zero fallback: % band used when all prices are identical
  - Wide-market protection (CV > 0.5): thresholds widen
  - Very-wide + small sample (CV > 0.8 and n < 8): sanity skipped entirely
  - 2-night price uplift: 2-night comps get +5% before deviation check
  - build_price_sanity_weights: correct dict construction
  - pricing_engine integration: downweighted comp has reduced influence
"""

from __future__ import annotations

from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from worker.core.price_sanity import (
    OUTCOME_DOWNWEIGHTED,
    OUTCOME_EXCLUDED,
    OUTCOME_FULL,
    OUTCOME_NO_PRICE,
    OUTCOME_SKIPPED,
    WEIGHT_EXCLUDED,
    WEIGHT_FULL,
    WEIGHT_HALF,
    apply_price_sanity,
    build_price_sanity_weights,
)
from worker.scraper.target_extractor import ListingSpec


# ── Helpers ───────────────────────────────────────────────────────

def _spec(price: Optional[float], scrape_nights: int = 1) -> ListingSpec:
    """Minimal ListingSpec with only the fields price_sanity cares about."""
    s = MagicMock(spec=ListingSpec)
    s.nightly_price = price
    s.scrape_nights = scrape_nights
    # Fields required by similarity_score (not used here but present on spec)
    s.property_type = "entire_home"
    s.bedrooms = 2
    s.baths = 1.0
    s.accommodates = 4
    s.beds = 2
    s.amenities = []
    s.url = None
    return s


def _pool(prices: List[Optional[float]], scrape_nights: int = 1):
    """Build (comp, sim_score=0.7) pairs from a price list."""
    return [(_spec(p, scrape_nights), 0.7) for p in prices]


# ── Small-sample bypass ───────────────────────────────────────────

def test_small_sample_skips_sanity():
    """n < 5 → all comps accepted at full weight, no outlier checks."""
    pool = _pool([100, 200, 5000, 1])  # n=4, would be outliers with n>=5
    results, excl, down = apply_price_sanity(pool)

    assert excl == 0
    assert down == 0
    assert all(r.weight == WEIGHT_FULL for r in results)
    assert all(r.outcome == OUTCOME_SKIPPED for r in results)


# ── Normal market ─────────────────────────────────────────────────

def test_normal_market_all_full_weight():
    """Moderate price spread → all comps accepted at full weight.

    [80, 90, 100, 110, 120, 108]: median=104, MAD≈10.
    Max nd ≈ |80-104|/10 = 2.4, comfortably below the 2.5 threshold.
    """
    prices = [80, 90, 100, 110, 120, 108]
    results, excl, down = apply_price_sanity(_pool(prices))

    assert excl == 0
    assert down == 0
    assert all(r.weight == WEIGHT_FULL for r in results)
    assert all(r.outcome == OUTCOME_FULL for r in results)


def test_severe_outlier_excluded():
    """One price far above the cluster (nd >> 4.0) must be excluded."""
    # Median ~$100, MAD ~$5, outlier $500 → nd = (500-100)/5 = 80 >> 4.0
    prices = [95, 98, 100, 102, 105, 500]
    results, excl, down = apply_price_sanity(_pool(prices))

    assert excl == 1
    # The $500 comp must be the excluded one
    excluded = [r for r in results if r.weight == WEIGHT_EXCLUDED]
    assert len(excluded) == 1
    assert excluded[0].comp.nightly_price == 500
    assert excluded[0].outcome == OUTCOME_EXCLUDED


def test_mild_outlier_downweighted():
    """Mild outlier (nd between 2.5 and 4.0) gets weight 0.5."""
    # Median ~$100, MAD ~$5.  Price $115 → nd ≈ (115-100)/5 = 3.0 (between 2.5 and 4.0)
    prices = [95, 98, 100, 102, 105, 115]
    results, excl, down = apply_price_sanity(_pool(prices))

    assert excl == 0
    assert down == 1
    dw = [r for r in results if r.weight == WEIGHT_HALF]
    assert len(dw) == 1
    assert dw[0].comp.nightly_price == 115
    assert dw[0].outcome == OUTCOME_DOWNWEIGHTED


def test_both_severe_and_mild_outliers():
    """Pool with one excluded and one downweighted comp."""
    # cluster ~$100, MAD ~$5
    # $118: nd ≈ 3.6 → downweighted
    # $600: nd >> 4.0 → excluded
    prices = [95, 98, 100, 102, 105, 118, 600]
    results, excl, down = apply_price_sanity(_pool(prices))

    assert excl == 1
    assert down == 1
    assert sum(r.weight == WEIGHT_FULL for r in results) == 5


# ── MAD-zero fallback ─────────────────────────────────────────────

def test_mad_zero_uses_percentage_band():
    """When all prices are identical, MAD=0; fallback band ±40% is used."""
    # All comps at $100; a comp at $160 is 60% above median.
    # band = 100 * 0.40 = 40, nd = |160-100| / 40 = 1.5 → full weight
    prices = [100, 100, 100, 100, 100, 160]
    results, excl, down = apply_price_sanity(_pool(prices))
    assert excl == 0
    assert down == 0

    # A comp at $250 → nd = |250-100| / 40 = 3.75 → downweighted
    prices2 = [100, 100, 100, 100, 100, 250]
    results2, excl2, down2 = apply_price_sanity(_pool(prices2))
    assert excl2 == 0
    assert down2 == 1

    # A comp at $400 → nd = |400-100| / 40 = 7.5 → excluded
    prices3 = [100, 100, 100, 100, 100, 400]
    results3, excl3, down3 = apply_price_sanity(_pool(prices3))
    assert excl3 == 1
    assert down3 == 0


# ── Wide-market protection ────────────────────────────────────────

def test_wide_market_relaxes_thresholds():
    """market_cv > 0.5: thresholds widen to 3.5 / 5.5."""
    # Build a genuinely dispersed market (CV > 0.5)
    # median ~$120, MAD ~$60
    # A comp at $330: nd = (330-120)/60 = 3.5 → at the wide full_threshold exactly
    # In standard mode (threshold 2.5) this would be downweighted;
    # in wide mode (threshold 3.5) it should be full weight.
    prices = [40, 60, 80, 120, 200, 260, 330]
    results, excl, down = apply_price_sanity(_pool(prices))

    # market_cv will be > 0.5 for this spread; verify no false downgrades
    import statistics as st
    mean_p = st.mean(prices)
    cv = st.pstdev(prices) / mean_p
    assert cv > 0.5, f"Test assumption failed: cv={cv:.2f} not > 0.5"

    # The $330 comp should NOT be downweighted in wide mode
    assert down == 0 or all(
        r.comp.nightly_price != 330 for r in results if r.weight == WEIGHT_HALF
    )


def test_very_wide_small_sample_skips_sanity():
    """market_cv > 0.8 and n < 8 → sanity skipped entirely."""
    # Extremely dispersed: $10 to $1000 with n=6
    prices = [10, 20, 100, 300, 700, 1000]
    results, excl, down = apply_price_sanity(_pool(prices))

    import statistics as st
    mean_p = st.mean(prices)
    cv = st.pstdev(prices) / mean_p
    assert cv > 0.8, f"Test assumption failed: cv={cv:.2f} not > 0.8"

    assert excl == 0
    assert down == 0
    assert all(r.outcome == OUTCOME_SKIPPED for r in results)


# ── 2-night price uplift ──────────────────────────────────────────

def test_two_night_uplift_prevents_false_low_outlier():
    """
    The +5% uplift for 2-night prices prevents false downweighting.

    Cluster: [80, 90, 100, 110, 120] → median=100, MAD=10.
    A 2-night comp at $74/night (raw):
      Without uplift: nd = |74-100|/10 = 2.6  → downweighted (> 2.5)
      With +5% uplift: nd = |77.7-100|/10 = 2.23 → full weight (≤ 2.5)
    """
    cluster = [_spec(p, scrape_nights=1) for p in [80, 90, 100, 110, 120]]
    two_night_comp = _spec(74, scrape_nights=2)
    pool = [(c, 0.7) for c in cluster] + [(two_night_comp, 0.7)]

    results, excl, down = apply_price_sanity(pool)
    two_night_result = next(r for r in results if r.comp is two_night_comp)

    # With +5% uplift, should be full weight (not downweighted or excluded)
    assert two_night_result.weight == WEIGHT_FULL
    assert two_night_result.outcome == OUTCOME_FULL


def test_two_night_genuine_outlier_still_excluded():
    """A 2-night comp with a genuinely extreme price should still be excluded."""
    cluster = [_spec(p) for p in [98, 100, 101, 102, 103]]
    # 2-night comp: nightly_price = 25 (÷2 already) → 25 * 1.05 = 26.25
    # median ~$101, MAD ~$1 → nd = (101 - 26.25) / 1 >> 4.0
    cheap_2night = _spec(25, scrape_nights=2)
    pool = [(c, 0.7) for c in cluster] + [(cheap_2night, 0.7)]

    results, excl, down = apply_price_sanity(pool)
    cheap_result = next(r for r in results if r.comp is cheap_2night)
    assert cheap_result.weight == WEIGHT_EXCLUDED


# ── No-price comps ────────────────────────────────────────────────

def test_no_price_comp_excluded():
    """Comp with nightly_price=None is excluded regardless of sanity thresholds."""
    prices = [98, 100, 101, 102, 103, None]
    results, excl, down = apply_price_sanity(_pool(prices))

    none_result = results[-1]
    assert none_result.weight == WEIGHT_EXCLUDED
    assert none_result.outcome == OUTCOME_NO_PRICE


# ── build_price_sanity_weights ────────────────────────────────────

def test_build_price_sanity_weights_excludes_zeros():
    """Excluded comps (weight=0.0) must not appear in the weights dict."""
    prices = [95, 98, 100, 102, 105, 500]
    pool = _pool(prices)
    results, excl, _ = apply_price_sanity(pool)
    assert excl == 1

    weights = build_price_sanity_weights(results)
    # Excluded comp must not be in the dict
    excluded_comp = next(r.comp for r in results if r.weight == WEIGHT_EXCLUDED)
    assert id(excluded_comp) not in weights

    # All other comps must be in the dict with weight 1.0
    for r in results:
        if r.weight > 0:
            assert id(r.comp) in weights
            assert weights[id(r.comp)] == r.weight


def test_build_price_sanity_weights_downweighted_is_half():
    """Downweighted comps appear in the dict with value 0.5."""
    prices = [95, 98, 100, 102, 105, 115]
    pool = _pool(prices)
    results, _, down = apply_price_sanity(pool)
    assert down == 1

    weights = build_price_sanity_weights(results)
    dw_comp = next(r.comp for r in results if r.weight == WEIGHT_HALF)
    assert weights[id(dw_comp)] == WEIGHT_HALF


# ── pricing_engine integration ────────────────────────────────────

def test_recommend_price_downweighted_comp_has_less_influence():
    """
    A downweighted comp (price=115, weight=0.5) should pull the weighted
    mean less than a full-weight comp at the same price would.
    """
    from worker.core.pricing_engine import recommend_price
    from worker.scraper.target_extractor import ListingSpec as LS

    def _full_spec(price: float) -> LS:
        s = MagicMock(spec=LS)
        s.nightly_price = price
        s.scrape_nights = 1
        s.property_type = "entire_home"
        s.bedrooms = 2
        s.baths = 1.0
        s.accommodates = 4
        s.beds = 2
        s.amenities = []
        s.url = None
        s.title = ""
        s.rating = None
        s.reviews = None
        s.location = ""
        s.currency = "USD"
        return s

    target = _full_spec(100)  # price not used for target
    cluster = [_full_spec(p) for p in [98, 100, 102]]
    high_comp = _full_spec(200)  # the outlier

    all_comps = cluster + [high_comp]

    # Without downweighting: high_comp at full weight
    price_full, _ = recommend_price(target, all_comps, new_listing_discount=0.0)

    # With downweighting: high_comp at 0.5×
    ps_weights = {id(high_comp): WEIGHT_HALF}
    price_down, _ = recommend_price(
        target, all_comps, new_listing_discount=0.0, price_sanity_weights=ps_weights
    )

    # Downweighted result must be closer to the cluster ($100)
    assert price_down is not None
    assert price_full is not None
    assert price_down < price_full, (
        f"Expected downweighted price ({price_down}) < full-weight price ({price_full})"
    )
    assert abs(price_down - 100) < abs(price_full - 100), (
        "Downweighted price should be closer to cluster median"
    )
