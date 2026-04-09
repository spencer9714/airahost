"""
Tests for _select_anchor_candidate() in worker/scraper/price_estimator.py.

All tests are pure-Python — no browser, no network.
Coordinates are real WGS-84 points (verified with haversine_km):

  Belmont, CA      37.5202, -122.2758   ← target
  San Mateo, CA    37.5630, -122.3255   ← ~6.5 km  (inside 20 km tight)
  Redwood City, CA 37.4849, -122.2364   ← ~5.2 km  (inside 20 km tight)
  Hayward, CA      37.6688, -122.0808   ← ~23.8 km (outside 20 km, inside 40 km)
  Sonoma, CA       38.2919, -122.4580   ← ~87.3 km (outside 40 km fallback)
"""

from __future__ import annotations

from typing import Optional

import pytest

from worker.scraper.price_estimator import (
    _ANCHOR_MIN_GEO_CANDIDATES,
    _ANCHOR_RADIUS_FALLBACK_KM,
    _ANCHOR_RADIUS_TIGHT_KM,
    _select_anchor_candidate,
)
from worker.scraper.target_extractor import ListingSpec

# ---------------------------------------------------------------------------
# Coord constants (WGS-84)
# ---------------------------------------------------------------------------

TARGET_LAT, TARGET_LNG = 37.5202, -122.2758      # Belmont, CA

_LOCAL_A_LAT, _LOCAL_A_LNG = 37.5630, -122.3255  # San Mateo, CA   ~6.5 km  (inside 20 km)
_LOCAL_B_LAT, _LOCAL_B_LNG = 37.4849, -122.2364  # Redwood City    ~5.2 km  (inside 20 km)
_EDGE_LAT, _EDGE_LNG       = 37.6688, -122.0808  # Hayward, CA     ~23.8 km (outside 20 km, inside 40 km)
_REMOTE_LAT, _REMOTE_LNG   = 38.2919, -122.4580  # Sonoma, CA      ~87.3 km (outside 40 km)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec(
    room_id: int,
    *,
    bedrooms: int = 2,
    baths: float = 1.0,
    accommodates: int = 4,
    property_type: str = "entire_home",
    price: float = 150.0,
    location: str = "",
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> ListingSpec:
    return ListingSpec(
        url=f"https://www.airbnb.com/rooms/{room_id}",
        title=f"Listing {room_id}",
        location=location,
        bedrooms=bedrooms,
        baths=baths,
        accommodates=accommodates,
        beds=bedrooms,
        property_type=property_type,
        nightly_price=price,
        lat=lat,
        lng=lng,
    )


def _target(
    bedrooms: int = 2,
    baths: float = 1.0,
    accommodates: int = 4,
    property_type: str = "entire_home",
) -> ListingSpec:
    return ListingSpec(
        url="",
        title="User property",
        bedrooms=bedrooms,
        baths=baths,
        accommodates=accommodates,
        beds=bedrooms,
        property_type=property_type,
        lat=TARGET_LAT,
        lng=TARGET_LNG,
    )


# ---------------------------------------------------------------------------
# A. Geo wins over structural similarity
# ---------------------------------------------------------------------------

class TestGeoConstraint:

    def test_local_beats_structurally_superior_remote(self):
        """
        Sonoma candidate (87 km) is a perfect structural match.
        Two local candidates (6-7 km) are slightly imperfect structurally.
        Geo filter must exclude Sonoma so a local listing wins.
        Two locals satisfy _ANCHOR_MIN_GEO_CANDIDATES=2 → tight radius used.
        """
        target = _target(bedrooms=2, baths=1.0, accommodates=4)

        local_a = _spec(
            1001,
            bedrooms=2, baths=1.5, accommodates=4,   # slightly off baths
            lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG,      # 6.5 km — inside 20 km
        )
        local_b = _spec(
            1003,
            bedrooms=2, baths=1.0, accommodates=5,   # slightly off accommodates
            lat=_LOCAL_B_LAT, lng=_LOCAL_B_LNG,      # 5.2 km — inside 20 km
        )
        remote = _spec(
            1002,
            bedrooms=2, baths=1.0, accommodates=4,   # perfect structural match
            lat=_REMOTE_LAT, lng=_REMOTE_LNG,        # 87 km — outside 40 km
        )

        best, score, debug = _select_anchor_candidate(
            [local_a, local_b, remote], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=3,
        )

        assert best.url != remote.url, (
            f"Remote listing must not win; got {best.url} "
            f"(anchorDistanceKm={debug['anchorDistanceKm']})"
        )
        assert debug["anchorGeoRadiusKm"] == _ANCHOR_RADIUS_TIGHT_KM
        assert debug["anchorGeoFallback"] is False
        assert debug["anchorGeoSkipped"] is False
        # 2 locals survive tight filter; remote excluded
        assert debug["anchorCandidatesAfterGeo"] == 2
        assert debug["anchorDistanceKm"] < _ANCHOR_RADIUS_TIGHT_KM

    def test_multiple_local_ranked_by_similarity(self):
        """When multiple local candidates survive geo filter, the one with
        the best structural match is chosen."""
        target = _target(bedrooms=3, baths=2.0, accommodates=6)

        close_good = _spec(
            2001,
            bedrooms=3, baths=2.0, accommodates=6,    # perfect
            lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG,
        )
        close_poor = _spec(
            2002,
            bedrooms=1, baths=1.0, accommodates=2,    # very different
            lat=_LOCAL_B_LAT, lng=_LOCAL_B_LNG,
        )
        remote_good = _spec(
            2003,
            bedrooms=3, baths=2.0, accommodates=6,    # perfect but far
            lat=_REMOTE_LAT, lng=_REMOTE_LNG,
        )

        best, score, debug = _select_anchor_candidate(
            [close_good, close_poor, remote_good], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=3,
        )

        assert best.url == close_good.url
        assert debug["anchorCandidatesAfterGeo"] == 2  # close_good + close_poor

    def test_remote_wins_when_no_local_candidates(self):
        """If ALL candidates are beyond tight radius, fallback allows remote."""
        target = _target()
        remote_a = _spec(3001, lat=_REMOTE_LAT, lng=_REMOTE_LNG)
        remote_b = _spec(3002, bedrooms=3, lat=_REMOTE_LAT + 0.1, lng=_REMOTE_LNG)

        best, score, debug = _select_anchor_candidate(
            [remote_a, remote_b], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=2,
        )

        # Both are beyond 40 km so geo is skipped entirely
        assert debug["anchorGeoSkipped"] is True
        assert debug["anchorGeoRadiusKm"] is None
        # Still returns something
        assert best is not None


# ---------------------------------------------------------------------------
# B. No-coords fallback
# ---------------------------------------------------------------------------

class TestNoCoordsGraceful:

    def test_candidates_without_coords_pass_through(self):
        """Candidates with no coords must not be excluded — they're unknown location,
        not confirmed-far."""
        target = _target()

        no_coords_a = _spec(4001, bedrooms=2, baths=1.0)
        no_coords_b = _spec(4002, bedrooms=3, baths=2.0)

        best, score, debug = _select_anchor_candidate(
            [no_coords_a, no_coords_b], target, TARGET_LAT, TARGET_LNG
        )

        # Neither candidate was excluded; both available for ranking
        assert debug["anchorCandidatesAfterGeo"] == 2
        assert best is not None

    def test_mixed_coords_and_no_coords(self):
        """A local candidate with coords should beat a no-coords candidate
        with better structural match."""
        target = _target(bedrooms=2, baths=1.0, accommodates=4)

        no_coords_perfect = _spec(5001, bedrooms=2, baths=1.0, accommodates=4)
        local_imperfect = _spec(
            5002,
            bedrooms=2, baths=1.5, accommodates=4,
            lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG,
        )
        remote_perfect = _spec(
            5003,
            bedrooms=2, baths=1.0, accommodates=4,
            lat=_REMOTE_LAT, lng=_REMOTE_LNG,
        )

        best, score, debug = _select_anchor_candidate(
            [no_coords_perfect, local_imperfect, remote_perfect],
            target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=2,  # local_imperfect + remote_perfect have coords
        )

        # remote_perfect is excluded; no_coords_perfect and local_imperfect both survive
        assert best.url != remote_perfect.url
        # Exactly 2 survive (no_coords + local), remote excluded
        assert debug["anchorCandidatesAfterGeo"] == 2

    def test_no_target_coords_skips_geo(self):
        """When target has no coords, geo filter is skipped and all candidates compete."""
        target = _target()
        # user_spec has no coords — override lat/lng explicitly
        target.lat = None
        target.lng = None

        local = _spec(6001, lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG)
        remote = _spec(6002, bedrooms=2, baths=1.0, lat=_REMOTE_LAT, lng=_REMOTE_LNG)

        best, score, debug = _select_anchor_candidate(
            [local, remote], target, None, None
        )

        assert debug["anchorHasTargetCoords"] is False
        assert debug["anchorGeoRadiusKm"] is None
        assert debug["anchorCandidatesAfterGeo"] == 2  # both compete


# ---------------------------------------------------------------------------
# C. Geo radius fallback
# ---------------------------------------------------------------------------

class TestGeoRadiusFallback:

    def test_fallback_triggered_when_tight_radius_too_sparse(self):
        """
        If only 1 candidate is within _ANCHOR_RADIUS_TIGHT_KM (< _ANCHOR_MIN_GEO_CANDIDATES=2),
        the fallback radius is used so the edge candidate (Hayward, 23.8 km) is included.
        """
        target = _target()

        # Only one candidate inside 20 km — below _ANCHOR_MIN_GEO_CANDIDATES
        single_local = _spec(7001, lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG)  # 6.5 km
        edge = _spec(7002, bedrooms=2, lat=_EDGE_LAT, lng=_EDGE_LNG)    # 23.8 km — inside 40 km only

        best, score, debug = _select_anchor_candidate(
            [single_local, edge], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=2,
        )

        # Fallback radius should have been triggered
        assert debug["anchorGeoFallback"] is True
        assert debug["anchorGeoRadiusKm"] == _ANCHOR_RADIUS_FALLBACK_KM
        # Both candidates are within 40 km
        assert debug["anchorCandidatesAfterGeo"] == 2

    def test_tight_radius_sufficient_no_fallback(self):
        """When at least _ANCHOR_MIN_GEO_CANDIDATES are within tight radius,
        no fallback occurs.  Hayward (23.8 km) is excluded by the tight filter."""
        target = _target()

        local_a = _spec(8001, lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG)   # 6.5 km  — inside 20 km
        local_b = _spec(8002, lat=_LOCAL_B_LAT, lng=_LOCAL_B_LNG)   # 5.2 km  — inside 20 km
        edge    = _spec(8003, lat=_EDGE_LAT, lng=_EDGE_LNG)          # 23.8 km — outside 20 km

        best, score, debug = _select_anchor_candidate(
            [local_a, local_b, edge], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=3,
        )

        assert debug["anchorGeoFallback"] is False
        assert debug["anchorGeoRadiusKm"] == _ANCHOR_RADIUS_TIGHT_KM
        # Only local_a and local_b survive the tight filter
        assert debug["anchorCandidatesAfterGeo"] == 2


# ---------------------------------------------------------------------------
# D. Full-address coords are a stronger anchor
# ---------------------------------------------------------------------------

class TestTargetCoordsSource:

    def test_precise_coords_exclude_far_candidates(self):
        """
        When the target has precise coords (e.g. from full-address geocode),
        the geo filter correctly excludes listings 86 km away even if they
        are a perfect structural match.
        """
        target = _target(bedrooms=3, baths=2.0, accommodates=6)

        local   = _spec(9001, bedrooms=3, baths=2.0, accommodates=6,
                         lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG)
        distant = _spec(9002, bedrooms=3, baths=2.0, accommodates=6,
                         lat=_REMOTE_LAT, lng=_REMOTE_LNG)

        best, score, debug = _select_anchor_candidate(
            [local, distant], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=2,
        )

        assert best.url == local.url
        assert debug["anchorHasTargetCoords"] is True
        assert debug["anchorDistanceKm"] < _ANCHOR_RADIUS_TIGHT_KM

    def test_debug_has_all_required_keys(self):
        """Ensure all documented debug keys are present in the output."""
        target = _target()
        cand = _spec(9999, lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG)

        _, _, debug = _select_anchor_candidate(
            [cand], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=1,
            target_city="Belmont",
            target_state="CA",
        )

        required_keys = {
            "anchorCandidatesBeforeGeo",
            "anchorCandidatesAfterGeo",
            "anchorGeoRadiusKm",
            "anchorGeoFallback",
            "anchorGeoSkipped",
            "anchorStructuralScore",
            "anchorDistanceKm",
            "anchorHasTargetCoords",
            "anchorSelectionMode",
            "anchorProxyCoordsAssigned",
            "anchorLocationBuckets",
            "anchorLocationBucket",
            "anchorFailSafeTriggered",
            "anchorNearbyExpansionUsed",
            "anchorAllowedNearbyCities",
            "anchorTargetCityOnlyCount",
            "anchorNearbyMarketCount",
            "targetLocationConfidence",
            "targetCanonicalCity",
            "targetCanonicalState",
            # Location explainability (Phase 4 additions)
            "anchorRawLocation",
            "anchorNormalizedLocation",
            "anchorNormalizationNotes",
            "anchorClusterId",
        }
        missing = required_keys - debug.keys()
        assert not missing, f"Missing debug keys: {missing}"

    def test_debug_location_bucket_keys_include_regional(self):
        """anchorLocationBuckets must include the regional_mismatch key."""
        target = _target()
        cand = _spec(9998, lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG)

        _, _, debug = _select_anchor_candidate(
            [cand], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=1,
        )

        assert "regional_mismatch" in debug["anchorLocationBuckets"], (
            "anchorLocationBuckets must include regional_mismatch key"
        )


# ---------------------------------------------------------------------------
# E. Path A vs Path B: listing coords vs city-proxy (via n_listing_coords)
# ---------------------------------------------------------------------------

class TestSelectionModeRouting:

    def test_path_a_used_when_listing_coords_present(self):
        """When n_listing_coords > 0, Path A (listing coords) is used."""
        target = _target()
        cand = _spec(10001, lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG)

        _, _, debug = _select_anchor_candidate(
            [cand], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=1,
        )

        assert debug["anchorSelectionMode"] == "listing_coords"

    def test_path_b_attempted_when_no_listing_coords(self):
        """
        When n_listing_coords == 0 and target has coords, Path B is tried.
        With no location text on candidates, geocoding yields nothing, so
        selection_mode falls through to text_bucket.
        """
        target = _target()
        # Candidates have no coords and no location text
        cand_a = _spec(10002, bedrooms=2)
        cand_b = _spec(10003, bedrooms=3)

        _, _, debug = _select_anchor_candidate(
            [cand_a, cand_b], target, TARGET_LAT, TARGET_LNG,
            n_listing_coords=0,
            target_city="Belmont",
            target_state="CA",
        )

        # Geocoding location="" → nothing; falls to text_bucket
        # text_bucket with no state info → unknown → all pass through
        assert debug["anchorSelectionMode"] in ("text_bucket", "city_proxy")
        assert debug["anchorProxyCoordsAssigned"] == 0

    def test_no_coords_no_city_is_no_geo_mode(self):
        """With no target coords and no city, no geo or text filtering occurs."""
        target = _target()
        cand_a = _spec(10004, bedrooms=2)
        cand_b = _spec(10005, bedrooms=3)

        _, _, debug = _select_anchor_candidate(
            [cand_a, cand_b], target, None, None,
            n_listing_coords=0,
        )

        assert debug["anchorHasTargetCoords"] is False
        assert debug["anchorSelectionMode"] == "no_geo"
        assert debug["anchorCandidatesAfterGeo"] == 2  # no filtering


# ---------------------------------------------------------------------------
# F. Path C: text-bucket classification (5-bucket system)
# ---------------------------------------------------------------------------

class TestTextBucketFilter:
    """
    Tests for Path C (text-bucket) when no coords are available.
    Target: city="Belmont", state="CA" (no lat/lng so geo filter cannot fire).

    Key upgrade: same-state is no longer automatically "nearby_market".
    Cities in a different metro cluster (SF, Sonoma, Oakland) are now
    "regional_mismatch", while Peninsula siblings (Redwood City, San Mateo)
    remain "nearby_market".
    """

    def _run(
        self,
        candidates,
        target_city="Belmont",
        target_state="CA",
        addr_confidence="high",
    ):
        target = _target()
        return _select_anchor_candidate(
            candidates, target, None, None,
            target_city=target_city,
            target_state=target_state,
            n_listing_coords=0,
            addr_confidence=addr_confidence,
        )

    # -- far_mismatch (cross-state) excluded at all confidence levels

    def test_far_mismatch_excluded_high_confidence(self):
        """Cross-state candidates excluded regardless of confidence."""
        local = _spec(11001, location="Belmont, California",  bedrooms=2, baths=1.0)
        far   = _spec(11002, location="Portland, Oregon",     bedrooms=2, baths=1.0)

        best, score, debug = self._run([local, far], addr_confidence="high")

        assert best.url == local.url
        assert debug["anchorLocationBuckets"]["far_mismatch"] == 1
        assert debug["anchorLocationBuckets"]["local_match"] == 1
        assert debug["anchorCandidatesAfterGeo"] == 1

    def test_far_mismatch_excluded_low_confidence(self):
        """Cross-state candidates excluded even for low-confidence targets."""
        local = _spec(11101, location="Belmont, California",  bedrooms=2)
        far   = _spec(11102, location="Portland, Oregon",     bedrooms=2)

        best, score, debug = self._run([local, far], addr_confidence="low")

        assert best.url == local.url
        assert debug["anchorLocationBuckets"]["far_mismatch"] == 1

    # -- nearby_market (same Peninsula cluster)

    def test_nearby_market_survives(self):
        """Peninsula siblings survive the text-bucket filter."""
        nearby = _spec(12001, location="Redwood City, California", bedrooms=2)
        far    = _spec(12002, location="Austin, Texas",            bedrooms=2)

        best, score, debug = self._run([nearby, far])

        assert best.url == nearby.url
        assert debug["anchorLocationBuckets"]["nearby_market"] == 1
        assert debug["anchorLocationBuckets"]["far_mismatch"] == 1

    # -- regional_mismatch (same state, different cluster)

    def test_regional_excluded_for_high_confidence(self):
        """SF and Sonoma are regional_mismatch and excluded for high-confidence targets."""
        nearby    = _spec(20001, location="Redwood City, California", bedrooms=2)
        regional1 = _spec(20002, location="San Francisco, California", bedrooms=2)
        regional2 = _spec(20003, location="Sonoma, California",        bedrooms=2)

        best, score, debug = self._run([nearby, regional1, regional2], addr_confidence="high")

        assert best.url == nearby.url
        assert debug["anchorLocationBuckets"]["nearby_market"] == 1
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 2
        # High confidence: regional excluded → only 1 survives
        assert debug["anchorCandidatesAfterGeo"] == 1

    def test_regional_allowed_for_medium_confidence(self):
        """
        For medium-confidence with no nearby candidates, regional_mismatch is
        used as fallback. When nearby IS present, nearby wins outright (regional
        is de-prioritized regardless of confidence level).
        """
        regional_only = _spec(20101, location="San Francisco, California", bedrooms=2)

        best, score, debug = self._run([regional_only], addr_confidence="medium")

        # Regional is the fallback — no fail-safe since medium conf accepts regional
        assert best.url == regional_only.url
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 1
        assert debug["anchorCandidatesAfterGeo"] == 1
        assert debug["anchorFailSafeTriggered"] is False

    def test_regional_allowed_for_low_confidence(self):
        """For low-confidence, regional_mismatch candidates are included."""
        regional = _spec(20201, location="San Francisco, California", bedrooms=2)
        far      = _spec(20202, location="Austin, Texas",             bedrooms=2)

        best, score, debug = self._run([regional, far], addr_confidence="low")

        # regional survives; far is excluded
        assert best.url == regional.url
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 1
        assert debug["anchorLocationBuckets"]["far_mismatch"] == 1

    # -- local > nearby > regional ranking

    def test_local_beats_nearby(self):
        """local_match is preferred over nearby_market when structural score is equal."""
        target_spec = _target(bedrooms=2, baths=1.0, accommodates=4)
        local  = _spec(13001, location="Belmont, CA",       bedrooms=2, baths=1.0, accommodates=4)
        nearby = _spec(13002, location="San Mateo, CA",     bedrooms=2, baths=1.0, accommodates=4)

        best, score, debug = _select_anchor_candidate(
            [local, nearby], target_spec, None, None,
            target_city="Belmont", target_state="CA",
            n_listing_coords=0,
            addr_confidence="high",
        )

        assert best.url == local.url
        assert debug["anchorLocationBuckets"]["local_match"] == 1
        assert debug["anchorLocationBuckets"]["nearby_market"] == 1

    def test_nearby_beats_regional_when_no_local(self):
        """
        Without a local_match, nearby_market is preferred over regional_mismatch
        for any confidence level where both survive.
        With high confidence, regional is excluded so nearby trivially wins.
        """
        nearby   = _spec(13101, location="Redwood City, California",
                          bedrooms=2, baths=1.0, accommodates=4)
        regional = _spec(13102, location="San Francisco, California",
                          bedrooms=2, baths=1.0, accommodates=4)

        best, score, debug = _select_anchor_candidate(
            [nearby, regional], _target(), None, None,
            target_city="Belmont", target_state="CA",
            n_listing_coords=0,
            addr_confidence="high",
        )

        # High confidence: regional excluded, so nearby wins
        assert best.url == nearby.url
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 1
        assert debug["anchorLocationBuckets"]["nearby_market"] == 1

    # -- fail-safe triggers

    def test_fail_safe_when_all_far_mismatch(self):
        """If ALL candidates are cross-state, fail-safe uses them anyway."""
        cand_a = _spec(14001, location="Portland, Oregon",  bedrooms=2)
        cand_b = _spec(14002, location="Austin, Texas",     bedrooms=2)

        best, score, debug = self._run([cand_a, cand_b])

        assert best is not None
        assert debug["anchorFailSafeTriggered"] is True
        assert debug["anchorLocationBuckets"]["far_mismatch"] == 2

    def test_fail_safe_when_high_confidence_only_regional(self):
        """
        High-confidence target with only regional candidates triggers fail-safe
        (preferred = local + nearby + unknown = all empty).
        """
        regional_a = _spec(14101, location="San Francisco, California", bedrooms=2)
        regional_b = _spec(14102, location="Sonoma, California",        bedrooms=2)

        best, score, debug = self._run([regional_a, regional_b], addr_confidence="high")

        assert best is not None
        assert debug["anchorFailSafeTriggered"] is True
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 2
        assert debug["anchorLocationBuckets"]["local_match"] == 0
        assert debug["anchorLocationBuckets"]["nearby_market"] == 0

    # -- unknown location passes through

    def test_unknown_location_passes_through(self):
        """Candidates with empty location (unknown) are never excluded."""
        target = _target()
        no_loc_a = _spec(15001, bedrooms=2)  # location="" → unknown
        no_loc_b = _spec(15002, bedrooms=3)

        best, score, debug = _select_anchor_candidate(
            [no_loc_a, no_loc_b], target, None, None,
            target_city="Belmont", target_state="CA",
            n_listing_coords=0,
            addr_confidence="high",
        )

        assert debug["anchorLocationBuckets"]["unknown"] == 2
        assert debug["anchorCandidatesAfterGeo"] == 2
        assert best is not None

    # -- anchorLocationBucket of selected anchor

    def test_anchor_bucket_reported_correctly(self):
        """debug['anchorLocationBucket'] should reflect the selected anchor's bucket."""
        local  = _spec(16001, location="Belmont, CA",       bedrooms=2)
        nearby = _spec(16002, location="San Mateo, CA",     bedrooms=2)

        best, score, debug = self._run([local, nearby], addr_confidence="high")

        assert best.url == local.url
        assert debug["anchorLocationBucket"] == "local_match"

    def test_anchor_bucket_nearby_when_no_local(self):
        nearby = _spec(16101, location="Redwood City, California", bedrooms=2)

        best, score, debug = self._run([nearby], addr_confidence="high")

        assert debug["anchorLocationBucket"] == "nearby_market"

    # -- targetLocationConfidence propagated

    def test_confidence_reflected_in_debug(self):
        cand = _spec(17001, location="Belmont, CA", bedrooms=2)

        _, _, debug = self._run([cand], addr_confidence="medium")

        assert debug["targetLocationConfidence"] == "medium"
        assert debug["targetCanonicalCity"] == "belmont"
        assert debug["targetCanonicalState"] == "CA"


# ---------------------------------------------------------------------------
# G. Bay Area nearby / regional distinction (integration-style Path C)
# ---------------------------------------------------------------------------

class TestBayAreaBuckets:
    """
    Verifies the key product requirement: Peninsula neighbours are acceptable
    anchors for a Belmont target; SF and Sonoma are not.

    These tests use Path C (no coords) so the metro-cluster classification is
    the sole filtering mechanism.  Path B (city-proxy geocoding) handles the
    same distinction by distance when Nominatim is available.
    """

    TARGET_CITY = "Belmont"
    TARGET_STATE = "CA"

    def _run(self, candidates, addr_confidence="high"):
        target = _target()
        return _select_anchor_candidate(
            candidates, target, None, None,
            target_city=self.TARGET_CITY,
            target_state=self.TARGET_STATE,
            n_listing_coords=0,
            addr_confidence=addr_confidence,
        )

    def _cand(self, room_id: int, location: str, bedrooms: int = 2, baths: float = 1.0):
        return _spec(room_id, location=location, bedrooms=bedrooms, baths=baths)

    def test_peninsula_siblings_accepted_high_conf(self):
        """Redwood City and San Carlos are nearby_market → accepted for high-conf."""
        rc = self._cand(21001, "Redwood City, California")
        sc = self._cand(21002, "San Carlos, California")
        sf = self._cand(21003, "San Francisco, California")
        sn = self._cand(21004, "Sonoma, California")

        best, score, debug = self._run([rc, sc, sf, sn], addr_confidence="high")

        assert debug["anchorLocationBuckets"]["nearby_market"] == 2
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 2
        # High conf: only nearby survives
        assert debug["anchorCandidatesAfterGeo"] == 2
        assert best.url in {rc.url, sc.url}

    def test_sf_and_sonoma_not_equal_to_redwood_city(self):
        """
        Verifying the core requirement: SF and Sonoma must be in a weaker bucket
        than Redwood City.  With high confidence they should be completely
        excluded from the anchor pool.
        """
        rc = self._cand(22001, "Redwood City, California")
        sf = self._cand(22002, "San Francisco, California")
        sn = self._cand(22003, "Sonoma, California")

        best, score, debug = self._run([rc, sf, sn])

        # Only Redwood City survives the high-conf filter
        assert best.url == rc.url
        assert debug["anchorLocationBuckets"]["nearby_market"] == 1
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 2

    def test_belmont_candidate_beats_redwood_city_on_perfect_match(self):
        """local_match (Belmont) wins over nearby_market (Redwood City)."""
        belmont = self._cand(23001, "Belmont, California",      bedrooms=2, baths=1.0)
        rc      = self._cand(23002, "Redwood City, California", bedrooms=2, baths=1.5)

        best, score, debug = self._run([rc, belmont])

        assert best.url == belmont.url
        assert debug["anchorLocationBuckets"]["local_match"] == 1
        assert debug["anchorLocationBuckets"]["nearby_market"] == 1

    def test_high_conf_only_sf_sonoma_triggers_fail_safe(self):
        """
        (Test D from the requirements)
        When only SF / Sonoma candidates exist for a high-confidence Belmont target,
        fail-safe should trigger — not silently pick an inappropriate anchor.
        """
        sf = self._cand(24001, "San Francisco, California")
        sn = self._cand(24002, "Sonoma, California")

        best, score, debug = self._run([sf, sn], addr_confidence="high")

        assert debug["anchorFailSafeTriggered"] is True
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 2
        assert debug["anchorLocationBuckets"]["nearby_market"] == 0
        # Still returns something (not None)
        assert best is not None

    def test_low_conf_broader_pool_with_degraded_flag(self):
        """
        (Test E from the requirements)
        Low-confidence target: nearby wins when present. Regional is only used
        as a fallback when no nearby/local exists. Debug shows confidence level
        so callers can detect degraded mode.
        """
        nearby   = self._cand(25001, "Redwood City, California")
        regional = self._cand(25002, "San Francisco, California")
        far      = self._cand(25003, "Portland, Oregon")

        best, score, debug = self._run(
            [nearby, regional, far], addr_confidence="low"
        )

        # nearby wins; regional and far are de-prioritized (nearby_market present)
        assert best.url == nearby.url
        assert debug["anchorCandidatesAfterGeo"] == 1   # pool = [nearby] only
        assert debug["anchorLocationBuckets"]["nearby_market"] == 1
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 1
        assert debug["anchorLocationBuckets"]["far_mismatch"] == 1
        # Debug shows degraded confidence
        assert debug["targetLocationConfidence"] == "low"
        assert debug["anchorFailSafeTriggered"] is False


# ---------------------------------------------------------------------------
# H. Controlled nearby-market expansion (the new priority-layering logic)
# ---------------------------------------------------------------------------

class TestControlledExpansion:
    """
    Tests for the four-phase pipeline's priority-layered expansion:
      Priority 1: target-city candidates (local_match)
      Priority 2: approved nearby market (nearby_market)
      Priority 3: confidence-gated fallback (fail-safe for high conf)

    All these tests use Path C (no coords) so bucket classification is the
    sole filter, making the expansion logic observable and testable.

    Target: Belmont, CA — high confidence throughout unless stated.
    Approved nearby cities: Redwood City, San Carlos, San Mateo, Palo Alto, etc.
    Not approved: San Francisco (CA:bay_sf), Sonoma (CA:wine_country).
    """

    def _run(
        self,
        candidates,
        target_city="Belmont",
        target_state="CA",
        addr_confidence="high",
    ):
        return _select_anchor_candidate(
            candidates, _target(), None, None,
            target_city=target_city,
            target_state=target_state,
            n_listing_coords=0,
            addr_confidence=addr_confidence,
        )

    # ── Test A: target city found → no expansion ─────────────────────────────

    def test_a_local_found_no_expansion(self):
        """
        Candidates: Belmont (local), Redwood City (nearby), SF (regional).
        Expected: Belmont wins; expansion not used.
        """
        belmont = _spec(30001, location="Belmont, California",      bedrooms=2, baths=1.0)
        rc      = _spec(30002, location="Redwood City, California", bedrooms=2, baths=1.0)
        sf      = _spec(30003, location="San Francisco, California", bedrooms=2, baths=1.0)

        best, score, debug = self._run([belmont, rc, sf])

        assert best.url == belmont.url
        assert debug["anchorNearbyExpansionUsed"] is False
        assert debug["anchorTargetCityOnlyCount"] == 1
        assert debug["anchorNearbyMarketCount"] == 1
        # Pool was restricted to Belmont (+ unknowns) → only 1 candidate in pool
        # (SF excluded by high-conf; RC excluded by local-only selection)
        assert debug["anchorCandidatesAfterGeo"] == 1

    def test_a_local_beats_structurally_perfect_nearby(self):
        """
        Even if a nearby-city candidate is a perfect structural match,
        local_match candidate wins when present.
        """
        belmont_imperfect = _spec(30101, location="Belmont, CA",       bedrooms=2, baths=1.5)
        rc_perfect        = _spec(30102, location="Redwood City, CA",  bedrooms=2, baths=1.0)

        best, score, debug = self._run([belmont_imperfect, rc_perfect])

        assert best.url == belmont_imperfect.url
        assert debug["anchorNearbyExpansionUsed"] is False

    # ── Test B: target city missing, nearby available → controlled expansion ──

    def test_b_expansion_to_nearby_when_no_local(self):
        """
        Candidates: Redwood City (nearby), San Carlos (nearby), SF (regional).
        Expected: expansion triggered; RC or SC wins; SF excluded (high conf).
        """
        rc = _spec(31001, location="Redwood City, California", bedrooms=2)
        sc = _spec(31002, location="San Carlos, California",   bedrooms=2)
        sf = _spec(31003, location="San Francisco, California", bedrooms=2)

        best, score, debug = self._run([rc, sc, sf])

        assert debug["anchorNearbyExpansionUsed"] is True
        assert debug["anchorTargetCityOnlyCount"] == 0
        assert debug["anchorNearbyMarketCount"] == 2
        assert best.url in {rc.url, sc.url}
        # SF excluded from pool (high conf, regional mismatch)
        assert debug["anchorCandidatesAfterGeo"] == 2

    def test_b_allowed_nearby_cities_in_debug(self):
        """anchorAllowedNearbyCities must include Peninsula siblings."""
        rc = _spec(31101, location="Redwood City, California", bedrooms=2)

        _, _, debug = self._run([rc])

        allowed = debug["anchorAllowedNearbyCities"]
        assert "redwood city" in allowed
        assert "san carlos" in allowed
        assert "san mateo" in allowed
        # Must NOT include SF or Sonoma
        assert "san francisco" not in allowed
        assert "sonoma" not in allowed

    def test_b_expansion_uses_approved_list(self):
        """Expansion only goes to cities in the same cluster (approved nearby)."""
        sc = _spec(31201, location="San Carlos, California",   bedrooms=2)
        sf = _spec(31202, location="San Francisco, California", bedrooms=2)

        best, score, debug = self._run([sc, sf])

        # San Carlos approved (nearby_market); SF rejected (regional, high conf)
        assert best.url == sc.url
        assert debug["anchorNearbyExpansionUsed"] is True

    # ── Test C: only regional/far → fail-safe ────────────────────────────────

    def test_c_fail_safe_only_regional(self):
        """
        High-confidence Belmont target with ONLY SF and Sonoma candidates.
        Expected: fail-safe triggered; expansion was attempted and found nothing.
        """
        sf = _spec(32001, location="San Francisco, California", bedrooms=2)
        sn = _spec(32002, location="Sonoma, California",        bedrooms=2)

        best, score, debug = self._run([sf, sn], addr_confidence="high")

        assert debug["anchorFailSafeTriggered"] is True
        assert debug["anchorNearbyExpansionUsed"] is True   # expansion attempted
        assert debug["anchorTargetCityOnlyCount"] == 0
        assert debug["anchorNearbyMarketCount"] == 0
        # Still returns something
        assert best is not None

    def test_c_fail_safe_only_far(self):
        """All cross-state candidates → fail-safe at any confidence level."""
        portland = _spec(32101, location="Portland, Oregon", bedrooms=2)
        austin   = _spec(32102, location="Austin, Texas",    bedrooms=2)

        best, score, debug = self._run([portland, austin], addr_confidence="high")

        assert debug["anchorFailSafeTriggered"] is True
        assert best is not None

    # ── Test D: low-confidence target → degraded fallback ────────────────────

    def test_d_low_conf_allows_regional(self):
        """
        Low-confidence target: when no local/nearby, regional_mismatch is
        accepted as a degraded fallback (instead of fail-safe).
        Debug must clearly show the degraded mode.
        """
        sf = _spec(33001, location="San Francisco, California", bedrooms=2)
        sn = _spec(33002, location="Sonoma, California",        bedrooms=2)

        best, score, debug = self._run([sf, sn], addr_confidence="low")

        # For low conf: regional is accepted, no fail-safe
        assert debug["anchorFailSafeTriggered"] is False
        assert debug["targetLocationConfidence"] == "low"
        assert debug["anchorNearbyExpansionUsed"] is True
        assert best is not None

    def test_d_low_conf_prefers_nearby_over_regional(self):
        """Even with low confidence, nearby beats regional when both available."""
        rc = _spec(33101, location="Redwood City, California",  bedrooms=2)
        sf = _spec(33102, location="San Francisco, California", bedrooms=2)

        best, score, debug = self._run([rc, sf], addr_confidence="low")

        # Redwood City is nearby_market → wins over SF (regional_mismatch)
        # even though both survive in low-conf mode
        assert best.url == rc.url
        assert debug["anchorNearbyExpansionUsed"] is True

    # ── Test E: Path B still primary (proxy geocoding overrides text rules) ───

    def test_e_path_b_is_primary_over_text_bucket(self):
        """
        When proxy geocoding succeeds (n_listing_coords=0, target has coords,
        candidates get proxy coords), Path B filters by distance and sets
        selection_mode='city_proxy'.  Text-bucket expansion rules do NOT
        change the geo-filtered pool (they only re-order by priority within it).
        """
        # Candidates with real WGS-84 coords (already assigned) — simulate
        # the outcome of a successful Path B geocoding session by passing
        # n_listing_coords=1 (so Path A runs on these pre-assigned coords).
        local_listing = _spec(
            34001,
            location="Belmont, California",
            lat=TARGET_LAT + 0.001,   # ~0.1 km from target
            lng=TARGET_LNG,
        )
        remote_listing = _spec(
            34002,
            location="Sonoma, California",
            lat=_REMOTE_LAT,
            lng=_REMOTE_LNG,   # 87 km — outside tight radius
        )

        best, score, debug = _select_anchor_candidate(
            [local_listing, remote_listing],
            _target(),
            TARGET_LAT, TARGET_LNG,
            target_city="Belmont",
            target_state="CA",
            n_listing_coords=2,   # Path A (listing coords)
            addr_confidence="high",
        )

        # Path A (geo) should exclude Sonoma (87 km); local Belmont listing wins
        assert best.url == local_listing.url
        assert debug["anchorSelectionMode"] == "listing_coords"
        # Nearby expansion not needed — local listing found within geo radius
        assert debug["anchorNearbyExpansionUsed"] is False

    # ── Test F: cluster mapping verified for expansion ────────────────────────

    def test_f_peninsula_cluster_approved(self):
        """All Peninsula cities are approved nearby for Belmont."""
        for city, location_str in [
            ("Redwood City", "Redwood City, California"),
            ("San Carlos",   "San Carlos, California"),
            ("San Mateo",    "San Mateo, California"),
            ("Palo Alto",    "Palo Alto, California"),
            ("Burlingame",   "Burlingame, California"),
        ]:
            cand = _spec(35000 + hash(city) % 1000, location=location_str, bedrooms=2)
            best, score, debug = self._run([cand])
            assert debug["anchorNearbyExpansionUsed"] is True, (
                f"{city} should trigger expansion (no local found)"
            )
            assert debug["anchorLocationBucket"] == "nearby_market", (
                f"{city} should be nearby_market, not {debug['anchorLocationBucket']}"
            )

    def test_f_non_cluster_city_is_regional_not_nearby(self):
        """Cities outside the Peninsula cluster are regional for Belmont."""
        for city, location_str in [
            ("San Francisco", "San Francisco, California"),
            ("Sonoma",        "Sonoma, California"),
            ("Oakland",       "Oakland, California"),
            ("San Jose",      "San Jose, California"),
        ]:
            cand = _spec(36000 + hash(city) % 1000, location=location_str, bedrooms=2)
            best, score, debug = self._run([cand], addr_confidence="high")
            # High conf: regional excluded → fail-safe
            assert debug["anchorFailSafeTriggered"] is True, (
                f"{city} should trigger fail-safe (regional, high conf)"
            )

    def test_f_unknown_market_conservative_fallback(self):
        """
        Target city not in any cluster → allowed_nearby_cities is empty.
        Expansion still works but treats ALL same-state cities as regional.
        """
        target_city = "Fresno"  # not in any cluster
        rc = _spec(37001, location="Redwood City, California", bedrooms=2)

        best, score, debug = _select_anchor_candidate(
            [rc], _target(), None, None,
            target_city=target_city,
            target_state="CA",
            n_listing_coords=0,
            addr_confidence="high",
        )

        # Fresno has no cluster → allowed_nearby_cities empty
        assert debug["anchorAllowedNearbyCities"] == []
        # Redwood City classified relative to Fresno: same state, no cluster match
        # → regional_mismatch → fail-safe for high conf
        assert debug["anchorFailSafeTriggered"] is True


# ---------------------------------------------------------------------------
# I. Location normalisation in the full pipeline
# ---------------------------------------------------------------------------

class TestLocationNormalisationInPipeline:
    """
    End-to-end verification that fuzzy location normalisation (prefix stripping,
    neighbourhood aliases) works inside _select_anchor_candidate.

    These tests use Path C (no coords) so normalisation is the sole filter
    mechanism — results are deterministic and network-free.
    """

    def _run(
        self,
        candidates,
        target_city="Belmont",
        target_state="CA",
        addr_confidence="high",
    ):
        return _select_anchor_candidate(
            candidates, _target(), None, None,
            target_city=target_city,
            target_state=target_state,
            n_listing_coords=0,
            addr_confidence=addr_confidence,
        )

    def test_downtown_belmont_is_local_match(self):
        """
        'Downtown Belmont, CA' normalises to 'Belmont, CA' → local_match,
        so it is preferred over a nearby-market candidate.
        """
        downtown = _spec(40001, location="Downtown Belmont, CA",       bedrooms=2)
        rc       = _spec(40002, location="Redwood City, California",   bedrooms=2)

        best, score, debug = self._run([downtown, rc])

        assert best.url == downtown.url
        assert debug["anchorLocationBucket"] == "local_match"
        assert debug["anchorNearbyExpansionUsed"] is False

    def test_downtown_sf_is_regional_not_unknown(self):
        """
        Without normalisation 'Downtown San Francisco, CA' would parse as city
        'downtown san francisco' (no cluster → regional_mismatch).  With
        normalisation it reliably maps to 'san francisco' → regional_mismatch.
        Either way regional, but the bucket now comes from the cluster lookup
        rather than the no-cluster fallback.
        """
        sf = _spec(40101, location="Downtown San Francisco, California", bedrooms=2)

        best, score, debug = self._run([sf], addr_confidence="low")

        # regional_mismatch (confirmed via cluster) or regional (via no-cluster):
        # both are regional — the key is it must NOT be "unknown"
        assert debug["anchorLocationBucket"] != "unknown"
        assert debug["anchorLocationBucket"] in ("regional_mismatch", "far_mismatch")

    def test_near_redwood_city_is_nearby(self):
        """
        'Near Redwood City, California' normalises → 'Redwood City, California'
        → CA:bay_peninsula → nearby_market for a Belmont target.
        """
        near_rc = _spec(40201, location="Near Redwood City, California", bedrooms=2)

        best, score, debug = self._run([near_rc])

        assert debug["anchorLocationBucket"] == "nearby_market"
        assert debug["anchorNearbyExpansionUsed"] is True

    def test_san_mateo_county_is_nearby(self):
        """
        'San Mateo County, CA' → 'San Mateo, CA' → CA:bay_peninsula
        → nearby_market for Belmont.
        """
        county = _spec(40301, location="San Mateo County, CA", bedrooms=2)

        best, score, debug = self._run([county])

        assert debug["anchorLocationBucket"] == "nearby_market"

    def test_debug_raw_and_normalised_location_present(self):
        """anchorRawLocation and anchorNormalizedLocation are populated."""
        downtown = _spec(40401, location="Downtown Belmont, CA", bedrooms=2)

        _, _, debug = self._run([downtown])

        assert debug["anchorRawLocation"] == "Downtown Belmont, CA"
        assert debug["anchorNormalizedLocation"] == "Belmont, CA"
        assert debug["anchorNormalizationNotes"] is not None
        assert "prefix:downtown" in debug["anchorNormalizationNotes"]

    def test_debug_cluster_id_populated(self):
        """anchorClusterId reflects the selected anchor's metro cluster."""
        rc = _spec(40501, location="Redwood City, California", bedrooms=2)

        _, _, debug = self._run([rc], addr_confidence="high")

        # Redwood City → CA:bay_peninsula
        assert debug["anchorClusterId"] == "CA:bay_peninsula"

    def test_san_jose_vs_gilroy_in_pipeline(self):
        """
        Gilroy candidate must be regional_mismatch (not nearby_market) for a
        San Jose target after the CA:bay_south split.
        """
        gilroy = _spec(40601, location="Gilroy, California",  bedrooms=2)
        sj     = _spec(40602, location="San Jose, California", bedrooms=2)

        best, score, debug = _select_anchor_candidate(
            [gilroy, sj], _target(), None, None,
            target_city="San Jose",
            target_state="CA",
            n_listing_coords=0,
            addr_confidence="high",
        )

        # San Jose is local_match → must win over Gilroy (regional)
        assert best.url == sj.url
        assert debug["anchorLocationBuckets"]["local_match"] == 1
        assert debug["anchorLocationBuckets"]["regional_mismatch"] == 1


# ---------------------------------------------------------------------------
# Tests for infer_canonical_target_from_candidates()
# ---------------------------------------------------------------------------


class TestInferCanonicalTarget:
    """Unit tests for anchor_location.infer_canonical_target_from_candidates."""

    def _infer(self, specs, fallback_city=None, fallback_state=None, **kw):
        from worker.core.anchor_location import infer_canonical_target_from_candidates
        return infer_canonical_target_from_candidates(
            specs,
            fallback_city=fallback_city,
            fallback_state=fallback_state,
            **kw,
        )

    # ── happy path ───────────────────────────────────────────────────────────

    def test_clear_majority_returns_winner(self):
        """6 SF candidates, no coords → vote stage → airbnb_first_page_vote."""
        specs = [_spec(i, location="San Francisco, CA") for i in range(60001, 60007)]
        city, state, source = self._infer(specs)
        assert city == "san francisco"
        assert state == "CA"
        assert source == "airbnb_first_page_vote"

    def test_plurality_above_threshold(self):
        """5 SF + 2 Oakland, no coords → vote stage → SF wins (5/7 = 71 %)."""
        sf  = [_spec(i, location="San Francisco, CA") for i in range(60101, 60106)]
        oak = [_spec(i, location="Oakland, CA")       for i in range(60106, 60108)]
        city, state, source = self._infer(sf + oak)
        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"

    def test_normalisation_applied_before_vote(self):
        """'Downtown San Francisco, CA' normalises and counts toward san francisco."""
        specs = [
            _spec(60201, location="Downtown San Francisco, CA"),
            _spec(60202, location="San Francisco, CA"),
            _spec(60203, location="San Francisco, California"),
        ]
        city, state, source = self._infer(specs)
        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"

    # ── inconclusive vote → fallback ─────────────────────────────────────────

    def test_tie_below_threshold_uses_fallback(self):
        """2 SF + 2 Oakland = 50 % each — below default 40 % min per winner.
        Wait, 50 % >= 40 %, so the plurality winner (whichever comes first) wins.
        Use a 3-way split to go below threshold instead."""
        specs = [
            _spec(60301, location="San Francisco, CA"),
            _spec(60302, location="Oakland, CA"),
            _spec(60303, location="San Jose, CA"),
        ]
        # Each 1/3 ≈ 33 % < 40 % → fallback
        city, state, source = self._infer(
            specs, fallback_city="belmont", fallback_state="CA"
        )
        assert source == "fallback"
        assert city == "belmont"
        assert state == "CA"

    def test_empty_locations_all_fall_back(self):
        """All candidates have empty location → no non-empty strings → no_candidates."""
        specs = [_spec(i) for i in range(60401, 60404)]
        city, state, source = self._infer(
            specs, fallback_city="belmont", fallback_state="CA"
        )
        assert source == "no_candidates"   # distinct from no_parseable_candidates
        assert city == "belmont"
        assert state == "CA"

    def test_empty_candidate_list(self):
        """Empty list → no_candidates, returns fallback values."""
        city, state, source = self._infer(
            [], fallback_city="belmont", fallback_state="CA"
        )
        assert source == "no_candidates"
        assert city == "belmont"

    def test_no_parseable_candidates_distinct_from_no_candidates(self):
        """Non-empty location strings that lack state → no_parseable_candidates,
        NOT no_candidates — distinguishes 'had data but couldn't parse' from
        'had no location strings at all'."""
        specs = [
            _spec(60450, location="Belmont"),         # city only, no state
            _spec(60451, location="San Francisco"),    # city only, no state
        ]
        city, state, source = self._infer(
            specs, fallback_city="belmont", fallback_state="CA"
        )
        assert source == "no_parseable_candidates"
        assert city == "belmont"   # fallback returned

    # ── mixed parseable / unparseable ────────────────────────────────────────

    def test_unparseable_locations_skipped(self):
        """State-less locations don't dilute the vote; parseable ones win."""
        specs = [
            _spec(60501, location="San Francisco, CA"),
            _spec(60502, location="San Francisco, CA"),
            _spec(60503, location="Lovely downtown views"),   # no state → skip
            _spec(60504, location=""),                        # empty → skip
        ]
        city, state, source = self._infer(specs)
        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"

    # ── source set sanity ─────────────────────────────────────────────────────

    def test_valid_source_values(self):
        """infer_canonical_target_from_candidates always returns a known source string."""
        from worker.core.anchor_location import infer_canonical_target_from_candidates
        _VALID_SOURCES = {
            "airbnb_first_page_nearest",
            "airbnb_first_page_vote",
            "fallback",
            "no_candidates",
            "no_parseable_candidates",
        }
        # vote path
        specs = [_spec(60601, location="San Francisco, CA")]
        _, _, source = infer_canonical_target_from_candidates(specs)
        assert source in _VALID_SOURCES
        # no_candidates path
        _, _, source2 = infer_canonical_target_from_candidates([])
        assert source2 in _VALID_SOURCES
        # nearest path
        s = _spec(60602, location="San Francisco, CA")
        s.lat, s.lng = 37.7749, -122.4194
        _, _, source3 = infer_canonical_target_from_candidates(
            [s], target_lat=37.52, target_lng=-122.28
        )
        assert source3 in _VALID_SOURCES


# ---------------------------------------------------------------------------
# Distance-based canonical target inference
# ---------------------------------------------------------------------------
#
# Real WGS-84 reference points used in this block:
#   Belmont, CA      37.5202, -122.2758   ← target in all "distance" tests
#   San Francisco    37.7749, -122.4194   ← ~37 km from Belmont
#   Redwood City     37.4849, -122.2364   ←  ~5 km from Belmont  (Peninsula)
#   San Mateo        37.5630, -122.3255   ←  ~6 km from Belmont  (Peninsula)


_TARGET_LAT = 37.5202   # Belmont, CA
_TARGET_LNG = -122.2758

_SF_LAT,  _SF_LNG  = 37.7749, -122.4194  # ~37 km from Belmont
_RC_LAT,  _RC_LNG  = 37.4849, -122.2364  # ~5 km  from Belmont
_SM_LAT,  _SM_LNG  = 37.5630, -122.3255  # ~6 km  from Belmont


def _with_coords(spec, lat, lng):
    """Return spec with lat/lng set (helper to keep test lines short)."""
    spec.lat, spec.lng = lat, lng
    return spec


class TestInferCanonicalTargetDistance:
    """
    Distance-first inference: when target coords are available and candidates
    carry listing-level coords, the geographically nearest market wins even
    if a more-distant city has more candidates.
    """

    def _infer(self, specs, **kw):
        from worker.core.anchor_location import infer_canonical_target_from_candidates
        return infer_canonical_target_from_candidates(
            specs,
            target_lat=_TARGET_LAT,
            target_lng=_TARGET_LNG,
            **kw,
        )

    # ── distance beats plurality ──────────────────────────────────────────────

    def test_nearest_beats_plurality(self):
        """
        5 SF listings (all ~37 km from Belmont, all with coords) +
        2 Redwood City listings (~5 km, with coords).

        nearest_n=3: nearest-3 → 2 RC + 1 SF → RC wins 2/3 = 67 % > 40 %.
        Vote alone would give SF (5/7 = 71 %) — distance correctly overrides.
        """
        sf  = [_with_coords(_spec(70001 + i, location="San Francisco, CA"),
                             _SF_LAT, _SF_LNG) for i in range(5)]
        rc  = [_with_coords(_spec(70006 + i, location="Redwood City, CA"),
                             _RC_LAT, _RC_LNG) for i in range(2)]

        city, state, source = self._infer(sf + rc, nearest_n=3)

        assert city == "redwood city"
        assert state == "CA"
        assert source == "airbnb_first_page_nearest"

    def test_single_coord_candidate_insufficient_uses_vote(self):
        """
        Only 1 coord-bearing candidate — fewer than min_nearest_candidates (3).
        Stage 1 is skipped; Stage 2 vote runs and SF (4 votes) wins.

        This is the key safety guard: a single listing cannot silently override
        a strong page-wide vote signal.
        """
        far_sf   = [_spec(70101 + i, location="San Francisco, CA") for i in range(4)]
        close_sm = _with_coords(_spec(70105, location="San Mateo, CA"),
                                _SM_LAT, _SM_LNG)

        # Only close_sm has coords → with_dist has 1 entry < min_nearest_candidates=3
        # → Stage 1 skipped → vote: SF 4/5 = 80% → vote wins
        city, state, source = self._infer(far_sf + [close_sm])

        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"   # not nearest

    def test_two_coord_candidates_insufficient_uses_vote(self):
        """
        2 coord-bearing candidates — still fewer than min_nearest_candidates (3).
        Stage 1 still skipped; vote on all 7 candidates decides.
        """
        far_sf  = [_spec(70111 + i, location="San Francisco, CA") for i in range(5)]
        near    = [
            _with_coords(_spec(70116, location="San Mateo, CA"), _SM_LAT, _SM_LNG),
            _with_coords(_spec(70117, location="San Mateo, CA"), _SM_LAT, _SM_LNG),
        ]

        # 2 coord candidates < 3 → Stage 1 skipped → vote: SF 5/7 = 71% wins
        city, state, source = self._infer(far_sf + near)

        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"

    def test_exactly_min_coord_candidates_fires_stage1(self):
        """
        Exactly min_nearest_candidates (3) coord-bearing candidates satisfies
        the guard — Stage 1 fires.  All 3 agree on Redwood City → nearest wins.
        """
        rc = [
            _with_coords(_spec(70121 + i, location="Redwood City, CA"),
                         _RC_LAT, _RC_LNG) for i in range(3)
        ]
        extra_sf = [_spec(70124 + i, location="San Francisco, CA") for i in range(5)]

        # 3 coord candidates == min_nearest_candidates → Stage 1 fires
        # nearest-5 → all 3 RC (with_dist has 3) → 3/3 = 100% → nearest wins
        city, state, source = self._infer(rc + extra_sf)

        assert city == "redwood city"
        assert source == "airbnb_first_page_nearest"

    def test_nearest_n_affects_winner(self):
        """
        nearest_n controls the neighbourhood size.  With a smaller window,
        the 2 very-close RC listings dominate; with a larger window, the 8
        farther SF listings can swamp them.
        """
        sf  = [_with_coords(_spec(70201 + i, location="San Francisco, CA"),
                             _SF_LAT, _SF_LNG) for i in range(8)]
        rc  = [_with_coords(_spec(70209 + i, location="Redwood City, CA"),
                             _RC_LAT, _RC_LNG) for i in range(2)]
        candidates = sf + rc

        # nearest_n=3 → 2 RC + 1 SF → RC wins
        city_small, _, _ = self._infer(candidates, nearest_n=3)
        assert city_small == "redwood city"

        # nearest_n=10 → 2 RC + 8 SF → SF wins (8/10 = 80 %)
        city_large, _, source_large = self._infer(candidates, nearest_n=10)
        assert city_large == "san francisco"
        assert source_large == "airbnb_first_page_nearest"

    # ── distance stage inconclusive → falls through to vote ──────────────────

    def test_distance_inconclusive_falls_to_vote(self):
        """
        If the nearest-N set has no clear winner (< min_vote_fraction),
        Stage 2 vote on all candidates takes over.
        """
        # 3 near: 1 RC + 1 SF + 1 SM — all equally split (1/3 < 40 %)
        near = [
            _with_coords(_spec(70301, location="Redwood City, CA"), _RC_LAT, _RC_LNG),
            _with_coords(_spec(70302, location="San Francisco, CA"), _SF_LAT, _SF_LNG),
            _with_coords(_spec(70303, location="San Mateo, CA"),     _SM_LAT, _SM_LNG),
        ]
        # 5 extra SF without coords → drives vote to SF
        extras = [_spec(70310 + i, location="San Francisco, CA") for i in range(5)]

        city, state, source = self._infer(near + extras, nearest_n=3)

        # Distance stage splits 1/1/1 → inconclusive; vote SF wins (6/8 = 75 %)
        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"

    # ── no target coords → vote only ─────────────────────────────────────────

    def test_no_target_coords_uses_vote(self):
        """When target_lat/lng are None, skip distance stage and use vote."""
        from worker.core.anchor_location import infer_canonical_target_from_candidates

        specs = [
            _with_coords(_spec(70401 + i, location="San Francisco, CA"),
                         _SF_LAT, _SF_LNG) for i in range(4)
        ]
        city, state, source = infer_canonical_target_from_candidates(
            specs,
            target_lat=None,
            target_lng=None,
        )
        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"   # not nearest

    # ── candidates lack coords → vote only ───────────────────────────────────

    def test_no_candidate_coords_uses_vote(self):
        """
        Target has coords but candidates carry no listing-level coords.
        Distance stage has nothing to sort → falls back to vote.
        """
        specs = [_spec(70501 + i, location="San Francisco, CA") for i in range(4)]
        # specs have lat=None (default)
        city, state, source = self._infer(specs)

        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"

    # ── normalisation integrates with distance stage ──────────────────────────

    def test_normalisation_respected_in_distance_stage(self):
        """
        'Downtown Redwood City, CA' normalises to 'Redwood City, CA' before
        the distance vote; both count toward 'redwood city'.
        """
        rc1 = _with_coords(_spec(70601, location="Redwood City, CA"),
                           _RC_LAT, _RC_LNG)
        rc2 = _with_coords(_spec(70602, location="Downtown Redwood City, CA"),
                           _RC_LAT, _RC_LNG)
        sf  = _with_coords(_spec(70603, location="San Francisco, CA"),
                           _SF_LAT, _SF_LNG)

        city, state, source = self._infer([rc1, rc2, sf], nearest_n=3)

        assert city == "redwood city"
        assert source == "airbnb_first_page_nearest"


# ---------------------------------------------------------------------------
# Wiring: _resolve_canonical_target (extracted helper from run_criteria_search)
# ---------------------------------------------------------------------------


class TestCriteriaWiring:
    """
    Tests for ``_resolve_canonical_target``, the pure helper that
    ``run_criteria_search`` calls to decide the canonical city/state before
    invoking ``_select_anchor_candidate``.

    Testing the helper directly instead of replicating the if/else inline
    gives better isolation and will catch future wiring regressions.
    """

    def _resolve(self, candidates, raw_city, raw_state, addr_confidence,
                 target_lat=None, target_lng=None):
        from worker.scraper.price_estimator import _resolve_canonical_target
        return _resolve_canonical_target(
            candidates, raw_city, raw_state, addr_confidence,
            target_lat=target_lat, target_lng=target_lng,
        )

    # ── high confidence → address wins unconditionally ────────────────────────

    def test_high_conf_ignores_first_page(self):
        """
        High confidence: _resolve returns raw address regardless of what
        first-page candidates show — even if they all have coords pointing
        to a completely different city.
        """
        candidates = [
            _with_coords(_spec(80001 + i, location="San Francisco, CA"),
                         _SF_LAT, _SF_LNG) for i in range(5)
        ]
        city, state, source = self._resolve(
            candidates, "belmont", "CA", "high",
            target_lat=_TARGET_LAT, target_lng=_TARGET_LNG,
        )

        assert city == "belmont"   # raw address, unchanged
        assert state == "CA"
        assert source == "address"

    # ── low confidence + ≥ min_nearest coords → nearest wins ─────────────────

    def test_low_conf_sufficient_coords_uses_nearest(self):
        """
        Low confidence + 5 SF listings all with coords (≥ min_nearest=3).
        Stage 1 fires; all 5 point to SF → canonical = SF, NOT raw 'belmont'.
        """
        candidates = [
            _with_coords(_spec(80101 + i, location="San Francisco, CA"),
                         _SF_LAT, _SF_LNG) for i in range(5)
        ]
        city, state, source = self._resolve(
            candidates, "belmont", "CA", "low",
            target_lat=_TARGET_LAT, target_lng=_TARGET_LNG,
        )

        assert city == "san francisco"
        assert state == "CA"
        assert source == "airbnb_first_page_nearest"

    # ── low confidence + insufficient coords → vote ───────────────────────────

    def test_low_conf_insufficient_coords_uses_vote(self):
        """
        6 SF candidates (no coords) + 1 RC candidate (with coords).
        Only 1 coord-bearing candidate < min_nearest=3 → Stage 1 skipped.
        Vote runs: SF 6/7 = 86% → SF wins, source = airbnb_first_page_vote.

        Critical regression guard: a single coord-bearing listing must NOT
        silently override the dominant page-wide signal.
        """
        sf_specs = [_spec(80201 + i, location="San Francisco, CA") for i in range(6)]
        rc_spec  = _with_coords(_spec(80207, location="Redwood City, CA"),
                                _RC_LAT, _RC_LNG)

        city, state, source = self._resolve(
            sf_specs + [rc_spec], "belmont", "CA", "low",
            target_lat=_TARGET_LAT, target_lng=_TARGET_LNG,
        )

        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"   # NOT nearest

    # ── medium confidence + no target coords → vote ───────────────────────────

    def test_medium_conf_no_target_coords_uses_vote(self):
        """
        Medium confidence, target has no coords.  Stage 1 is skipped entirely;
        4 SF candidates → vote → canonical = SF.
        """
        candidates = [_spec(80301 + i, location="San Francisco, CA") for i in range(4)]
        city, state, source = self._resolve(
            candidates, "belmont", "CA", "medium",
            target_lat=None, target_lng=None,
        )

        assert city == "san francisco"
        assert source == "airbnb_first_page_vote"

    # ── empty first page → fallback to raw address ────────────────────────────

    def test_empty_candidates_fallback(self):
        """No first-page candidates → inference falls back to raw address."""
        city, state, source = self._resolve(
            [], "belmont", "CA", "low",
            target_lat=_TARGET_LAT, target_lng=_TARGET_LNG,
        )

        assert city == "belmont"
        assert source == "no_candidates"

    # ── distance beats plurality when evidence is sufficient ──────────────────

    def test_distance_beats_plurality_with_sufficient_evidence(self):
        """
        5 SF candidates with coords (~37 km) + 3 RC candidates with coords
        (~5 km).  All 8 have coords → Stage 1 fires with nearest_n=5.

        Sorted by distance: 3 RC (~5km) then 2 SF (~37km) fill the nearest-5
        slot → RC 3/5 = 60% wins.

        Vote alone: SF 5/8 = 63% would win — distance correctly overrides.
        """
        sf  = [_with_coords(_spec(80401 + i, location="San Francisco, CA"),
                             _SF_LAT, _SF_LNG) for i in range(5)]
        rc  = [_with_coords(_spec(80406 + i, location="Redwood City, CA"),
                             _RC_LAT, _RC_LNG) for i in range(3)]

        city, state, source = self._resolve(
            sf + rc, "belmont", "CA", "low",
            target_lat=_TARGET_LAT, target_lng=_TARGET_LNG,
        )

        assert city == "redwood city"
        assert source == "airbnb_first_page_nearest"
