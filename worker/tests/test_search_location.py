"""
Tests for _extract_search_location() and _build_structured_search_location()
in worker/scraper/price_estimator.py.

Covers the Belmont/94002 ambiguity bug where a city+ZIP input was collapsed
to city-only, allowing results from geographically unrelated cities (e.g.
Belmont Shore in Long Beach) to appear.
"""
import pytest

from worker.scraper.price_estimator import (
    _build_structured_search_location,
    _extract_search_location,
)


# ---------------------------------------------------------------------------
# _extract_search_location — bug regression cases
# ---------------------------------------------------------------------------

class TestExtractSearchLocation:
    """
    Core contract:
      - city + ZIP (no state) → ZIP  (ZIP is more precise than ambiguous city)
      - city + state + ZIP    → "City, ST"  (state disambiguates; ZIP not needed)
      - city + state (no ZIP) → "City, ST"
      - pure ZIP              → ZIP
      - city-only             → city  (medium confidence; no better option)
      - Taiwanese address     → city+district (preserved)
    """

    # -- THE BUG: city + ZIP without state must not collapse to city-only --

    def test_city_zip_no_state_returns_zip(self):
        """Belmont, 94002 must NOT collapse to 'Belmont'."""
        loc, conf = _extract_search_location("Belmont, 94002")
        assert loc == "94002"
        assert conf == "high"

    def test_street_city_zip_returns_zip(self):
        """Street prefix is skipped; city + ZIP (no state) → ZIP."""
        loc, conf = _extract_search_location("123 Main St, Belmont, 94002")
        assert loc == "94002"
        assert conf == "high"

    def test_street_city_zip_another_city(self):
        """Same rule for a different city/ZIP."""
        loc, conf = _extract_search_location("45 Oak Ave, Redwood City, 94063")
        assert loc == "94063"
        assert conf == "high"

    # -- Cases that were already correct and must stay correct --

    def test_pure_zip_preserved(self):
        """Bare ZIP passes straight through."""
        loc, conf = _extract_search_location("94002")
        assert loc == "94002"
        assert conf == "high"

    def test_city_state_zip_returns_city_state(self):
        """Full address with state: state disambiguates, ZIP not needed."""
        loc, conf = _extract_search_location("123 Main St, Belmont, CA, 94002")
        assert loc == "Belmont, CA"
        assert conf == "high"

    def test_city_state_no_zip(self):
        """City + state without ZIP → City, ST."""
        loc, conf = _extract_search_location("New York, NY")
        assert loc == "New York, NY"
        assert conf == "high"

    def test_city_only_medium_confidence(self):
        """Single unqualified city token → returned as-is with medium confidence."""
        loc, conf = _extract_search_location("Belmont")
        assert loc == "Belmont"
        assert conf == "medium"

    def test_full_address_with_state(self):
        """Street + city + state → city, state."""
        loc, conf = _extract_search_location("123 Main St, New York, NY")
        assert loc == "New York, NY"
        assert conf == "high"

    def test_full_address_city_state_zip_inline(self):
        """'City, NY 10001' style (state+ZIP in one part)."""
        loc, conf = _extract_search_location("123 Main St, New York, NY 10001")
        assert loc == "New York, NY 10001"
        assert conf == "high"

    # -- Taiwan: must NOT be broken by this change --

    def test_taiwanese_city_district(self):
        """Traditional Chinese city+district is still extracted correctly."""
        loc, conf = _extract_search_location("台北市信義區松山路123號")
        assert loc == "台北市信義區"
        assert conf == "high"

    def test_taiwanese_county(self):
        loc, conf = _extract_search_location("新北市板橋區府中路100號")
        assert loc == "新北市板橋區"
        assert conf == "high"


# ---------------------------------------------------------------------------
# _build_structured_search_location — structured-first priority
# ---------------------------------------------------------------------------

class TestBuildStructuredSearchLocation:
    """
    Priority order:
      city + state + postalCode  →  "City, ST POSTAL"  (fully qualified)
      postalCode alone           →  "POSTAL"
      city + state               →  "City, ST"
      city alone                 →  ""  (fall back; city-only is ambiguous)
      all empty                  →  ""
    """

    def test_city_state_postal_fully_qualified(self):
        loc, conf = _build_structured_search_location("Belmont", "CA", "94002")
        assert loc == "Belmont, CA 94002"
        assert conf == "high"

    def test_postal_code_alone(self):
        """When only ZIP is available, use it — highly precise."""
        loc, conf = _build_structured_search_location(None, None, "94002")
        assert loc == "94002"
        assert conf == "high"

    def test_city_and_postal_no_state_returns_postal(self):
        """city + ZIP without state → ZIP (avoids ambiguous city-only)."""
        loc, conf = _build_structured_search_location("Belmont", None, "94002")
        assert loc == "94002"
        assert conf == "high"

    def test_city_state_no_postal(self):
        loc, conf = _build_structured_search_location("Belmont", "CA", None)
        assert loc == "Belmont, CA"
        assert conf == "high"

    def test_city_alone_returns_empty(self):
        """City alone is ambiguous; caller should fall back to address parsing."""
        loc, conf = _build_structured_search_location("Belmont", None, None)
        assert loc == ""
        assert conf == ""

    def test_all_empty_returns_empty(self):
        loc, conf = _build_structured_search_location(None, None, None)
        assert loc == ""
        assert conf == ""

    def test_whitespace_is_stripped(self):
        loc, conf = _build_structured_search_location("  Belmont  ", "  CA  ", "  94002  ")
        assert loc == "Belmont, CA 94002"
        assert conf == "high"

    def test_empty_strings_treated_as_missing(self):
        loc, conf = _build_structured_search_location("", "", "94002")
        assert loc == "94002"
        assert conf == "high"

    def test_different_state_and_zip(self):
        loc, conf = _build_structured_search_location("Austin", "TX", "78701")
        assert loc == "Austin, TX 78701"
        assert conf == "high"
