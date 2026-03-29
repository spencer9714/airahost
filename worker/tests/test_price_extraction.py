"""
Unit tests for nightly price candidate selection logic.

Tests the pure-Python select_nightly_price_from_candidates() helper which
is the core decision function for choosing between strikethrough (original)
and non-strikethrough (discounted/current) price elements scraped from the
Airbnb booking widget DOM.

These tests do NOT require a browser — they operate on the structured
candidate dicts that the JS layer (_BOOKING_WIDGET_PRICE_JS) would return.
"""

import pytest
from worker.scraper.target_extractor import select_nightly_price_from_candidates


# ---------------------------------------------------------------------------
# Core discount scenario
# ---------------------------------------------------------------------------

def test_picks_discounted_price_over_strikethrough_original():
    """
    Critical case: $540 (strikethrough original) and $465 (current discounted).
    System must return $465, not $540.
    """
    candidates = [
        {"value": 540, "strikethrough": True, "domIndex": 0},   # original (crossed out)
        {"value": 465, "strikethrough": False, "domIndex": 1},  # discounted (current)
    ]
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    price, kind = result
    assert price == 465.0
    assert kind == "nightly_discounted"


def test_price_kind_is_discounted_when_original_present():
    """price_kind reflects that a strikethrough original was present."""
    candidates = [
        {"value": 300, "strikethrough": True, "domIndex": 0},
        {"value": 250, "strikethrough": False, "domIndex": 1},
    ]
    _, kind = select_nightly_price_from_candidates(candidates)
    assert kind == "nightly_discounted"


# ---------------------------------------------------------------------------
# Standard (no discount) scenario
# ---------------------------------------------------------------------------

def test_picks_standard_price_when_no_discount():
    """Single non-strikethrough entry → standard nightly."""
    candidates = [{"value": 350, "strikethrough": False, "domIndex": 0}]
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    price, kind = result
    assert price == 350.0
    assert kind == "nightly_standard"


def test_price_kind_is_standard_when_no_strikethrough():
    candidates = [{"value": 200, "strikethrough": False, "domIndex": 0}]
    _, kind = select_nightly_price_from_candidates(candidates)
    assert kind == "nightly_standard"


# ---------------------------------------------------------------------------
# DOM order: last non-strikethrough is the discounted price
# ---------------------------------------------------------------------------

def test_last_non_strikethrough_in_dom_order_is_selected():
    """
    When the DOM has: original(strikethrough) → discounted → /night
    the last non-strikethrough candidate (highest domIndex) must be chosen.
    """
    candidates = [
        {"value": 600, "strikethrough": True, "domIndex": 0},   # original
        {"value": 520, "strikethrough": False, "domIndex": 1},  # discounted
        {"value": 520, "strikethrough": False, "domIndex": 2},  # same price, duplicate span
    ]
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    price, _ = result
    assert price == 520.0


def test_multiple_non_strikethrough_picks_last_by_dom_index():
    """Among multiple non-strikethrough candidates picks the highest domIndex."""
    candidates = [
        {"value": 400, "strikethrough": False, "domIndex": 0},
        {"value": 380, "strikethrough": False, "domIndex": 3},
        {"value": 370, "strikethrough": False, "domIndex": 1},
    ]
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    price, _ = result
    assert price == 380.0  # domIndex=3 is the last


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_candidates_returns_none():
    assert select_nightly_price_from_candidates([]) is None


def test_all_strikethrough_returns_none():
    """If every candidate has strikethrough, we cannot determine current price."""
    candidates = [
        {"value": 540, "strikethrough": True, "domIndex": 0},
        {"value": 465, "strikethrough": True, "domIndex": 1},
    ]
    assert select_nightly_price_from_candidates(candidates) is None


def test_price_below_minimum_returns_none():
    """Prices < $10 are likely fees or formatting artifacts — reject them."""
    candidates = [{"value": 5, "strikethrough": False, "domIndex": 0}]
    assert select_nightly_price_from_candidates(candidates) is None


def test_price_above_maximum_returns_none():
    """Prices > $10 000 are likely totals — reject them."""
    candidates = [{"value": 15000, "strikethrough": False, "domIndex": 0}]
    assert select_nightly_price_from_candidates(candidates) is None


def test_price_at_boundary_10_is_valid():
    candidates = [{"value": 10, "strikethrough": False, "domIndex": 0}]
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    assert result[0] == 10.0


def test_price_at_boundary_10000_is_valid():
    candidates = [{"value": 10000, "strikethrough": False, "domIndex": 0}]
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    assert result[0] == 10000.0


def test_missing_strikethrough_key_treated_as_false():
    """Candidates without 'strikethrough' key default to non-strikethrough."""
    candidates = [{"value": 299, "domIndex": 0}]  # no 'strikethrough' key
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    assert result[0] == 299.0


def test_missing_dom_index_does_not_crash():
    """Candidates without 'domIndex' should still work (defaults to 0)."""
    candidates = [
        {"value": 500, "strikethrough": True},   # no domIndex
        {"value": 420, "strikethrough": False},  # no domIndex
    ]
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    # Both have domIndex defaulting to 0; last in sorted order is either —
    # the important thing is a non-strikethrough price is returned
    price, kind = result
    assert price == 420.0
    assert kind == "nightly_discounted"


def test_float_value_preserved():
    """Values passed as floats are returned as floats."""
    candidates = [{"value": 123.45, "strikethrough": False, "domIndex": 0}]
    result = select_nightly_price_from_candidates(candidates)
    assert result is not None
    assert result[0] == 123.45
