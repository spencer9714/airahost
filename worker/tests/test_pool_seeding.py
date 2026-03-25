"""
Tests for worker.core.pool_seeding — Phase 2 pool bootstrap.

Covers:
  - _extract_airbnb_id: extracts numeric room ID from URL
  - _compute_price_reliability: correct formula at 0, partial, and full outliers
  - _build_snapshot: field mapping from comparableListings entry
  - seed_pool_from_report: insert path (no existing entries)
  - seed_pool_from_report: update path (existing entry — increments counts)
  - seed_pool_from_report: outlier flag propagates to outlier_count
  - seed_pool_from_report: entries with no URL are skipped
  - seed_pool_from_report: capped at _POOL_MAX_SIZE (20)
  - seed_pool_from_report: empty list is a no-op
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

from worker.core.pool_seeding import (
    _POOL_MAX_SIZE,
    _build_snapshot,
    _compute_price_reliability,
    _extract_airbnb_id,
    seed_pool_from_report,
)


# ── Unit: _extract_airbnb_id ──────────────────────────────────────────────

def test_extract_airbnb_id_standard():
    url = "https://www.airbnb.com/rooms/12345678?adults=2"
    assert _extract_airbnb_id(url) == "12345678"


def test_extract_airbnb_id_no_rooms():
    assert _extract_airbnb_id("https://www.airbnb.com/s/NYC") is None


def test_extract_airbnb_id_empty():
    assert _extract_airbnb_id("") is None


def test_extract_airbnb_id_none():
    assert _extract_airbnb_id(None) is None  # type: ignore[arg-type]


# ── Unit: _compute_price_reliability ─────────────────────────────────────

def test_price_reliability_no_observations():
    assert _compute_price_reliability(0, 0) == 1.0


def test_price_reliability_no_outliers():
    assert _compute_price_reliability(10, 0) == 1.0


def test_price_reliability_half_outliers():
    assert _compute_price_reliability(10, 5) == pytest.approx(0.5)


def test_price_reliability_all_outliers():
    assert _compute_price_reliability(5, 5) == 0.0


def test_price_reliability_floor_zero():
    # More outliers than observations shouldn't go below 0
    assert _compute_price_reliability(2, 5) == 0.0


# ── Unit: _build_snapshot ─────────────────────────────────────────────────

def test_build_snapshot_full_entry():
    comp = {
        "url": "https://www.airbnb.com/rooms/999",
        "title": "Nice Place",
        "nightlyPrice": 120.5,
        "propertyType": "entire_home",
        "bedrooms": 2,
        "baths": 1.5,
        "accommodates": 4,
        "beds": 2,
        "location": "Brooklyn, NY",
        "rating": 4.8,
        "reviews": 150,
    }
    snap = _build_snapshot(comp)
    assert snap["listing_url"] == "https://www.airbnb.com/rooms/999"
    assert snap["title"] == "Nice Place"
    assert snap["last_nightly_price"] == pytest.approx(120.5)
    assert snap["property_type"] == "entire_home"
    assert snap["bedrooms"] == 2
    assert snap["baths"] == pytest.approx(1.5)
    assert snap["accommodates"] == 4
    assert snap["beds"] == 2
    assert snap["location"] == "Brooklyn, NY"
    assert snap["rating"] == pytest.approx(4.8)
    assert snap["reviews"] == 150
    assert "last_seen_at" in snap


def test_build_snapshot_missing_optional_fields():
    comp = {"url": "https://www.airbnb.com/rooms/1"}
    snap = _build_snapshot(comp)
    assert "last_nightly_price" not in snap
    assert "title" not in snap
    assert "last_seen_at" in snap


def test_build_snapshot_zero_price_omitted():
    comp = {"url": "https://www.airbnb.com/rooms/1", "nightlyPrice": 0}
    snap = _build_snapshot(comp)
    assert "last_nightly_price" not in snap


# ── Helpers for seed_pool_from_report tests ───────────────────────────────

def _make_comp(room_id: str, similarity: float = 0.75, outlier: bool = False) -> Dict[str, Any]:
    return {
        "url": f"https://www.airbnb.com/rooms/{room_id}",
        "title": f"Listing {room_id}",
        "similarity": similarity,
        "nightlyPrice": 100.0,
        "propertyType": "entire_home",
        "bedrooms": 2,
        "baths": 1.0,
        "accommodates": 4,
        "beds": 2,
        "priceOutlier": outlier,
    }


def _make_client(existing_rows: Optional[List[Dict[str, Any]]] = None) -> MagicMock:
    """Build a mock Supabase client that returns given rows for the pool query."""
    client = MagicMock()

    # Chain for comparable_pool_entries select
    pool_select_chain = MagicMock()
    pool_select_chain.execute.return_value = MagicMock(data=existing_rows or [])
    (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .in_.return_value
    ) = pool_select_chain

    # Chain for comparable_pool_entries count query
    count_chain = MagicMock()
    count_chain.execute.return_value = MagicMock(count=len(existing_rows or []), data=[])

    # Chain for saved_listings version fetch
    version_chain = MagicMock()
    version_chain.execute.return_value = MagicMock(data={"comp_pool_version": 0})

    return client


# ── Integration: seed_pool_from_report ───────────────────────────────────

def test_seed_empty_list_is_noop():
    client = MagicMock()
    seed_pool_from_report(client, "listing-uuid", [])
    client.table.assert_not_called()


def test_seed_skips_entries_without_url():
    client = _make_client()
    comps = [{"similarity": 0.9, "title": "No URL"}]
    seed_pool_from_report(client, "listing-uuid", comps)
    # insert should not have been called for entries with no valid Airbnb ID
    # (the only DB calls would be select + stats update; no insert)
    insert_calls = [
        c for c in client.table.return_value.insert.call_args_list
    ]
    assert len(insert_calls) == 0


def test_seed_inserts_new_entries():
    """New entries (not in existing pool) go through insert path."""
    client = _make_client(existing_rows=[])

    # Patch _refresh_pool_stats to isolate insert behaviour
    with patch("worker.core.pool_seeding._refresh_pool_stats"):
        comps = [_make_comp("111"), _make_comp("222")]
        seed_pool_from_report(client, "abc-listing", comps)

    # insert should have been called once with both rows
    insert_call = client.table.return_value.insert
    assert insert_call.called
    inserted_rows = insert_call.call_args[0][0]  # first positional arg
    assert len(inserted_rows) == 2
    airbnb_ids = {r["airbnb_listing_id"] for r in inserted_rows}
    assert airbnb_ids == {"111", "222"}


def test_seed_updates_existing_entry():
    """Existing pool entry gets tenure_runs incremented, not re-inserted."""
    existing = [
        {
            "id": "row-id-1",
            "airbnb_listing_id": "111",
            "tenure_runs": 3,
            "total_observations": 3,
            "outlier_count": 0,
            "status": "active",
        }
    ]
    client = _make_client(existing_rows=existing)

    with patch("worker.core.pool_seeding._refresh_pool_stats"):
        comps = [_make_comp("111")]
        seed_pool_from_report(client, "abc-listing", comps)

    # update should be called; insert should NOT be called
    update_chain = client.table.return_value.update
    assert update_chain.called
    updated_payload = update_chain.call_args[0][0]
    assert updated_payload["tenure_runs"] == 4
    assert updated_payload["total_observations"] == 4
    assert updated_payload["outlier_count"] == 0
    assert updated_payload["price_reliability_score"] == pytest.approx(1.0)


def test_seed_outlier_increments_outlier_count():
    """A comp flagged priceOutlier=True must increment outlier_count."""
    existing = [
        {
            "id": "row-id-1",
            "airbnb_listing_id": "111",
            "tenure_runs": 2,
            "total_observations": 2,
            "outlier_count": 1,
            "status": "active",
        }
    ]
    client = _make_client(existing_rows=existing)

    with patch("worker.core.pool_seeding._refresh_pool_stats"):
        comps = [_make_comp("111", outlier=True)]
        seed_pool_from_report(client, "abc-listing", comps)

    update_chain = client.table.return_value.update
    updated_payload = update_chain.call_args[0][0]
    assert updated_payload["outlier_count"] == 2
    # reliability = 1 - 2/3 ≈ 0.333
    assert updated_payload["price_reliability_score"] == pytest.approx(1 - 2 / 3)


def test_seed_caps_at_pool_max_size():
    """Only the top _POOL_MAX_SIZE comps by similarity are seeded."""
    client = _make_client(existing_rows=[])

    # Create 25 comps with decreasing similarity
    comps = [_make_comp(str(i), similarity=0.99 - i * 0.01) for i in range(25)]

    with patch("worker.core.pool_seeding._refresh_pool_stats"):
        seed_pool_from_report(client, "abc-listing", comps)

    insert_call = client.table.return_value.insert
    inserted_rows = insert_call.call_args[0][0]
    assert len(inserted_rows) == _POOL_MAX_SIZE


def test_seed_new_outlier_entry_has_correct_reliability():
    """Brand-new entry with priceOutlier=True should have reliability < 1.0."""
    client = _make_client(existing_rows=[])

    with patch("worker.core.pool_seeding._refresh_pool_stats"):
        comps = [_make_comp("777", outlier=True)]
        seed_pool_from_report(client, "abc-listing", comps)

    insert_call = client.table.return_value.insert
    inserted_rows = insert_call.call_args[0][0]
    assert len(inserted_rows) == 1
    row = inserted_rows[0]
    assert row["outlier_count"] == 1
    assert row["total_observations"] == 1
    assert row["price_reliability_score"] == pytest.approx(0.0)
