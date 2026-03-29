"""
Tests for Phase 3A geographic modules.

Covers:
  geocoding.py:
    - geocode_address returns (lat, lng) on success
    - geocode_address returns None on network error
    - geocode_address returns None on empty response
    - geocode_address returns None for blank address
    - geocode_address returns None on out-of-range coords

  geo_filter.py:
    - haversine_km: known distances (same point, NYC↔LA, antipodal)
    - apply_geo_filter: comps with coords beyond radius are excluded
    - apply_geo_filter: comps without coords always pass through
    - apply_geo_filter: target without coords → no filtering (caller check)
    - apply_geo_filter: sets distance_to_target_km on specs with coords
    - apply_geo_filter: comps within radius are retained
    - apply_geo_filter: empty input returns empty list

  integration:
    - ListingSpec accepts lat/lng/distance_to_target_km fields
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from worker.core.geo_filter import DEFAULT_MAX_RADIUS_KM, apply_geo_filter, haversine_km
from worker.core.geocoding import geocode_address
from worker.scraper.target_extractor import ListingSpec


# ── haversine_km ──────────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert haversine_km(40.0, -74.0, 40.0, -74.0) == pytest.approx(0.0, abs=1e-6)


def test_haversine_nyc_to_la():
    # New York City ~ (40.7128, -74.0060)
    # Los Angeles   ~ (34.0522, -118.2437)
    # True great-circle ≈ 3940 km
    dist = haversine_km(40.7128, -74.0060, 34.0522, -118.2437)
    assert 3900 < dist < 3980


def test_haversine_short_urban_distance():
    # Two points 1 km apart (approx 0.009° lat)
    dist = haversine_km(25.0, 121.5, 25.009, 121.5)
    assert 0.9 < dist < 1.1


def test_haversine_antipodal_points():
    # Antipodal = half Earth circumference ≈ 20015 km
    dist = haversine_km(0.0, 0.0, 0.0, 180.0)
    assert 19000 < dist < 21000


# ── apply_geo_filter ──────────────────────────────────────────────────────────

def _make_spec(lat: Optional[float] = None, lng: Optional[float] = None) -> ListingSpec:
    s = ListingSpec(url="https://www.airbnb.com/rooms/1")
    s.lat = lat
    s.lng = lng
    return s


TARGET_LAT, TARGET_LNG = 40.7128, -74.0060  # NYC


def test_geo_filter_nearby_comp_retained():
    # Brooklyn Bridge ≈ 0.6 km from NYC reference point
    comp = _make_spec(lat=40.7061, lng=-73.9969)
    retained, excluded = apply_geo_filter([comp], TARGET_LAT, TARGET_LNG)
    assert excluded == 0
    assert len(retained) == 1


def test_geo_filter_far_comp_excluded():
    # Los Angeles — ~3940 km away → beyond 30 km default
    comp = _make_spec(lat=34.0522, lng=-118.2437)
    retained, excluded = apply_geo_filter([comp], TARGET_LAT, TARGET_LNG)
    assert excluded == 1
    assert len(retained) == 0


def test_geo_filter_no_coords_passes_through():
    """Comp without coordinates must never be filtered out."""
    comp = _make_spec(lat=None, lng=None)
    retained, excluded = apply_geo_filter([comp], TARGET_LAT, TARGET_LNG)
    assert excluded == 0
    assert len(retained) == 1


def test_geo_filter_sets_distance_on_coord_comps():
    """distance_to_target_km is set on comps that have coordinates."""
    comp = _make_spec(lat=40.7061, lng=-73.9969)
    apply_geo_filter([comp], TARGET_LAT, TARGET_LNG)
    assert comp.distance_to_target_km is not None
    assert comp.distance_to_target_km < 5.0


def test_geo_filter_no_coords_distance_not_set():
    """distance_to_target_km is not set on comps without coordinates."""
    comp = _make_spec(lat=None, lng=None)
    apply_geo_filter([comp], TARGET_LAT, TARGET_LNG)
    assert comp.distance_to_target_km is None


def test_geo_filter_mixed_pool():
    """Near comp + far comp + no-coord comp → only far comp excluded."""
    near = _make_spec(lat=40.72, lng=-74.01)     # ~1 km
    far = _make_spec(lat=34.0522, lng=-118.2437)  # LA
    no_coord = _make_spec()
    retained, excluded = apply_geo_filter([near, far, no_coord], TARGET_LAT, TARGET_LNG)
    assert excluded == 1
    assert len(retained) == 2
    assert far not in retained
    assert near in retained
    assert no_coord in retained


def test_geo_filter_custom_radius():
    """Custom radius allows tighter or looser filtering."""
    # 10 km from NYC — within 30 km but outside 5 km
    comp = _make_spec(lat=40.8000, lng=-74.0060)  # ~10 km north
    retained_wide, excl_wide = apply_geo_filter([comp], TARGET_LAT, TARGET_LNG, max_radius_km=30.0)
    retained_tight, excl_tight = apply_geo_filter([comp], TARGET_LAT, TARGET_LNG, max_radius_km=5.0)
    assert excl_wide == 0   # passes 30 km filter
    assert excl_tight == 1  # fails 5 km filter


def test_geo_filter_empty_input():
    retained, excluded = apply_geo_filter([], TARGET_LAT, TARGET_LNG)
    assert retained == []
    assert excluded == 0


# ── geocode_address (mocked network) ─────────────────────────────────────────

def _mock_urlopen(response_json: str):
    """Context manager that makes urlopen return the given JSON bytes."""
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = response_json.encode("utf-8")
    return patch("worker.core.geocoding.urlopen", return_value=mock_resp)


def test_geocode_returns_coords_on_success():
    payload = json.dumps([{"lat": "40.71280", "lon": "-74.00600"}])
    with _mock_urlopen(payload):
        result = geocode_address("New York, NY")
    assert result is not None
    lat, lng = result
    assert lat == pytest.approx(40.7128)
    assert lng == pytest.approx(-74.006)


def test_geocode_returns_none_on_empty_response():
    with _mock_urlopen("[]"):
        result = geocode_address("xyzzy nowhere land")
    assert result is None


def test_geocode_returns_none_on_network_error():
    from urllib.error import URLError
    with patch("worker.core.geocoding.urlopen", side_effect=URLError("timeout")):
        result = geocode_address("Tokyo, Japan")
    assert result is None


def test_geocode_returns_none_for_blank_address():
    result = geocode_address("")
    assert result is None

    result2 = geocode_address("   ")
    assert result2 is None


def test_geocode_returns_none_for_invalid_json():
    with _mock_urlopen("not-json"):
        result = geocode_address("Test Address")
    assert result is None


def test_geocode_returns_none_for_out_of_range_coords():
    # lat=999 is out of valid range → should be rejected
    payload = json.dumps([{"lat": "999.0", "lon": "0.0"}])
    with _mock_urlopen(payload):
        result = geocode_address("Bad coords")
    assert result is None


# ── ListingSpec: new fields ───────────────────────────────────────────────────

def test_listing_spec_accepts_lat_lng():
    spec = ListingSpec(url="https://airbnb.com/rooms/1", lat=40.7128, lng=-74.006)
    assert spec.lat == pytest.approx(40.7128)
    assert spec.lng == pytest.approx(-74.006)
    assert spec.distance_to_target_km is None


def test_listing_spec_defaults_lat_lng_to_none():
    spec = ListingSpec(url="https://airbnb.com/rooms/2")
    assert spec.lat is None
    assert spec.lng is None
    assert spec.distance_to_target_km is None
