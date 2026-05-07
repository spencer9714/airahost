"""
Tests for the excluded-comps safety net in _build_daily_transparent_result.

Primary blacklist filter lives in worker/scraper/day_query.py
(estimate_base_price_for_date) and worker/core/benchmark.py
(estimate_benchmark_price_for_date).  Both run before pricing math so the
median / priceByDate stay clean.

This safety net is defense-in-depth: even if a future day-query variant
forgets to filter, _build_daily_transparent_result will still drop excluded
roomIds before they enter comparable_listings or priceByDate.
"""

from __future__ import annotations

from worker.scraper.price_estimator import _build_daily_transparent_result
from worker.scraper.target_extractor import ListingSpec


def _target() -> ListingSpec:
    return ListingSpec(
        url="https://www.airbnb.com/rooms/999",
        title="Target listing",
        location="Belmont, CA",
        property_type="entire_home",
        accommodates=4,
        bedrooms=2,
        baths=1.5,
        nightly_price=200,
    )


def _comp(comp_id: str, price: float) -> dict:
    return {
        "id": comp_id,
        "title": f"Comp {comp_id}",
        "propertyType": "entire_home",
        "nightlyPrice": price,
        "similarity": 0.9,
        "url": f"https://www.airbnb.com/rooms/{comp_id}",
    }


def _day_result(date: str, comps: list[dict], comp_prices: dict[str, float]) -> dict:
    return {
        "date": date,
        "median_price": 180,
        "comps_collected": len(comps),
        "comps_used": len(comps),
        "below_similarity_floor": 0,
        "price_outliers_excluded": 0,
        "price_outliers_downweighted": 0,
        "geo_excluded": 0,
        "price_band_excluded": 0,
        "filter_stage": "strict",
        "flags": [],
        "is_sampled": True,
        "is_weekend": False,
        "price_distribution": {},
        "top_comps": comps,
        "comp_prices": comp_prices,
        "error": None,
        "selection_mode": "strict",
        "pricing_confidence": "high",
    }


_QUERY_CRITERIA = {
    "locationBasis": "Belmont, CA",
    "searchAdults": 4,
    "checkin": "2026-05-01",
    "checkout": "2026-05-02",
    "totalNights": 1,
    "sampledNights": 1,
    "queryMode": "day_by_day",
    "propertyTypeFilter": "entire_home",
}


# ── Backward compat: missing excluded_room_ids kwarg defaults to None ─────────


def test_default_kwarg_is_none_so_existing_tests_pass():
    """The new kwarg must default to None so existing call sites stay green."""
    transparent = _build_daily_transparent_result(
        target=_target(),
        query_criteria=_QUERY_CRITERIA,
        all_day_results=[
            _day_result(
                "2026-05-01",
                [_comp("111", 120), _comp("222", 140)],
                {"111": 120, "222": 140},
            )
        ],
        timings_ms={"total_ms": 10},
        source="scrape",
        extraction_warnings=[],
    )
    ids = {c["id"] for c in transparent["comparableListings"]}
    assert ids == {"111", "222"}


# ── Safety net: excluded room IDs are dropped from comparable_listings ────────


def test_excluded_room_id_dropped_from_comparable_listings():
    transparent = _build_daily_transparent_result(
        target=_target(),
        query_criteria=_QUERY_CRITERIA,
        all_day_results=[
            _day_result(
                "2026-05-01",
                [_comp("111", 120), _comp("222", 140)],
                {"111": 120, "222": 140},
            )
        ],
        timings_ms={"total_ms": 10},
        source="scrape",
        extraction_warnings=[],
        excluded_room_ids={"222"},
    )
    ids = {c["id"] for c in transparent["comparableListings"]}
    assert ids == {"111"}, f"excluded roomId 222 must be absent; got {ids}"


def test_excluded_room_id_absent_from_price_by_date():
    """Even if comp_prices contains the excluded id, comparable_listings must
    not surface its priceByDate. (comp_prices is the day-level map; the
    safety net works at the comparable_index population step.)"""
    transparent = _build_daily_transparent_result(
        target=_target(),
        query_criteria=_QUERY_CRITERIA,
        all_day_results=[
            _day_result(
                "2026-05-01",
                [_comp("111", 120), _comp("222", 140)],
                {"111": 120, "222": 140},
            ),
            _day_result(
                "2026-05-02",
                [_comp("111", 125), _comp("222", 150)],
                {"111": 125, "222": 150},
            ),
        ],
        timings_ms={"total_ms": 10},
        source="scrape",
        extraction_warnings=[],
        excluded_room_ids={"222"},
    )
    comps = {c["id"]: c for c in transparent["comparableListings"]}
    assert "222" not in comps
    # 111 still has both days
    assert comps["111"]["priceByDate"] == {"2026-05-01": 120.0, "2026-05-02": 125.0}


def test_multiple_excluded_room_ids():
    transparent = _build_daily_transparent_result(
        target=_target(),
        query_criteria=_QUERY_CRITERIA,
        all_day_results=[
            _day_result(
                "2026-05-01",
                [_comp("111", 120), _comp("222", 140), _comp("333", 160)],
                {"111": 120, "222": 140, "333": 160},
            )
        ],
        timings_ms={"total_ms": 10},
        source="scrape",
        extraction_warnings=[],
        excluded_room_ids={"222", "333"},
    )
    ids = {c["id"] for c in transparent["comparableListings"]}
    assert ids == {"111"}


def test_empty_excluded_set_passes_all_through():
    """Empty set should not hide any comps (it is truthy-falsy: an empty set
    short-circuits the filter, equivalent to None)."""
    transparent = _build_daily_transparent_result(
        target=_target(),
        query_criteria=_QUERY_CRITERIA,
        all_day_results=[
            _day_result(
                "2026-05-01",
                [_comp("111", 120), _comp("222", 140)],
                {"111": 120, "222": 140},
            )
        ],
        timings_ms={"total_ms": 10},
        source="scrape",
        extraction_warnings=[],
        excluded_room_ids=set(),
    )
    ids = {c["id"] for c in transparent["comparableListings"]}
    assert ids == {"111", "222"}


def test_excluded_id_not_in_results_is_no_op():
    """Excluding an id that doesn't appear in any day_result must not error."""
    transparent = _build_daily_transparent_result(
        target=_target(),
        query_criteria=_QUERY_CRITERIA,
        all_day_results=[
            _day_result(
                "2026-05-01",
                [_comp("111", 120)],
                {"111": 120},
            )
        ],
        timings_ms={"total_ms": 10},
        source="scrape",
        extraction_warnings=[],
        excluded_room_ids={"99999"},
    )
    ids = {c["id"] for c in transparent["comparableListings"]}
    assert ids == {"111"}
