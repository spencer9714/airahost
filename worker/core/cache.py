"""
Cache key computation and get/set helpers.

Uses pricing_cache table for deduplication.
Key = stable hash of (listing_url or address + attributes + dateRange + discountPolicy).
TTL defaults to 24 hours.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from supabase import Client

CACHE_TTL_HOURS = 24


def compute_cache_key(
    address: str,
    attributes: Dict[str, Any],
    start_date: str,
    end_date: str,
    discount_policy: Dict[str, Any],
    listing_url: Optional[str] = None,
    input_mode: str = "criteria",
) -> str:
    """
    Compute a stable cache key from report inputs.
    Canonical JSON ensures deterministic ordering.
    """
    payload = {
        "inputMode": input_mode,
        "listing_url": listing_url or "",
        "address": address,
        "propertyType": attributes.get("propertyType", ""),
        "bedrooms": attributes.get("bedrooms", 0),
        "bathrooms": attributes.get("bathrooms", 0),
        "maxGuests": attributes.get("maxGuests", 0),
        "startDate": start_date,
        "endDate": end_date,
        "weeklyDiscountPct": discount_policy.get("weeklyDiscountPct", 0),
        "monthlyDiscountPct": discount_policy.get("monthlyDiscountPct", 0),
        "refundable": discount_policy.get("refundable", True),
        "nonRefundableDiscountPct": discount_policy.get("nonRefundableDiscountPct", 0),
        "stackingMode": discount_policy.get("stackingMode", "compound"),
        "maxTotalDiscountPct": discount_policy.get("maxTotalDiscountPct", 40),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def get_cached(
    client: Client,
    cache_key: str,
) -> Optional[Tuple[Dict[str, Any], list]]:
    """
    Look up a valid (non-expired) cache entry.
    Returns (summary, calendar) or None.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    result = (
        client.table("pricing_cache")
        .select("summary, calendar")
        .eq("cache_key", cache_key)
        .gt("expires_at", now_iso)
        .limit(1)
        .execute()
    )
    rows = result.data
    if rows and len(rows) > 0:
        row = rows[0]
        return row["summary"], row["calendar"]
    return None


def set_cached(
    client: Client,
    cache_key: str,
    summary: Dict[str, Any],
    calendar: list,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Insert or update a cache entry. Upserts on cache_key.
    """
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    payload = {
        "cache_key": cache_key,
        "expires_at": expires_at,
        "summary": summary,
        "calendar": calendar,
        "meta": meta or {},
    }
    client.table("pricing_cache").upsert(payload, on_conflict="cache_key").execute()
