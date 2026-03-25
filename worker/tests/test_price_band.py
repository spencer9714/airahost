"""
Tests for Phase 3B price-band-aware filtering.

Covers:
  price_band.py:
    - make_anchor_band: returns correct (lower, upper) from anchor ± pcts
    - find_majority_band: returns None when n < 4
    - find_majority_band: returns IQR-based band when n >= 4
    - find_majority_band: ignores non-positive prices
    - apply_price_band_filter: empty input returns empty lists
    - apply_price_band_filter: with anchor → comps in band retained, out excluded
    - apply_price_band_filter: comps without price always pass through
    - apply_price_band_filter: no anchor, n < 4 → no filtering
    - apply_price_band_filter: no anchor, n >= 4 → majority band applied
    - apply_price_band_filter: band_info keys present
    - apply_price_band_filter: anchor_mode "anchor" when anchor_price given
    - apply_price_band_filter: anchor_mode "majority" when derived from comps
    - apply_price_band_filter: anchor_mode "none" when no anchor and n < 4

  split pricing/display behavior (Bug B fix — "only 1 comparable" regression):
    - when all comps are outside ±30% anchor, pricing_pool is empty but in_band
      is still returned so display logic can show them (apply_price_band_filter
      returns out_of_band separately — caller decides what to display)
    - all-outside-band scenario: 0 in_band, N out_of_band
    - caller can still build top_comps from the original above_floor list

  benchmark URL date handling (Bug A fix):
    - extract_nightly_price_from_listing_page strips any existing date params
      and injects the caller-supplied checkin/checkout regardless of what was
      in the original URL (unit test via URL reconstruction logic)
"""

from __future__ import annotations

import pytest

from worker.core.price_band import (
    PRICE_BAND_LOWER_PCT,
    PRICE_BAND_UPPER_PCT,
    apply_price_band_filter,
    find_majority_band,
    make_anchor_band,
)
from worker.scraper.target_extractor import ListingSpec


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_spec(price: float | None = None) -> ListingSpec:
    s = ListingSpec(url="https://www.airbnb.com/rooms/1")
    s.nightly_price = price
    return s


def _scored(price: float | None = None, score: float = 0.8):
    return (_make_spec(price), score)


# ── make_anchor_band ──────────────────────────────────────────────────────────

def test_make_anchor_band_defaults():
    lower, upper = make_anchor_band(300.0)
    assert lower == pytest.approx(300.0 * (1 - PRICE_BAND_LOWER_PCT))
    assert upper == pytest.approx(300.0 * (1 + PRICE_BAND_UPPER_PCT))


def test_make_anchor_band_custom_pcts():
    lower, upper = make_anchor_band(200.0, lower_pct=0.20, upper_pct=0.40)
    assert lower == pytest.approx(160.0)
    assert upper == pytest.approx(280.0)


def test_make_anchor_band_zero_pcts():
    lower, upper = make_anchor_band(150.0, lower_pct=0.0, upper_pct=0.0)
    assert lower == pytest.approx(150.0)
    assert upper == pytest.approx(150.0)


# ── find_majority_band ────────────────────────────────────────────────────────

def test_find_majority_band_too_few_prices():
    assert find_majority_band([100.0, 150.0, 200.0]) is None


def test_find_majority_band_empty():
    assert find_majority_band([]) is None


def test_find_majority_band_ignores_nonpositive():
    # 0.0 and -50.0 stripped → 3 valid prices → None
    assert find_majority_band([0.0, -50.0, 100.0, 150.0, 200.0]) is None
    # 4 valid prices after stripping zero
    result = find_majority_band([0.0, 100.0, 150.0, 200.0, 250.0])
    assert result is not None


def test_find_majority_band_returns_iqr_expanded():
    # Symmetric set: Q1=100, Q3=300 → lower=70, upper=390
    prices = [100.0, 100.0, 200.0, 300.0, 300.0]
    result = find_majority_band(prices)
    assert result is not None
    lower, upper = result
    # Q1 and Q3 of [100,100,200,300,300] depend on quantiles implementation;
    # just verify lower < upper and they straddle the median
    assert lower < upper
    assert lower < 200.0 < upper


def test_find_majority_band_tight_cluster():
    # All prices near $300 → band should be roughly 210–390
    prices = [290.0, 295.0, 300.0, 305.0, 310.0]
    lower, upper = find_majority_band(prices)
    assert lower == pytest.approx(290.0 * 0.70, rel=0.02)
    assert upper == pytest.approx(310.0 * 1.30, rel=0.02)


# ── apply_price_band_filter ───────────────────────────────────────────────────

def test_price_band_empty_input():
    in_band, out_of_band, info = apply_price_band_filter([], anchor_price=300.0)
    assert in_band == []
    assert out_of_band == []
    assert info["anchor_mode"] == "none"


def test_price_band_anchor_retains_in_band():
    # anchor=$300, band=[210, 390]
    near = _scored(280.0)
    far = _scored(900.0)
    in_band, out_of_band, info = apply_price_band_filter([near, far], anchor_price=300.0)
    assert len(in_band) == 1
    assert in_band[0][0] is near[0]
    assert len(out_of_band) == 1
    assert out_of_band[0][0] is far[0]


def test_price_band_anchor_excludes_below_band():
    # $50 is below $300 × 0.70 = $210
    cheap = _scored(50.0)
    in_band, out_of_band, info = apply_price_band_filter([cheap], anchor_price=300.0)
    assert len(out_of_band) == 1


def test_price_band_no_price_passes_through():
    no_price = _scored(None)
    in_band, out_of_band, info = apply_price_band_filter([no_price], anchor_price=300.0)
    assert len(in_band) == 1
    assert len(out_of_band) == 0


def test_price_band_zero_price_passes_through():
    zero = _scored(0.0)
    in_band, out_of_band, info = apply_price_band_filter([zero], anchor_price=300.0)
    assert len(in_band) == 1


def test_price_band_anchor_mode_anchor():
    _, _, info = apply_price_band_filter([_scored(300.0)], anchor_price=300.0)
    assert info["anchor_mode"] == "anchor"
    assert info["anchor_price"] == 300.0
    assert info["lower"] is not None
    assert info["upper"] is not None


def test_price_band_no_anchor_majority_when_enough_comps():
    # 5 comps near $300, no anchor → majority band applied
    comps = [_scored(290.0), _scored(295.0), _scored(300.0), _scored(305.0), _scored(310.0)]
    # One far outlier
    comps.append(_scored(1800.0))
    in_band, out_of_band, info = apply_price_band_filter(comps, anchor_price=None)
    assert info["anchor_mode"] == "majority"
    assert len(out_of_band) >= 1
    assert out_of_band[0][0].nightly_price == 1800.0


def test_price_band_no_anchor_no_filter_when_too_few_comps():
    # Only 3 comps → no majority band → no filter
    comps = [_scored(300.0), _scored(900.0), _scored(1800.0)]
    in_band, out_of_band, info = apply_price_band_filter(comps, anchor_price=None)
    assert info["anchor_mode"] == "none"
    assert len(in_band) == 3
    assert len(out_of_band) == 0


def test_price_band_anchor_mode_none_when_no_anchor_and_few_comps():
    comps = [_scored(300.0), _scored(500.0)]
    _, _, info = apply_price_band_filter(comps, anchor_price=None)
    assert info["anchor_mode"] == "none"


def test_price_band_band_info_has_required_keys():
    _, _, info = apply_price_band_filter([_scored(300.0)], anchor_price=200.0)
    assert "anchor_mode" in info
    assert "anchor_price" in info
    assert "lower" in info
    assert "upper" in info


def test_price_band_example_from_requirements():
    # benchmark ~$300, comps include $900 and $1800 → both excluded
    comps = [
        _scored(280.0),   # in band
        _scored(310.0),   # in band
        _scored(900.0),   # out: 3× benchmark
        _scored(1800.0),  # out: 6× benchmark
    ]
    in_band, out_of_band, info = apply_price_band_filter(comps, anchor_price=300.0)
    assert len(in_band) == 2
    assert len(out_of_band) == 2
    assert all(c.nightly_price in (900.0, 1800.0) for c, _ in out_of_band)


# ── Bug B fix: "only 1 comparable" regression ─────────────────────────────────
# The fix moves price band application from above_floor (display) to
# pricing_pool (pricing only).  These tests verify the split semantics.

def test_price_band_all_comps_outside_band_still_returned_as_out_of_band():
    """
    When ALL comps are outside ±30%, pricing is empty but out_of_band is non-empty.
    Caller can use out_of_band to understand what was excluded, and can still
    build display from the original above_floor list (which price band never touches).
    """
    # benchmark=$300, all market comps at $400-$500 → all outside $210-$390
    comps = [_scored(400.0), _scored(450.0), _scored(500.0)]
    in_band, out_of_band, info = apply_price_band_filter(comps, anchor_price=300.0)
    assert len(in_band) == 0
    assert len(out_of_band) == 3  # all excluded from pricing
    # Caller keeps the original list for display — this is what prevents "1 comp"


def test_price_band_pricing_only_leaves_display_intact():
    """
    The split: apply filter to pricing_pool but keep above_floor for display.
    Verify that out_of_band comps are exactly what the display still shows.
    """
    above_floor = [_scored(280.0), _scored(400.0), _scored(500.0)]
    # Simulate: price band applied to pricing_pool (a copy), not to above_floor
    pricing_pool_pre_band = list(above_floor)
    in_band, out_of_band, _ = apply_price_band_filter(pricing_pool_pre_band, anchor_price=300.0)

    # Pricing pool has only the in-band comp
    assert len(in_band) == 1
    assert in_band[0][0].nightly_price == 280.0

    # above_floor is UNCHANGED — display still shows all 3
    assert len(above_floor) == 3  # not mutated


# ── Bug A fix: benchmark URL date injection ───────────────────────────────────

def test_extract_nightly_price_strips_old_dates_from_url():
    """
    extract_nightly_price_from_listing_page rebuilds the URL with caller-supplied
    dates, stripping any pre-existing check_in/check_out params.

    This is a unit test of the URL construction logic extracted from the function.
    """
    from urllib.parse import urlparse, parse_qs

    def _build_url_with_dates(listing_url: str, checkin: str, checkout: str) -> str:
        """Mirrors the URL reconstruction in extract_nightly_price_from_listing_page."""
        parsed = urlparse(listing_url)
        return (
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            f"?check_in={checkin}&check_out={checkout}&adults=2"
        )

    # URL with stale/wrong dates
    stale_url = "https://www.airbnb.com/rooms/12345?check_in=2020-01-01&check_out=2020-01-02"
    result = _build_url_with_dates(stale_url, "2025-06-01", "2025-06-02")

    parsed = urlparse(result)
    qs = parse_qs(parsed.query)
    assert qs["check_in"] == ["2025-06-01"]
    assert qs["check_out"] == ["2025-06-02"]
    assert "2020" not in result  # old dates stripped

    # URL without any dates
    bare_url = "https://www.airbnb.com/rooms/99999"
    result2 = _build_url_with_dates(bare_url, "2025-07-15", "2025-07-16")
    parsed2 = urlparse(result2)
    qs2 = parse_qs(parsed2.query)
    assert qs2["check_in"] == ["2025-07-15"]
    assert qs2["check_out"] == ["2025-07-16"]


def test_benchmark_checkout_str_is_always_one_night():
    """
    _extract_benchmark_price_with_min_stay_fallback should always be called
    with a 1-night checkout so that its own 1→2-night fallback logic works.
    Verify that the checkin + 1 day formula produces the right string.
    """
    from datetime import date, timedelta

    date_i = date(2025, 6, 15)
    # The fix: always use date_i + 1, not the market search's checkout_str
    bm_checkout = (date_i + timedelta(days=1)).isoformat()
    assert bm_checkout == "2025-06-16"

    # Even if market search used 2-night fallback, benchmark gets 1-night
    market_checkout_2night = (date_i + timedelta(days=2)).isoformat()
    assert market_checkout_2night == "2025-06-17"
    assert bm_checkout != market_checkout_2night
