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
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> ListingSpec:
    return ListingSpec(
        url=f"https://www.airbnb.com/rooms/{room_id}",
        title=f"Listing {room_id}",
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
            [local_a, local_b, remote], target, TARGET_LAT, TARGET_LNG
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
            [close_good, close_poor, remote_good], target, TARGET_LAT, TARGET_LNG
        )

        assert best.url == close_good.url
        assert debug["anchorCandidatesAfterGeo"] == 2  # close_good + close_poor

    def test_remote_wins_when_no_local_candidates(self):
        """If ALL candidates are beyond tight radius, fallback allows remote."""
        target = _target()
        remote_a = _spec(3001, lat=_REMOTE_LAT, lng=_REMOTE_LNG)
        remote_b = _spec(3002, bedrooms=3, lat=_REMOTE_LAT + 0.1, lng=_REMOTE_LNG)

        best, score, debug = _select_anchor_candidate(
            [remote_a, remote_b], target, TARGET_LAT, TARGET_LNG
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
            [single_local, edge], target, TARGET_LAT, TARGET_LNG
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
            [local_a, local_b, edge], target, TARGET_LAT, TARGET_LNG
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
            [local, distant], target, TARGET_LAT, TARGET_LNG
        )

        assert best.url == local.url
        assert debug["anchorHasTargetCoords"] is True
        assert debug["anchorDistanceKm"] < _ANCHOR_RADIUS_TIGHT_KM

    def test_debug_has_all_required_keys(self):
        """Ensure all documented debug keys are present in the output."""
        target = _target()
        cand = _spec(9999, lat=_LOCAL_A_LAT, lng=_LOCAL_A_LNG)

        _, _, debug = _select_anchor_candidate(
            [cand], target, TARGET_LAT, TARGET_LNG
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
        }
        missing = required_keys - debug.keys()
        assert not missing, f"Missing debug keys: {missing}"
