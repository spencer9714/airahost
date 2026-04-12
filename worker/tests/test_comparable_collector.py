"""
Tests for comparable_collector price extraction.

Includes:
  - Python ports of the JS isPerNight / parseMoneyValue helpers so we can
    unit-test the regex logic without a browser.
  - parse_card_to_spec tests covering the new two-layer extraction contract.
  - End-to-end card-survival test verifying the regression case (price_value=None
    when JS could not extract a price) does NOT produce a false nightly_price,
    while a successfully-extracted price survives the day_query downstream filter.
"""

import re
from typing import Optional

from worker.scraper.comparable_collector import (
    extract_search_result_location,
    parse_card_to_spec,
)


# ---------------------------------------------------------------------------
# Python ports of the JS helper functions (for pure-Python unit testing)
# ---------------------------------------------------------------------------

def _is_per_night(text: str) -> bool:
    """Python equivalent of the JS isPerNight() in collect_search_cards."""
    return bool(re.search(r"/\s*night|per\s+night|night", text, re.IGNORECASE))


def _parse_money_value(text: str) -> Optional[float]:
    """Python equivalent of the JS parseMoneyValue() in collect_search_cards."""
    if not text:
        return None
    t = re.sub(r"\s+", "", text)
    m = re.search(
        r"(?:US\$|CA\$|AU\$|NZ\$|\$)(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)", t
    )
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return val if 10 <= val <= 10000 else None


# ---------------------------------------------------------------------------
# isPerNight — verifying the regression fix
# ---------------------------------------------------------------------------

class TestIsPerNight:
    """
    The regression: before the fix isPerNight() only matched '/night' and
    'per night', so cards whose DOM element text was "$189\nnight" or
    "$189 night" were silently rejected → price_value=null → nightly_price=None
    → all comps filtered → report failure.

    After the fix the bare 'night' alternative is restored, matching the
    original code's /(\/\s*night|per\s+night|night)/i pattern.
    """

    # --- cases that must match (the fix restores these) ---

    def test_bare_night_newline(self):
        """THE regression case: price and unit separated by newline in one element."""
        assert _is_per_night("$189\nnight")

    def test_bare_night_space(self):
        assert _is_per_night("$189 night")

    def test_bare_night_no_space(self):
        assert _is_per_night("$189night")

    # --- cases that already worked (must keep working) ---

    def test_slash_night_with_space(self):
        assert _is_per_night("$189 / night")

    def test_slash_night_no_space(self):
        assert _is_per_night("$189/night")

    def test_per_night(self):
        assert _is_per_night("$189 per night")

    def test_per_night_in_aria_label(self):
        assert _is_per_night("Cabin in Portland. $189 per night. 4.91 (312 reviews).")

    def test_case_insensitive(self):
        assert _is_per_night("$189 / Night")
        assert _is_per_night("$189 Per Night")
        assert _is_per_night("$189\nNIGHT")

    # --- cases that must NOT match ---

    def test_no_night_keyword(self):
        assert not _is_per_night("$189")
        assert not _is_per_night("$189 total")
        assert not _is_per_night("3 guests · 2 bedrooms")
        assert not _is_per_night("")


# ---------------------------------------------------------------------------
# parseMoneyValue — verifying currency breadth and range guard
# ---------------------------------------------------------------------------

class TestParseMoneyValue:
    def test_plain_dollar(self):
        assert _parse_money_value("$189 / night") == 189.0

    def test_us_dollar(self):
        assert _parse_money_value("US$189 / night") == 189.0

    def test_ca_dollar(self):
        assert _parse_money_value("CA$175 / night") == 175.0

    def test_au_dollar(self):
        assert _parse_money_value("AU$80 / night") == 80.0

    def test_with_cents(self):
        assert _parse_money_value("$120.50 / night") == 120.50

    def test_thousands_comma(self):
        assert _parse_money_value("$1,234 / night") == 1234.0

    def test_whitespace_stripped(self):
        # Simulates "$189\nnight" after re.sub(r'\s+','')  → "$189night"
        assert _parse_money_value("$189\nnight") == 189.0

    def test_below_range_rejected(self):
        assert _parse_money_value("$5 / night") is None   # < 10 floor

    def test_above_range_rejected(self):
        # Regex is \d{1,4}(?:,\d{3})* so bare $99999 captures only $9999.
        # Use comma-separated notation to produce a value above 10000.
        assert _parse_money_value("$10,001 / night") is None  # 10001 > 10000 ceiling
        assert _parse_money_value("$99,999 / night") is None  # well above ceiling

    def test_no_currency_prefix(self):
        assert _parse_money_value("189 / night") is None

    def test_empty_string(self):
        assert _parse_money_value("") is None


# ---------------------------------------------------------------------------
# parse_card_to_spec — new two-layer extraction contract
# ---------------------------------------------------------------------------

class TestParseCardToSpec:
    """
    Contract after the two-layer refactor:
      - price_value non-null (aria or dom extracted) → nightly_price = that value
      - price_value null (JS could not safely extract)  → nightly_price = None
        (no Python-side fallback; a missing price is safer than a wrong one)
    """

    def test_aria_extracted_price_passes_through(self):
        card = {
            "url": "https://www.airbnb.com/rooms/100",
            "title": "Cozy cabin",
            "text": "Entire home · 4 guests · 2 bedrooms",
            "price_text": "189.0",
            "price_value": 189.0,
            "price_kind": "nightly_from_aria",
            "price_source": "aria",
            "rating": 4.91,
            "reviews": 312,
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price == 189.0

    def test_dom_standard_price_passes_through(self):
        card = {
            "url": "https://www.airbnb.com/rooms/200",
            "title": "Beach house",
            "text": "Entire home · 6 guests · 3 bedrooms",
            "price_text": "350.0",
            "price_value": 350.0,
            "price_kind": "nightly_standard",
            "price_source": "dom",
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price == 350.0

    def test_dom_discounted_price_passes_through(self):
        card = {
            "url": "https://www.airbnb.com/rooms/300",
            "title": "Discounted listing",
            "text": "Entire home · 4 guests · 2 bedrooms",
            "price_text": "465.0",
            "price_value": 465.0,
            "price_kind": "nightly_discounted",
            "price_source": "dom",
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price == 465.0

    def test_null_price_value_produces_none(self):
        """
        When JS extraction fails (ambiguous/no price), nightly_price must be
        None — not a guess from raw text.
        """
        card = {
            "url": "https://www.airbnb.com/rooms/400",
            "title": "Card where JS found no price",
            "text": "Entire home · 4 guests",
            "price_text": "",
            "price_value": None,
            "price_kind": "unknown",
            "price_source": "none",
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price is None

    def test_price_value_below_floor_produces_none(self):
        """Values outside [10, 10000] are rejected at the Python boundary."""
        card = {
            "url": "https://www.airbnb.com/rooms/500",
            "title": "Suspicious price",
            "text": "Entire home",
            "price_text": "5.0",
            "price_value": 5.0,   # < 10 floor
            "price_kind": "nightly_standard",
            "price_source": "dom",
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price is None

    def test_missing_price_source_field_is_tolerated(self):
        """Old-format cards without price_source/price_kind still parse."""
        card = {
            "url": "https://www.airbnb.com/rooms/600",
            "title": "Legacy card",
            "text": "Entire home · 2 guests",
            "price_text": "210.0",
            "price_value": 210.0,
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price == 210.0

    def test_extracts_search_card_location(self):
        card = {
            "url": "https://www.airbnb.com/rooms/700",
            "title": "Sunset Hill - Ballard 5bed/3bath",
            "text": "Entire home in Seattle, Washington\n16+ guests · 5 bedrooms · 11 beds · 3 baths",
            "price_text": "499.0",
            "price_value": 499.0,
            "price_kind": "nightly_standard",
            "price_source": "dom",
        }
        spec = parse_card_to_spec(card)
        assert spec.location == "Seattle, Washington"
        assert spec.accommodates == 16
        assert spec.bedrooms == 5
        assert spec.baths == 3.0

    def test_extracts_abbreviated_bd_ba_fields(self):
        card = {
            "url": "https://www.airbnb.com/rooms/701",
            "title": "Modern retreat",
            "text": "Entire home in Seattle, Washington\n10 guests · 5 bd · 8 beds · 5 ba",
            "price_text": "450.0",
            "price_value": 450.0,
            "price_kind": "nightly_standard",
            "price_source": "dom",
        }
        spec = parse_card_to_spec(card)
        assert spec.accommodates == 10
        assert spec.bedrooms == 5
        assert spec.baths == 5.0


class TestExtractSearchResultLocation:
    def test_entire_home_pattern(self):
        text = "Entire home in Edmonds, Washington\n10 guests · 5 bedrooms · 8 beds · 5 baths"
        assert extract_search_result_location(text) == "Edmonds, Washington"

    def test_badge_suffix_is_stripped(self):
        text = "Private room in Seattle · Guest favorite · ★4.9"
        assert extract_search_result_location(text) == "Seattle"


# ---------------------------------------------------------------------------
# End-to-end card survival: regression case + downstream filter
# ---------------------------------------------------------------------------

class TestCardSurvivalPipeline:
    """
    Verifies the full regression scenario end-to-end at the unit level:

      1. JS now correctly extracts a price for "$189\nnight"-style cards
         (represented here by price_value=189.0, as the JS would produce
         after the isPerNight fix).
      2. parse_card_to_spec produces nightly_price=189.0.
      3. The day_query.py downstream filter (c.url and c.nightly_price and
         c.nightly_price > 0) keeps the card in comps.
      4. comps_collected > 0, so the day produces a valid median_price instead
         of DayResult(median_price=None, flags=["missing_data"]).
    """

    @staticmethod
    def _day_query_filter(specs) -> list:
        """Mirrors day_query.py line 184."""
        return [c for c in specs if c.url and c.nightly_price and c.nightly_price > 0]

    def test_fixed_card_survives_downstream_filter(self):
        """
        Card whose text was "$189\nnight": before fix JS returned price_value=null,
        after fix JS returns price_value=189.0.
        """
        card = {
            "url": "https://www.airbnb.com/rooms/999",
            "title": "Cozy studio",
            "text": "Entire home · 2 guests · 1 bedroom · 1 bath",
            "price_text": "189.0",
            "price_value": 189.0,   # JS now extracts this after isPerNight fix
            "price_kind": "nightly_standard",
            "price_source": "dom",
            "rating": 4.85,
            "reviews": 47,
        }
        spec = parse_card_to_spec(card)
        surviving = self._day_query_filter([spec])

        assert spec.nightly_price == 189.0, "Price must be non-null after fix"
        assert len(surviving) == 1, "Card must survive day_query downstream filter"

    def test_broken_card_excluded_from_comps(self):
        """
        Card where JS could not extract a price (unknown/ambiguous) must be
        excluded so it never contaminates the median calculation.
        """
        card = {
            "url": "https://www.airbnb.com/rooms/998",
            "title": "Ambiguous card",
            "text": "Entire home · 2 guests",
            "price_text": "",
            "price_value": None,
            "price_kind": "unknown",
            "price_source": "none",
        }
        spec = parse_card_to_spec(card)
        surviving = self._day_query_filter([spec])

        assert spec.nightly_price is None
        assert len(surviving) == 0, "Card with no price must be excluded from comps"

    def test_mixed_batch_only_valid_prices_survive(self):
        """
        Realistic batch: some cards have prices, some do not.
        Only priced cards should reach the pricing engine.
        """
        cards = [
            # Good: aria-extracted price
            {
                "url": "https://www.airbnb.com/rooms/1",
                "title": "A", "text": "Entire home · 4 guests",
                "price_text": "220.0", "price_value": 220.0,
                "price_kind": "nightly_from_aria", "price_source": "aria",
            },
            # Good: DOM standard price (was failing before isPerNight fix)
            {
                "url": "https://www.airbnb.com/rooms/2",
                "title": "B", "text": "Entire home · 2 guests",
                "price_text": "189.0", "price_value": 189.0,
                "price_kind": "nightly_standard", "price_source": "dom",
            },
            # Good: discounted price
            {
                "url": "https://www.airbnb.com/rooms/3",
                "title": "C", "text": "Entire home · 6 guests",
                "price_text": "310.0", "price_value": 310.0,
                "price_kind": "nightly_discounted", "price_source": "dom",
            },
            # Bad: JS could not extract (all-strikethrough or no match)
            {
                "url": "https://www.airbnb.com/rooms/4",
                "title": "D", "text": "Entire home · 4 guests",
                "price_text": "", "price_value": None,
                "price_kind": "unknown", "price_source": "none",
            },
        ]

        specs = [parse_card_to_spec(c) for c in cards]
        surviving = self._day_query_filter(specs)

        assert len(surviving) == 3, "3 of 4 cards have valid prices"
        prices = {s.nightly_price for s in surviving}
        assert prices == {220.0, 189.0, 310.0}


# ---------------------------------------------------------------------------
# Regression: 2-night-primary detectTripNights false-positive halving
# ---------------------------------------------------------------------------

class TestTwoNightSecondaryDetection:
    """
    Regression test for the "prices too low" bug introduced when 2-night-primary
    queries were adopted.

    Root cause: the JS detectTripNights() secondary check fires whenever the card
    element's text contains "2 nights" AND stayNights=2.  Without the !isPerNight()
    guard, a DOM element showing "$150/night  2 nights minimum" was classified as
    price_kind="trip_total_*" with price_nights=2, causing parse_card_to_spec to
    halve the price to $75.

    After the fix: the secondary check is gated by !isPerNight(text), so a card
    with a per-night indicator is always treated as nightly regardless of surrounding
    "N nights" context text.

    These Python tests simulate the CARD DICT that the corrected JS produces and
    verify parse_card_to_spec handles each outcome correctly.
    """

    def test_per_night_card_with_minimum_stay_text_not_halved(self):
        """
        Bug scenario: card showing '$150/night' also contains '2 nights minimum'.
        Before fix: JS set price_nights=2, price_kind='trip_total_standard' → $75.
        After fix: JS sets price_nights=1, price_kind='nightly_standard' → $150.
        """
        card = {
            "url": "https://www.airbnb.com/rooms/700",
            "title": "Min-stay-2 listing",
            "text": "Entire home · 4 guests · 2 bedrooms",
            "price_text": "150.0",
            "price_value": 150.0,
            # Correct JS output after fix: per-night was detected; "2 nights" in
            # surrounding text did NOT override it.
            "price_kind": "nightly_standard",
            "price_nights": 1,
            "price_source": "dom",
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price == 150.0, (
            "Per-night price must not be halved even when '2 nights' appears in card text"
        )
        assert spec.scrape_nights == 1

    def test_genuine_trip_total_still_divided(self):
        """
        Legitimate trip-total card (e.g., "$300 for 2 nights" aria-label).
        JS correctly sets price_kind='trip_total_from_aria', price_nights=2.
        parse_card_to_spec must still divide → $150/night.
        """
        card = {
            "url": "https://www.airbnb.com/rooms/701",
            "title": "Min-stay-2 listing",
            "text": "Entire home · 4 guests · 2 bedrooms",
            "price_text": "300.0",
            "price_value": 300.0,
            "price_kind": "trip_total_from_aria",
            "price_nights": 2,
            "price_source": "aria",
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price == 150.0, "Trip-total $300/2 nights must divide to $150/night"
        assert spec.scrape_nights == 2

    def test_buggy_js_output_would_have_halved_per_night(self):
        """
        Documents the BEFORE-FIX JS output for a per-night card that also had
        '2 nights' text: price_kind='trip_total_standard', price_nights=2,
        price_value=150.  parse_card_to_spec would have produced $75.
        This test is kept as documentation of the regression.
        """
        buggy_card = {
            "url": "https://www.airbnb.com/rooms/702",
            "title": "Min-stay-2 listing",
            "text": "Entire home · 4 guests · 2 bedrooms",
            "price_text": "150.0",
            "price_value": 150.0,
            # This is what the buggy JS would have produced:
            "price_kind": "trip_total_standard",
            "price_nights": 2,
            "price_source": "dom",
        }
        spec = parse_card_to_spec(buggy_card)
        # Confirm the Python code DID halve it — the bug was in JS, not Python.
        assert spec.nightly_price == 75.0, (
            "parse_card_to_spec faithfully divides trip_total by price_nights — "
            "the fix must be in the JS detectTripNights guard, not here"
        )


# ---------------------------------------------------------------------------
# Regression: Airbnb .ca CAD-suffix price format (priced=0 across all cards)
# ---------------------------------------------------------------------------

class TestCADCurrencySuffixCards:
    """
    Regression for airbnb.ca listings where search cards display prices as
    '$267 CAD' without a '/night' label.  Before the fix, isPerNight() returned
    false for every card → extractFromAriaLabel and extractFromDOM both skipped
    the price element → price_value=null → priced=0 for all 24 cards → report
    fails with 'Scrape produced no daily results: No results'.

    After the fix, JS extracts the price and calls it 'nightly_from_currency_suffix'.
    These tests verify parse_card_to_spec handles that result correctly.
    """

    def test_cad_suffix_price_passes_through(self):
        """Card from airbnb.ca with '$267 CAD' nightly — JS now extracts it."""
        card = {
            "url": "https://www.airbnb.ca/rooms/700888293266225258",
            "title": "Luxury condo in Vancouver",
            "text": "Entire home · 4 guests · 2 bedrooms · 3 beds · 2 baths",
            "price_text": "267.0",
            "price_value": 267.0,
            "price_kind": "nightly_from_currency_suffix",
            "price_source": "currency_suffix",
            "rating": 4.95,
            "reviews": 87,
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price == 267.0

    def test_cad_suffix_card_survives_downstream_filter(self):
        """A CAD-suffix card with a valid price must survive the day_query filter."""
        card = {
            "url": "https://www.airbnb.ca/rooms/700888293266225258",
            "title": "Cozy cabin in Whistler",
            "text": "Entire home · 6 guests · 3 bedrooms · 4 beds · 2 baths",
            "price_text": "312.0",
            "price_value": 312.0,
            "price_kind": "nightly_from_currency_suffix",
            "price_source": "currency_suffix",
        }
        spec = parse_card_to_spec(card)
        surviving = [c for c in [spec] if c.url and c.nightly_price and c.nightly_price > 0]
        assert len(surviving) == 1
        assert surviving[0].nightly_price == 312.0

    def test_cad_total_only_card_still_excluded(self):
        """When JS correctly rejects a 'CAD total' element, price_value stays null."""
        card = {
            "url": "https://www.airbnb.ca/rooms/111",
            "title": "Ambiguous card",
            "text": "Entire home · 2 guests",
            "price_text": "",
            "price_value": None,
            "price_kind": "unknown",
            "price_source": "none",
        }
        spec = parse_card_to_spec(card)
        assert spec.nightly_price is None
