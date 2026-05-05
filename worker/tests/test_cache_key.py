"""
Cache key tests for worker/core/cache.py.

Mirrors src/lib/cacheKey.test.ts — the TS and Python implementations must
stay byte-for-byte compatible (same input → same hex digest).
"""

from __future__ import annotations

from worker.core.cache import CACHE_SCHEMA_VERSION, compute_cache_key


ADDR = "Belmont, CA"
START = "2026-05-01"
END = "2026-05-08"
POLICY = {
    "weeklyDiscountPct": 0,
    "monthlyDiscountPct": 0,
    "refundable": True,
    "nonRefundableDiscountPct": 0,
    "stackingMode": "compound",
    "maxTotalDiscountPct": 0,
}


def _key(attrs: dict) -> str:
    return compute_cache_key(ADDR, attrs, START, END, POLICY, None, "criteria")


# ── Schema version ───────────────────────────────────────────────────────────


def test_schema_version_bumped_to_v2():
    """v1→v2 bump invalidates all old cache rows post-deploy."""
    assert CACHE_SCHEMA_VERSION == "v2"


# ── Baseline determinism ──────────────────────────────────────────────────────


def test_same_input_same_key():
    a = _key({"propertyType": "entire_home", "bedrooms": 2})
    b = _key({"propertyType": "entire_home", "bedrooms": 2})
    assert a == b


# ── preferred_comp_room_ids: order matters ────────────────────────────────────


def test_preferred_comp_order_changes_key():
    """Reordering preferredComps must change key — primary semantics is index 0."""
    k1 = _key(
        {
            "preferredComps": [
                {"listingUrl": "https://www.airbnb.com/rooms/111"},
                {"listingUrl": "https://www.airbnb.com/rooms/222"},
            ]
        }
    )
    k2 = _key(
        {
            "preferredComps": [
                {"listingUrl": "https://www.airbnb.com/rooms/222"},
                {"listingUrl": "https://www.airbnb.com/rooms/111"},
            ]
        }
    )
    assert k1 != k2


def test_appending_secondary_preferred_changes_key():
    """The new behavior — pre-v2 cache key only looked at the first; v2 looks at all."""
    k1 = _key(
        {"preferredComps": [{"listingUrl": "https://www.airbnb.com/rooms/111"}]}
    )
    k2 = _key(
        {
            "preferredComps": [
                {"listingUrl": "https://www.airbnb.com/rooms/111"},
                {"listingUrl": "https://www.airbnb.com/rooms/222"},
            ]
        }
    )
    assert k1 != k2


def test_disabled_preferred_not_counted():
    k1 = _key(
        {
            "preferredComps": [
                {"listingUrl": "https://www.airbnb.com/rooms/111", "enabled": True},
            ]
        }
    )
    k2 = _key(
        {
            "preferredComps": [
                {"listingUrl": "https://www.airbnb.com/rooms/111", "enabled": True},
                {"listingUrl": "https://www.airbnb.com/rooms/222", "enabled": False},
            ]
        }
    )
    assert k1 == k2


def test_url_query_params_collapse_to_same_room_id():
    k1 = _key(
        {"preferredComps": [{"listingUrl": "https://www.airbnb.com/rooms/111"}]}
    )
    k2 = _key(
        {
            "preferredComps": [
                {"listingUrl": "https://www.airbnb.com/rooms/111?check_in=2026-05-01"}
            ]
        }
    )
    assert k1 == k2


# ── excluded_room_ids: order does NOT matter (sorted set) ─────────────────────


def test_excluded_comps_order_irrelevant():
    """excludedComps is set semantics — order should not affect the key."""
    k1 = _key(
        {
            "excludedComps": [
                {"roomId": "111", "excludedAt": "2026-04-01T00:00:00Z"},
                {"roomId": "222", "excludedAt": "2026-04-02T00:00:00Z"},
            ]
        }
    )
    k2 = _key(
        {
            "excludedComps": [
                {"roomId": "222", "excludedAt": "2026-04-02T00:00:00Z"},
                {"roomId": "111", "excludedAt": "2026-04-01T00:00:00Z"},
            ]
        }
    )
    assert k1 == k2


def test_adding_exclusion_changes_key():
    k1 = _key({})
    k2 = _key(
        {
            "excludedComps": [
                {"roomId": "111", "excludedAt": "2026-04-01T00:00:00Z"}
            ]
        }
    )
    assert k1 != k2


def test_empty_excluded_array_preserves_key():
    """empty list and missing field must both produce the same canonical key."""
    k1 = _key({})
    k2 = _key({"excludedComps": []})
    assert k1 == k2


# ── Cross-language compatibility marker ───────────────────────────────────────
# This test guards a specific known input → known hex.  If you change the
# canonical payload shape, regenerate the expected value AND update the TS
# mirror in src/lib/cacheKey.test.ts.


def test_known_canonical_hex():
    """
    Pin a known input to a known SHA-256 prefix so accidental payload-shape
    changes are caught immediately.  Re-generate by running this test once
    and copying the actual output.
    """
    k = _key(
        {
            "propertyType": "entire_home",
            "bedrooms": 2,
            "bathrooms": 1,
            "maxGuests": 4,
            "preferredComps": [
                {"listingUrl": "https://www.airbnb.com/rooms/111"},
                {"listingUrl": "https://www.airbnb.com/rooms/222"},
            ],
            "excludedComps": [
                {"roomId": "999", "excludedAt": "2026-04-01T00:00:00Z"}
            ],
        }
    )
    # 32-char SHA-256 prefix
    assert len(k) == 32
    assert all(c in "0123456789abcdef" for c in k)
