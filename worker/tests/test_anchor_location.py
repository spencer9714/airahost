"""
Unit tests for worker/core/anchor_location.py.

All tests are pure-Python — no network, no browser.

The metro-cluster system is the key new feature: two cities in the same cluster
are "nearby_market"; same state but different cluster is "regional_mismatch".

Key design requirements verified here:
  Belmont vs Redwood City   → nearby_market    (both CA:bay_peninsula)
  Belmont vs San Carlos     → nearby_market    (both CA:bay_peninsula)
  Belmont vs San Mateo      → nearby_market    (both CA:bay_peninsula)
  Belmont vs San Francisco  → regional_mismatch (CA:bay_sf ≠ CA:bay_peninsula)
  Belmont vs Sonoma         → regional_mismatch (CA:wine_country ≠ CA:bay_peninsula)
  Belmont vs Oakland        → regional_mismatch (CA:bay_east ≠ CA:bay_peninsula)
  Belmont vs Portland OR    → far_mismatch      (different state)
"""

from __future__ import annotations

import pytest

from worker.core.anchor_location import (
    classify_candidate_location,
    get_city_cluster,
    get_nearby_cities,
    normalize_city,
    normalize_location_text,
    normalize_state,
    parse_location_city_state,
)


# ---------------------------------------------------------------------------
# normalize_state
# ---------------------------------------------------------------------------

class TestNormalizeState:

    def test_two_letter_code_uppercased(self):
        assert normalize_state("ca") == "CA"
        assert normalize_state("CA") == "CA"
        assert normalize_state("tx") == "TX"

    def test_full_name_maps_to_code(self):
        assert normalize_state("california") == "CA"
        assert normalize_state("California") == "CA"
        assert normalize_state("New York") == "NY"
        assert normalize_state("west virginia") == "WV"
        assert normalize_state("District of Columbia") == "DC"

    def test_unknown_state_returns_lower(self):
        # Non-US regions fall through — returned as lowercased string
        assert normalize_state("Ontario") == "ontario"
        assert normalize_state("Île-de-France") == "île-de-france"

    def test_empty_returns_empty(self):
        assert normalize_state("") == ""
        assert normalize_state("   ") == ""


# ---------------------------------------------------------------------------
# normalize_city
# ---------------------------------------------------------------------------

class TestNormalizeCity:

    def test_strips_and_lowercases(self):
        assert normalize_city("Belmont") == "belmont"
        assert normalize_city("  San Mateo  ") == "san mateo"
        assert normalize_city("SAN FRANCISCO") == "san francisco"

    def test_empty_returns_empty(self):
        assert normalize_city("") == ""


# ---------------------------------------------------------------------------
# parse_location_city_state
# ---------------------------------------------------------------------------

class TestParseLocationCityState:

    def test_city_comma_full_state(self):
        city, state = parse_location_city_state("Belmont, California")
        assert city == "belmont"
        assert state == "CA"

    def test_city_comma_abbrev_state(self):
        city, state = parse_location_city_state("San Mateo, CA")
        assert city == "san mateo"
        assert state == "CA"

    def test_multi_word_city(self):
        city, state = parse_location_city_state("Redwood City, California")
        assert city == "redwood city"
        assert state == "CA"

    def test_non_us_location(self):
        city, state = parse_location_city_state("Paris, Île-de-France")
        assert city == "paris"
        assert state == "île-de-france"  # non-US: lowercased as-is

    def test_city_only(self):
        city, state = parse_location_city_state("Belmont")
        assert city == "belmont"
        assert state == ""

    def test_empty_string(self):
        city, state = parse_location_city_state("")
        assert city == ""
        assert state == ""


# ---------------------------------------------------------------------------
# get_city_cluster
# ---------------------------------------------------------------------------

class TestGetCityCluster:

    def test_peninsula_cities_in_same_cluster(self):
        assert get_city_cluster("CA", "belmont") == "CA:bay_peninsula"
        assert get_city_cluster("CA", "redwood city") == "CA:bay_peninsula"
        assert get_city_cluster("CA", "san carlos") == "CA:bay_peninsula"
        assert get_city_cluster("CA", "san mateo") == "CA:bay_peninsula"
        assert get_city_cluster("CA", "burlingame") == "CA:bay_peninsula"
        assert get_city_cluster("CA", "palo alto") == "CA:bay_peninsula"

    def test_sf_in_own_cluster(self):
        assert get_city_cluster("CA", "san francisco") == "CA:bay_sf"

    def test_east_bay_cluster(self):
        assert get_city_cluster("CA", "oakland") == "CA:bay_east"
        assert get_city_cluster("CA", "berkeley") == "CA:bay_east"
        assert get_city_cluster("CA", "hayward") == "CA:bay_east"

    def test_wine_country_cluster(self):
        assert get_city_cluster("CA", "sonoma") == "CA:wine_country"
        assert get_city_cluster("CA", "napa") == "CA:wine_country"
        assert get_city_cluster("CA", "santa rosa") == "CA:wine_country"

    def test_south_bay_cluster(self):
        # Core Silicon Valley cities are in the new sub-cluster
        assert get_city_cluster("CA", "san jose") == "CA:bay_south_core"
        assert get_city_cluster("CA", "sunnyvale") == "CA:bay_south_core"
        assert get_city_cluster("CA", "mountain view") == "CA:bay_south_core"
        # Far south are in their own sub-cluster (keeps San Jose vs Gilroy separate)
        assert get_city_cluster("CA", "gilroy") == "CA:bay_south_far"
        assert get_city_cluster("CA", "morgan hill") == "CA:bay_south_far"

    def test_unknown_city_returns_none(self):
        assert get_city_cluster("CA", "timbuktu") is None
        assert get_city_cluster("CA", "") is None

    def test_case_insensitive(self):
        # get_city_cluster normalises internally
        assert get_city_cluster("ca", "Belmont") == "CA:bay_peninsula"
        assert get_city_cluster("CA", "REDWOOD CITY") == "CA:bay_peninsula"

    def test_same_city_name_different_states(self):
        # "portland" exists in OR and ME — must not collide
        or_cluster = get_city_cluster("OR", "portland")
        ca_cluster = get_city_cluster("CA", "portland")
        # CA:portland is not in any cluster — None
        assert ca_cluster is None
        # OR:portland may or may not be in a cluster (not defined); just not CA
        assert or_cluster != "CA:bay_peninsula"


# ---------------------------------------------------------------------------
# classify_candidate_location — five-bucket system
# ---------------------------------------------------------------------------

class TestClassifyCandidateLocation:
    """
    Target: Belmont, CA

    Key requirement:
      same city (Belmont)   → local_match
      same cluster (RC, SC, SM) → nearby_market
      same state, diff cluster (SF, Sonoma, Oakland) → regional_mismatch
      different state        → far_mismatch
      unparseable            → unknown
    """

    TARGET_CITY = "belmont"
    TARGET_STATE = "CA"

    def _classify(self, location_text: str) -> str:
        return classify_candidate_location(
            location_text, self.TARGET_CITY, self.TARGET_STATE
        )

    # -- local_match

    def test_exact_city_match(self):
        assert self._classify("Belmont, California") == "local_match"

    def test_exact_city_match_abbrev_state(self):
        assert self._classify("Belmont, CA") == "local_match"

    def test_case_insensitive_match(self):
        assert self._classify("BELMONT, CALIFORNIA") == "local_match"

    # -- nearby_market (same CA:bay_peninsula cluster)

    def test_redwood_city_is_nearby(self):
        assert self._classify("Redwood City, California") == "nearby_market"

    def test_san_carlos_is_nearby(self):
        assert self._classify("San Carlos, California") == "nearby_market"

    def test_san_mateo_is_nearby(self):
        assert self._classify("San Mateo, CA") == "nearby_market"

    def test_palo_alto_is_nearby(self):
        assert self._classify("Palo Alto, California") == "nearby_market"

    def test_burlingame_is_nearby(self):
        assert self._classify("Burlingame, California") == "nearby_market"

    # -- regional_mismatch (same state, different cluster)

    def test_san_francisco_is_regional(self):
        """SF is a distinct market cluster from the Peninsula."""
        assert self._classify("San Francisco, California") == "regional_mismatch"

    def test_sonoma_is_regional(self):
        """Sonoma is CA:wine_country — different cluster from CA:bay_peninsula."""
        assert self._classify("Sonoma, California") == "regional_mismatch"

    def test_oakland_is_regional(self):
        """Oakland is CA:bay_east — different cluster from CA:bay_peninsula."""
        assert self._classify("Oakland, California") == "regional_mismatch"

    def test_hayward_is_regional(self):
        assert self._classify("Hayward, California") == "regional_mismatch"

    def test_santa_rosa_is_regional(self):
        assert self._classify("Santa Rosa, California") == "regional_mismatch"

    def test_san_jose_is_regional(self):
        """San Jose is CA:bay_south — different cluster."""
        assert self._classify("San Jose, California") == "regional_mismatch"

    def test_unlisted_ca_city_is_regional(self):
        """Cities not in any cluster → regional_mismatch (conservative)."""
        assert self._classify("Fresno, California") == "regional_mismatch"
        assert self._classify("Sacramento, California") == "regional_mismatch"

    # -- far_mismatch (different state)

    def test_portland_oregon_is_far(self):
        assert self._classify("Portland, Oregon") == "far_mismatch"

    def test_new_york_city_is_far(self):
        assert self._classify("New York City, New York") == "far_mismatch"

    def test_seattle_wa_is_far(self):
        assert self._classify("Seattle, Washington") == "far_mismatch"

    def test_austin_tx_is_far(self):
        assert self._classify("Austin, TX") == "far_mismatch"

    # -- unknown / unparseable

    def test_empty_location_is_unknown(self):
        assert self._classify("") == "unknown"

    def test_city_only_no_state_is_unknown(self):
        # Can't determine cross-state without state info
        assert self._classify("Belmont") == "unknown"

    def test_no_target_city_returns_unknown(self):
        result = classify_candidate_location("Belmont, CA", "", "CA")
        assert result == "unknown"

    def test_no_target_state_returns_unknown(self):
        result = classify_candidate_location("Belmont, CA", "belmont", "")
        assert result == "unknown"

    # -- cross-cluster within state is regional, not nearby

    def test_sf_vs_east_bay_no_overlap(self):
        """Two different Bay Area sub-markets → regional_mismatch relative to each other."""
        result = classify_candidate_location(
            "San Francisco, California", "oakland", "CA"
        )
        assert result == "regional_mismatch"

    def test_peninsula_vs_wine_country(self):
        result = classify_candidate_location(
            "Sonoma, California", "redwood city", "CA"
        )
        assert result == "regional_mismatch"

    # -- full state name in target_state normalised correctly

    def test_target_full_state_name_normalised(self):
        result = classify_candidate_location(
            "Redwood City, California", "belmont", "california"
        )
        assert result == "nearby_market"

    def test_cross_state_with_full_name_target(self):
        result = classify_candidate_location(
            "Portland, Oregon", "belmont", "california"
        )
        assert result == "far_mismatch"


# ---------------------------------------------------------------------------
# get_nearby_cities
# ---------------------------------------------------------------------------

class TestGetNearbyCities:

    def test_belmont_nearby_includes_peninsula_siblings(self):
        nearby = get_nearby_cities("CA", "Belmont")
        # Key requirement: Peninsula siblings must be in the approved list
        assert "redwood city" in nearby
        assert "san carlos" in nearby
        assert "san mateo" in nearby
        assert "burlingame" in nearby
        assert "palo alto" in nearby

    def test_belmont_nearby_excludes_self(self):
        nearby = get_nearby_cities("CA", "Belmont")
        assert "belmont" not in nearby

    def test_belmont_nearby_does_not_include_sf(self):
        """SF is CA:bay_sf — not in the same cluster as Belmont."""
        nearby = get_nearby_cities("CA", "Belmont")
        assert "san francisco" not in nearby

    def test_belmont_nearby_does_not_include_sonoma(self):
        """Sonoma is CA:wine_country — not in the same cluster as Belmont."""
        nearby = get_nearby_cities("CA", "Belmont")
        assert "sonoma" not in nearby

    def test_belmont_nearby_does_not_include_oakland(self):
        """Oakland is CA:bay_east — different cluster."""
        nearby = get_nearby_cities("CA", "Belmont")
        assert "oakland" not in nearby

    def test_unknown_city_returns_empty(self):
        nearby = get_nearby_cities("CA", "unknown_city_xyz")
        assert nearby == []

    def test_returns_sorted_list(self):
        nearby = get_nearby_cities("CA", "Belmont")
        assert nearby == sorted(nearby)

    def test_case_insensitive(self):
        nearby_lower = get_nearby_cities("ca", "belmont")
        nearby_mixed = get_nearby_cities("CA", "Belmont")
        assert nearby_lower == nearby_mixed

    def test_sonoma_nearby_includes_wine_country(self):
        """Sonoma's nearby cities are other Wine Country cities, not Peninsula."""
        nearby = get_nearby_cities("CA", "Sonoma")
        assert "napa" in nearby
        assert "santa rosa" in nearby
        assert "petaluma" in nearby
        # Must NOT include Peninsula cities
        assert "belmont" not in nearby
        assert "san mateo" not in nearby

    def test_sf_nearby_is_empty_own_cluster(self):
        """SF is alone in CA:bay_sf — no other approved nearby cities."""
        nearby = get_nearby_cities("CA", "San Francisco")
        assert nearby == []

    def test_austin_nearby_includes_tx_austin_cluster(self):
        nearby = get_nearby_cities("TX", "Austin")
        assert "round rock" in nearby
        assert "cedar park" in nearby
        assert len(nearby) > 3


# ---------------------------------------------------------------------------
# Cluster integrity — no city in two clusters for the same state
# ---------------------------------------------------------------------------

class TestClusterIntegrity:

    def test_no_duplicate_city_in_same_state(self):
        """Each (state, city) pair must map to at most one cluster."""
        from worker.core.anchor_location import _CITY_TO_CLUSTER, _METRO_CLUSTERS

        seen: dict = {}
        for cluster_id, cities in _METRO_CLUSTERS.items():
            state = cluster_id.split(":")[0]
            for city in cities:
                key = (state, city)
                assert key not in seen, (
                    f"City '{city}' in state '{state}' appears in both "
                    f"'{seen[key]}' and '{cluster_id}'"
                )
                seen[key] = cluster_id

    def test_cluster_ids_have_state_prefix(self):
        """All cluster IDs must follow the STATE:name convention."""
        from worker.core.anchor_location import _METRO_CLUSTERS

        for cluster_id in _METRO_CLUSTERS:
            parts = cluster_id.split(":", 1)
            assert len(parts) == 2, f"Cluster ID '{cluster_id}' missing state prefix"
            state = parts[0]
            assert len(state) == 2 and state.isalpha(), (
                f"Cluster '{cluster_id}' has invalid state prefix '{state}'"
            )


# ---------------------------------------------------------------------------
# Over-broad cluster fix — San Jose vs Gilroy
# ---------------------------------------------------------------------------

class TestOverBroadClusterFix:
    """
    CA:bay_south was split into CA:bay_south_core (San Jose metro) and
    CA:bay_south_far (Gilroy / Morgan Hill).  This prevents San Jose vs Gilroy
    (~45 km apart) from being classified as nearby_market.
    """

    def test_san_jose_gilroy_not_nearby_market(self):
        """San Jose and Gilroy are in different sub-clusters → regional_mismatch."""
        result = classify_candidate_location(
            "Gilroy, California", "san jose", "CA"
        )
        assert result == "regional_mismatch", (
            f"San Jose vs Gilroy must be regional_mismatch, got {result!r}"
        )

    def test_gilroy_san_jose_not_nearby_market(self):
        """Symmetric: Gilroy target, San Jose candidate → regional_mismatch."""
        result = classify_candidate_location(
            "San Jose, California", "gilroy", "CA"
        )
        assert result == "regional_mismatch"

    def test_san_jose_sunnyvale_still_nearby(self):
        """San Jose and Sunnyvale are in the same core cluster → nearby_market."""
        result = classify_candidate_location(
            "Sunnyvale, California", "san jose", "CA"
        )
        assert result == "nearby_market"

    def test_san_jose_santa_clara_still_nearby(self):
        result = classify_candidate_location(
            "Santa Clara, California", "san jose", "CA"
        )
        assert result == "nearby_market"

    def test_gilroy_morgan_hill_are_nearby(self):
        """Gilroy and Morgan Hill share CA:bay_south_far → nearby_market."""
        result = classify_candidate_location(
            "Morgan Hill, California", "gilroy", "CA"
        )
        assert result == "nearby_market"

    def test_gilroy_is_regional_from_peninsula(self):
        """Gilroy (CA:bay_south_far) is regional_mismatch relative to Belmont."""
        result = classify_candidate_location(
            "Gilroy, California", "belmont", "CA"
        )
        assert result == "regional_mismatch"

    def test_get_nearby_cities_excludes_gilroy_from_san_jose(self):
        """get_nearby_cities for San Jose must NOT include Gilroy."""
        nearby = get_nearby_cities("CA", "san jose")
        assert "sunnyvale" in nearby
        assert "gilroy" not in nearby
        assert "morgan hill" not in nearby

    def test_get_nearby_cities_gilroy_includes_morgan_hill(self):
        """Gilroy's approved nearby cities are only CA:bay_south_far siblings."""
        nearby = get_nearby_cities("CA", "gilroy")
        assert "morgan hill" in nearby
        # Must not include San Jose (different sub-cluster)
        assert "san jose" not in nearby
        assert "sunnyvale" not in nearby


# ---------------------------------------------------------------------------
# Fuzzy location normalisation
# ---------------------------------------------------------------------------

class TestNormalizeLocationText:

    def _n(self, raw: str) -> str:
        """Return the normalised text (ignore notes)."""
        text, _ = normalize_location_text(raw)
        return text

    def _notes(self, raw: str) -> str:
        _, notes = normalize_location_text(raw)
        return notes

    # ── prefix stripping ────────────────────────────────────────────────────

    def test_downtown_prefix_stripped(self):
        assert self._n("Downtown San Francisco, CA") == "San Francisco, CA"
        assert "prefix:downtown" in self._notes("Downtown San Francisco, CA")

    def test_downtown_full_state_preserved(self):
        assert self._n("Downtown San Francisco, California") == "San Francisco, California"

    def test_uptown_prefix_stripped(self):
        assert self._n("Uptown Chicago, IL") == "Chicago, IL"

    def test_midtown_prefix_stripped(self):
        assert self._n("Midtown Manhattan, NY") == "Manhattan, NY"

    def test_near_prefix_stripped(self):
        assert self._n("Near Redwood City, California") == "Redwood City, California"
        assert "prefix:near" in self._notes("Near Redwood City, California")

    def test_greater_prefix_stripped(self):
        assert self._n("Greater Boston, MA") == "Boston, MA"

    def test_old_town_prefix_stripped(self):
        assert self._n("Old Town San Diego, CA") == "San Diego, CA"
        assert "prefix:old town" in self._notes("Old Town San Diego, CA")

    # ── directional prefixes are NOT stripped ───────────────────────────────

    def test_north_hollywood_unchanged(self):
        """North Hollywood is a distinct place — directional prefix must not be stripped."""
        assert self._n("North Hollywood, CA") == "North Hollywood, CA"
        assert self._notes("North Hollywood, CA") == ""

    def test_west_hollywood_unchanged(self):
        assert self._n("West Hollywood, CA") == "West Hollywood, CA"

    def test_south_san_francisco_unchanged(self):
        assert self._n("South San Francisco, CA") == "South San Francisco, CA"

    def test_east_palo_alto_unchanged(self):
        assert self._n("East Palo Alto, CA") == "East Palo Alto, CA"

    # ── suffix stripping ────────────────────────────────────────────────────

    def test_county_suffix_stripped(self):
        assert self._n("San Mateo County, CA") == "San Mateo, CA"
        assert "suffix:county" in self._notes("San Mateo County, CA")

    def test_county_suffix_los_angeles(self):
        assert self._n("Los Angeles County, CA") == "Los Angeles, CA"

    def test_area_suffix_stripped(self):
        assert self._n("San Jose Area, CA") == "San Jose, CA"
        assert "suffix:area" in self._notes("San Jose Area, CA")

    def test_greater_plus_area_chained(self):
        """'Greater Boston Area, MA' strips prefix then suffix."""
        result = self._n("Greater Boston Area, MA")
        assert result == "Boston, MA"
        notes = self._notes("Greater Boston Area, MA")
        assert "prefix:greater" in notes
        assert "suffix:area" in notes

    # ── unscoped aliases ────────────────────────────────────────────────────

    def test_soma_unscoped(self):
        text, notes = normalize_location_text("SoMa")
        assert text == "san francisco"
        assert "alias:soma" in notes

    def test_soma_with_state(self):
        text, notes = normalize_location_text("SoMa, CA")
        assert text == "san francisco, CA"

    def test_south_of_market(self):
        text, _ = normalize_location_text("South of Market")
        assert text == "san francisco"

    def test_south_of_market_with_state(self):
        text, _ = normalize_location_text("South of Market, California")
        assert text == "san francisco, California"

    # ── state-scoped aliases ─────────────────────────────────────────────────

    def test_noe_valley_ca(self):
        text, notes = normalize_location_text("Noe Valley, CA")
        assert text == "san francisco, CA"
        assert "alias:noe valley" in notes

    def test_belmont_hills_ca(self):
        text, _ = normalize_location_text("Belmont Hills, CA")
        assert text == "belmont, CA"

    def test_hollywood_hills_ca(self):
        text, _ = normalize_location_text("Hollywood Hills, CA")
        assert text == "los angeles, CA"

    def test_williamsburg_ny(self):
        text, _ = normalize_location_text("Williamsburg, NY")
        assert text == "brooklyn, NY"

    def test_fremont_wa_scoped_to_seattle(self):
        """Fremont WA → seattle (state-scoped to avoid CA:fremont collision)."""
        text, _ = normalize_location_text("Fremont, WA")
        assert text == "seattle, WA"

    def test_fremont_ca_unchanged(self):
        """Fremont CA is its own city in CA:bay_east — must not be aliased."""
        text, notes = normalize_location_text("Fremont, CA")
        assert text == "Fremont, CA"
        assert notes == ""

    # ── conservative / no-op cases ──────────────────────────────────────────

    def test_standard_city_unchanged(self):
        assert self._n("Belmont, CA") == "Belmont, CA"
        assert self._notes("Belmont, CA") == ""

    def test_empty_returns_empty(self):
        assert self._n("") == ""
        assert normalize_location_text("") == ("", "")

    def test_whitespace_only_returns_empty(self):
        assert self._n("   ") == ""

    def test_ambiguous_name_unchanged(self):
        """'Castro' alone (no state, not an alias) is returned as-is."""
        # "castro" is not in the unscoped alias table (too ambiguous)
        text, notes = normalize_location_text("Castro")
        assert text == "Castro"
        assert notes == ""

    def test_no_state_alias_preserved(self):
        """'Belmont Hills' without state cannot be safely canonicalised."""
        text, notes = normalize_location_text("Belmont Hills")
        # No state → unscoped alias table has no entry → unchanged
        assert text == "Belmont Hills"
        assert notes == ""


# ---------------------------------------------------------------------------
# Ambiguous city / neighbourhood handling
# ---------------------------------------------------------------------------

class TestAmbiguousCityHandling:
    """
    Verifies that state-aware cluster lookup handles ambiguous city/neighbourhood
    names correctly without misclassifying listings.
    """

    TARGET_CITY  = "belmont"
    TARGET_STATE = "CA"

    def _classify(self, location: str) -> str:
        return classify_candidate_location(location, self.TARGET_CITY, self.TARGET_STATE)

    # ── Hollywood ────────────────────────────────────────────────────────────

    def test_hollywood_fl_is_far_from_ca_target(self):
        """Hollywood FL is a real city — different state → far_mismatch."""
        assert self._classify("Hollywood, Florida") == "far_mismatch"

    def test_hollywood_ca_is_regional_from_belmont(self):
        """Hollywood CA is in CA:la_central — regional mismatch for Peninsula."""
        assert self._classify("Hollywood, California") == "regional_mismatch"

    def test_north_hollywood_ca_is_regional(self):
        """North Hollywood is in CA:la_valley — regional mismatch for Peninsula."""
        assert self._classify("North Hollywood, California") == "regional_mismatch"

    def test_west_hollywood_ca_is_regional(self):
        """West Hollywood is in CA:la_westside — regional mismatch for Peninsula."""
        assert self._classify("West Hollywood, California") == "regional_mismatch"

    # ── Brentwood ────────────────────────────────────────────────────────────

    def test_brentwood_ca_is_regional_from_belmont(self):
        """Brentwood CA maps to CA:la_central → regional mismatch for Peninsula."""
        assert self._classify("Brentwood, California") == "regional_mismatch"

    def test_brentwood_tn_is_far(self):
        """Brentwood TN is in TN:nashville — different state → far_mismatch."""
        assert self._classify("Brentwood, Tennessee") == "far_mismatch"

    # ── SoMa / South of Market ───────────────────────────────────────────────

    def test_soma_with_state_is_regional_from_belmont(self):
        """SoMa, CA normalises to san francisco, CA → regional_mismatch."""
        assert self._classify("SoMa, CA") == "regional_mismatch"

    def test_south_of_market_ca_is_regional(self):
        assert self._classify("South of Market, CA") == "regional_mismatch"

    def test_soma_without_state_is_unknown(self):
        """SoMa without state → normalised to 'san francisco' (no state) → unknown."""
        assert self._classify("SoMa") == "unknown"

    # ── Neighbourhood normalisation improves classification ──────────────────

    def test_downtown_sf_is_regional_not_unknown(self):
        """'Downtown San Francisco, CA' normalises to SF → regional_mismatch."""
        result = classify_candidate_location(
            "Downtown San Francisco, California", "belmont", "CA"
        )
        assert result == "regional_mismatch"

    def test_downtown_belmont_is_local_match(self):
        """'Downtown Belmont, CA' normalises to 'Belmont, CA' → local_match."""
        result = classify_candidate_location(
            "Downtown Belmont, CA", "belmont", "CA"
        )
        assert result == "local_match"

    def test_near_redwood_city_is_nearby(self):
        """'Near Redwood City, CA' normalises → nearby_market for Belmont target."""
        result = classify_candidate_location(
            "Near Redwood City, California", "belmont", "CA"
        )
        assert result == "nearby_market"

    def test_san_mateo_county_is_nearby(self):
        """'San Mateo County, CA' → 'San Mateo, CA' → nearby_market for Belmont."""
        result = classify_candidate_location(
            "San Mateo County, CA", "belmont", "CA"
        )
        assert result == "nearby_market"


# ---------------------------------------------------------------------------
# New cluster coverage
# ---------------------------------------------------------------------------

class TestNewClusterCoverage:

    # ── NV:las_vegas ─────────────────────────────────────────────────────────

    def test_las_vegas_cluster_members(self):
        assert get_city_cluster("NV", "las vegas") == "NV:las_vegas"
        assert get_city_cluster("NV", "henderson") == "NV:las_vegas"
        assert get_city_cluster("NV", "north las vegas") == "NV:las_vegas"
        assert get_city_cluster("NV", "paradise") == "NV:las_vegas"

    def test_henderson_is_nearby_las_vegas(self):
        result = classify_candidate_location("Henderson, Nevada", "las vegas", "NV")
        assert result == "nearby_market"

    def test_las_vegas_vs_reno_is_regional(self):
        """Reno is not in any NV cluster → regional_mismatch for Las Vegas."""
        result = classify_candidate_location("Reno, Nevada", "las vegas", "NV")
        assert result == "regional_mismatch"

    def test_las_vegas_vs_phoenix_is_far(self):
        result = classify_candidate_location("Phoenix, Arizona", "las vegas", "NV")
        assert result == "far_mismatch"

    def test_get_nearby_cities_las_vegas(self):
        nearby = get_nearby_cities("NV", "Las Vegas")
        assert "henderson" in nearby
        assert "north las vegas" in nearby

    # ── OR:portland ──────────────────────────────────────────────────────────

    def test_portland_cluster_members(self):
        assert get_city_cluster("OR", "portland") == "OR:portland"
        assert get_city_cluster("OR", "beaverton") == "OR:portland"
        assert get_city_cluster("OR", "gresham") == "OR:portland"
        assert get_city_cluster("OR", "lake oswego") == "OR:portland"

    def test_beaverton_is_nearby_portland(self):
        result = classify_candidate_location("Beaverton, Oregon", "portland", "OR")
        assert result == "nearby_market"

    def test_portland_or_vs_seattle_wa_is_far(self):
        result = classify_candidate_location("Seattle, Washington", "portland", "OR")
        assert result == "far_mismatch"

    def test_portland_or_vs_eugene_is_regional(self):
        """Eugene OR is not in any cluster → regional_mismatch."""
        result = classify_candidate_location("Eugene, Oregon", "portland", "OR")
        assert result == "regional_mismatch"

    def test_get_nearby_cities_portland(self):
        nearby = get_nearby_cities("OR", "Portland")
        assert "beaverton" in nearby
        assert "lake oswego" in nearby
        assert "tigard" in nearby

    # ── MN:minneapolis ───────────────────────────────────────────────────────

    def test_minneapolis_cluster_members(self):
        assert get_city_cluster("MN", "minneapolis") == "MN:minneapolis"
        assert get_city_cluster("MN", "saint paul") == "MN:minneapolis"
        assert get_city_cluster("MN", "st. paul") == "MN:minneapolis"
        assert get_city_cluster("MN", "bloomington") == "MN:minneapolis"
        assert get_city_cluster("MN", "eden prairie") == "MN:minneapolis"

    def test_saint_paul_is_nearby_minneapolis(self):
        result = classify_candidate_location("Saint Paul, Minnesota", "minneapolis", "MN")
        assert result == "nearby_market"

    def test_st_paul_abbreviation_is_nearby(self):
        result = classify_candidate_location("St. Paul, Minnesota", "minneapolis", "MN")
        assert result == "nearby_market"

    def test_minneapolis_vs_duluth_is_regional(self):
        result = classify_candidate_location("Duluth, Minnesota", "minneapolis", "MN")
        assert result == "regional_mismatch"

    def test_minneapolis_vs_chicago_is_far(self):
        result = classify_candidate_location("Chicago, Illinois", "minneapolis", "MN")
        assert result == "far_mismatch"

    def test_get_nearby_cities_minneapolis(self):
        nearby = get_nearby_cities("MN", "Minneapolis")
        assert "saint paul" in nearby
        assert "st. paul" in nearby
        assert "bloomington" in nearby
        assert "eden prairie" in nearby
        assert "minneapolis" not in nearby

    # ── MI:detroit ───────────────────────────────────────────────────────────

    def test_detroit_cluster_members(self):
        assert get_city_cluster("MI", "detroit") == "MI:detroit"
        assert get_city_cluster("MI", "dearborn") == "MI:detroit"
        assert get_city_cluster("MI", "royal oak") == "MI:detroit"
        assert get_city_cluster("MI", "troy") == "MI:detroit"

    def test_dearborn_is_nearby_detroit(self):
        result = classify_candidate_location("Dearborn, Michigan", "detroit", "MI")
        assert result == "nearby_market"

    def test_detroit_vs_ann_arbor_is_regional(self):
        """Ann Arbor is deliberately excluded from MI:detroit (too far)."""
        result = classify_candidate_location("Ann Arbor, Michigan", "detroit", "MI")
        assert result == "regional_mismatch"

    def test_detroit_vs_chicago_is_far(self):
        result = classify_candidate_location("Chicago, Illinois", "detroit", "MI")
        assert result == "far_mismatch"


# ---------------------------------------------------------------------------
# Regression safety — existing Belmont / Bay Area behaviour unchanged
# ---------------------------------------------------------------------------

class TestRegressionSafety:
    """
    Fast smoke-test that the cluster split and new normalization code did not
    regress any previously-passing Belmont / Bay Area requirements.
    """

    def _classify(self, location: str) -> str:
        return classify_candidate_location(location, "belmont", "CA")

    def test_belmont_redwood_city_still_nearby(self):
        assert self._classify("Redwood City, California") == "nearby_market"

    def test_belmont_san_carlos_still_nearby(self):
        assert self._classify("San Carlos, California") == "nearby_market"

    def test_belmont_san_mateo_still_nearby(self):
        assert self._classify("San Mateo, CA") == "nearby_market"

    def test_belmont_palo_alto_still_nearby(self):
        assert self._classify("Palo Alto, California") == "nearby_market"

    def test_belmont_sf_still_regional(self):
        assert self._classify("San Francisco, California") == "regional_mismatch"

    def test_belmont_sonoma_still_regional(self):
        assert self._classify("Sonoma, California") == "regional_mismatch"

    def test_belmont_oakland_still_regional(self):
        assert self._classify("Oakland, California") == "regional_mismatch"

    def test_belmont_san_jose_still_regional(self):
        """San Jose moved from CA:bay_south → CA:bay_south_core but is still
        regional mismatch relative to Belmont (CA:bay_peninsula)."""
        assert self._classify("San Jose, California") == "regional_mismatch"

    def test_belmont_portland_still_far(self):
        assert self._classify("Portland, Oregon") == "far_mismatch"

    def test_belmont_self_is_local(self):
        assert self._classify("Belmont, CA") == "local_match"

    def test_nearby_cities_list_unchanged(self):
        """Peninsula siblings must still be in get_nearby_cities for Belmont."""
        nearby = get_nearby_cities("CA", "Belmont")
        for city in ("redwood city", "san carlos", "san mateo", "burlingame", "palo alto"):
            assert city in nearby, f"{city} missing from Belmont nearby list"
        assert "san francisco" not in nearby
        assert "sonoma" not in nearby
