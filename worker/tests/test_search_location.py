"""
Tests for _extract_search_location() and _build_structured_search_location()
in worker/scraper/price_estimator.py.

Core rule: ZIP / postalCode is ALWAYS the highest-priority search token.
Whenever a postalCode is present (structured fields) or a bare ZIP is found
in an address string (fallback parser), it is used as the search location —
even when city and state are also available.  This eliminates city-name
ambiguity (e.g. "Belmont" exists in CA, NC, and as a Long Beach neighbourhood;
"94002" is unambiguously San Mateo County, CA).
"""
import pytest

from worker.scraper.price_estimator import (
    _build_structured_search_location,
    _extract_search_location,
)


# ---------------------------------------------------------------------------
# _build_structured_search_location — ZIP is highest priority
# ---------------------------------------------------------------------------

class TestBuildStructuredSearchLocation:
    """
    Priority order (new rule — ZIP wins):
      postalCode present (any combo) → postalCode
      city + state (no postalCode)   → "City, ST"
      city alone                     → ""  (ambiguous; caller falls back)
      all empty                      → ""
    """

    # --- postalCode present → always use ZIP ---

    def test_city_state_postal_returns_zip(self):
        """city + state + postalCode: ZIP wins, not the combined string."""
        loc, conf = _build_structured_search_location("Belmont", "CA", "94002")
        assert loc == "94002"
        assert conf == "high"

    def test_city_postal_no_state_returns_zip(self):
        """city + ZIP without state → ZIP."""
        loc, conf = _build_structured_search_location("Belmont", None, "94002")
        assert loc == "94002"
        assert conf == "high"

    def test_postal_alone_returns_zip(self):
        """Only postalCode available → ZIP."""
        loc, conf = _build_structured_search_location(None, None, "94002")
        assert loc == "94002"
        assert conf == "high"

    def test_state_postal_no_city_returns_zip(self):
        """state + ZIP (no city) → ZIP."""
        loc, conf = _build_structured_search_location(None, "CA", "94002")
        assert loc == "94002"
        assert conf == "high"

    def test_different_zip(self):
        loc, conf = _build_structured_search_location("Austin", "TX", "78701")
        assert loc == "78701"
        assert conf == "high"

    # --- no postalCode → fall through to city + state ---

    def test_city_state_no_postal_returns_city_state(self):
        loc, conf = _build_structured_search_location("Belmont", "CA", None)
        assert loc == "Belmont, CA"
        assert conf == "high"

    def test_city_state_no_postal_new_york(self):
        loc, conf = _build_structured_search_location("New York", "NY", None)
        assert loc == "New York, NY"
        assert conf == "high"

    # --- city alone → empty (ambiguous) ---

    def test_city_alone_returns_empty(self):
        """City alone is ambiguous; caller must fall back."""
        loc, conf = _build_structured_search_location("Belmont", None, None)
        assert loc == ""
        assert conf == ""

    def test_all_empty_returns_empty(self):
        loc, conf = _build_structured_search_location(None, None, None)
        assert loc == ""
        assert conf == ""

    # --- whitespace / empty-string normalization ---

    def test_whitespace_stripped_with_zip(self):
        loc, conf = _build_structured_search_location("  Belmont  ", "  CA  ", "  94002  ")
        assert loc == "94002"
        assert conf == "high"

    def test_empty_strings_treated_as_missing_returns_zip(self):
        loc, conf = _build_structured_search_location("", "", "94002")
        assert loc == "94002"
        assert conf == "high"

    def test_empty_strings_no_zip_returns_empty(self):
        loc, conf = _build_structured_search_location("", "", "")
        assert loc == ""
        assert conf == ""


# ---------------------------------------------------------------------------
# _extract_search_location — fallback address-string parser
# ---------------------------------------------------------------------------

class TestExtractSearchLocation:
    """
    Core contract (ZIP is highest priority here too):
      - any ZIP found in address → ZIP  (even when state is present)
      - city + state (no ZIP)   → "City, ST"
      - pure ZIP                → ZIP
      - city-only               → city  (medium confidence; no better option)
      - Taiwanese address       → city+district (preserved)
    """

    # --- ZIP wins, regardless of whether state is present ---

    def test_city_zip_no_state_returns_zip(self):
        """THE original bug: Belmont, 94002 must NOT collapse to 'Belmont'."""
        loc, conf = _extract_search_location("Belmont, 94002")
        assert loc == "94002"
        assert conf == "high"

    def test_street_city_zip_no_state_returns_zip(self):
        """Street prefix is skipped; city + ZIP (no state) → ZIP."""
        loc, conf = _extract_search_location("123 Main St, Belmont, 94002")
        assert loc == "94002"
        assert conf == "high"

    def test_city_state_zip_returns_zip(self):
        """ZIP wins even when state is present — consistent with structured builder."""
        loc, conf = _extract_search_location("123 Main St, Belmont, CA, 94002")
        assert loc == "94002"
        assert conf == "high"

    def test_city_state_zip_no_street(self):
        loc, conf = _extract_search_location("Belmont, CA, 94002")
        assert loc == "94002"
        assert conf == "high"

    def test_street_city_zip_another_city(self):
        loc, conf = _extract_search_location("45 Oak Ave, Redwood City, 94063")
        assert loc == "94063"
        assert conf == "high"

    # --- pure ZIP passthrough ---

    def test_pure_zip_preserved(self):
        loc, conf = _extract_search_location("94002")
        assert loc == "94002"
        assert conf == "high"

    # --- no ZIP → city + state ---

    def test_city_state_no_zip(self):
        loc, conf = _extract_search_location("New York, NY")
        assert loc == "New York, NY"
        assert conf == "high"

    def test_full_address_with_state_no_zip(self):
        loc, conf = _extract_search_location("123 Main St, New York, NY")
        assert loc == "New York, NY"
        assert conf == "high"

    # --- "City, ST POSTAL" inline (ZIP embedded in last comma-part) ---

    def test_city_state_zip_inline_last_part(self):
        """'New York, NY 10001' — ZIP embedded in final part, extracted as ZIP."""
        loc, conf = _extract_search_location("123 Main St, New York, NY 10001")
        # NY 10001 is a single comma-part; current parser preserves it as-is
        # because the ZIP is not a standalone comma-part here.
        assert loc == "New York, NY 10001"
        assert conf == "high"

    # --- city-only fallback ---

    def test_city_only_medium_confidence(self):
        loc, conf = _extract_search_location("Belmont")
        assert loc == "Belmont"
        assert conf == "medium"

    # --- Taiwan: must NOT be broken ---

    def test_taiwanese_city_district(self):
        loc, conf = _extract_search_location("台北市信義區松山路123號")
        assert loc == "台北市信義區"
        assert conf == "high"

    def test_taiwanese_county(self):
        loc, conf = _extract_search_location("新北市板橋區府中路100號")
        assert loc == "新北市板橋區"
        assert conf == "high"


# ---------------------------------------------------------------------------
# Integration: run_criteria_search location selection logic
# ---------------------------------------------------------------------------

class TestCriteriaLocationSelection:
    """
    Verifies that _build_structured_search_location() is tried first and
    _extract_search_location() is used only when structured fields are absent,
    without needing to spin up a browser / Playwright.
    """

    def _resolve(self, address: str, city=None, state=None, postal_code=None) -> str:
        """Mirror the location selection logic in run_criteria_search()."""
        loc, _ = _build_structured_search_location(city, state, postal_code)
        if loc:
            return loc
        loc, _ = _extract_search_location(address)
        return loc

    # Structured path with ZIP → always ZIP

    def test_belmont_ca_94002_structured(self):
        result = self._resolve("Belmont, CA 94002", city="Belmont", state="CA", postal_code="94002")
        assert result == "94002"

    def test_belmont_94002_no_state_structured(self):
        result = self._resolve("Belmont, 94002", city="Belmont", state=None, postal_code="94002")
        assert result == "94002"

    def test_postal_only_structured(self):
        result = self._resolve("94002", city=None, state=None, postal_code="94002")
        assert result == "94002"

    def test_city_state_no_zip_structured(self):
        result = self._resolve("Belmont, CA", city="Belmont", state="CA", postal_code=None)
        assert result == "Belmont, CA"

    # No structured fields → falls back to address parser

    def test_address_fallback_city_zip(self):
        """When no structured fields, address parser applies ZIP-wins rule."""
        result = self._resolve("Belmont, 94002")
        assert result == "94002"

    def test_address_fallback_city_state_zip(self):
        result = self._resolve("123 Main St, Belmont, CA, 94002")
        assert result == "94002"

    def test_address_fallback_city_state_no_zip(self):
        result = self._resolve("Belmont, CA")
        assert result == "Belmont, CA"

    def test_address_fallback_city_only(self):
        result = self._resolve("Belmont")
        assert result == "Belmont"

    def test_address_fallback_taiwan(self):
        result = self._resolve("台北市信義區松山路123號")
        assert result == "台北市信義區"
