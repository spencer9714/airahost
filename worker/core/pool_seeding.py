"""
Comparable Pool Seeding — Phase 2 (V2 Spec)

After each successful pricing run, this module bootstraps / updates the
per-listing comparable pool in `comparable_pool_entries`.

Responsibilities:
  - Upsert top comps from the just-completed run into the pool table.
  - Propagate Layer 1 price-sanity signals (outlier_count / total_observations).
  - Update `saved_listings` pool-level bookkeeping fields.

Design constraints:
  - All DB calls use the service role key (bypasses RLS).
  - Non-fatal: seeding failures are logged and swallowed so that a DB hiccup
    never causes the primary pricing report to roll back.
  - Idempotent: re-running the same report a second time converges to the
    same pool state rather than inflating counts.
  - Phase 2 only seeds the pool; pool evolution (replacement rules,
    anti-churn, state-machine transitions) is deferred to Phase 4.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("worker.core.pool_seeding")

# Max entries to seed per run (top-N by similarity)
_POOL_MAX_SIZE: int = 20

# Airbnb room ID regex — matches /rooms/<digits> in any URL
_ROOM_ID_RE = re.compile(r"/rooms/(\d+)")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def seed_pool_from_report(
    client,  # supabase.Client
    saved_listing_id: str,
    comparable_listings: List[Dict[str, Any]],
) -> None:
    """
    Upsert the top comparable listings from a completed pricing run into
    `comparable_pool_entries`, then refresh the pool stats on `saved_listings`.

    Args:
        client: Supabase service-role client.
        saved_listing_id: UUID of the linked saved listing.
        comparable_listings: The `comparableListings` list from transparent_result.
            Each entry is a dict produced by _build_daily_transparent_result().
    """
    if not comparable_listings:
        logger.debug("[pool_seeding] No comparableListings — skipping")
        return

    # Filter out entries with no URL (synthetic injections may lack IDs)
    candidates = [c for c in comparable_listings if _extract_airbnb_id(c.get("url") or "")]

    # Sort by similarity DESC, cap at pool max
    candidates.sort(key=lambda c: float(c.get("similarity") or 0.0), reverse=True)
    candidates = candidates[:_POOL_MAX_SIZE]

    if not candidates:
        logger.debug("[pool_seeding] No valid candidates after filtering — skipping")
        return

    try:
        _upsert_pool_entries(client, saved_listing_id, candidates)
        _refresh_pool_stats(client, saved_listing_id)
        logger.info(
            f"[pool_seeding] Seeded {len(candidates)} entries "
            f"for listing {saved_listing_id}"
        )
    except Exception as exc:
        logger.error(
            f"[pool_seeding] Failed to seed pool for listing {saved_listing_id}: {exc}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_airbnb_id(url: str) -> Optional[str]:
    """Return the numeric Airbnb room ID from a URL, or None."""
    m = _ROOM_ID_RE.search(url or "")
    return m.group(1) if m else None


def _upsert_pool_entries(
    client,
    saved_listing_id: str,
    candidates: List[Dict[str, Any]],
) -> None:
    """
    Upsert each candidate into comparable_pool_entries.

    Strategy:
      - If an active/degraded entry already exists for the airbnb_listing_id,
        UPDATE it (increment counts, refresh snapshot fields).
      - If no active entry exists, INSERT a new one.

    We do this in Python (fetch-then-upsert) rather than a single SQL UPSERT
    because we need to increment `tenure_runs` and `total_observations`
    conditionally, which is awkward to express cleanly in a single upsert
    without a custom SQL function.
    """
    # Fetch existing active/degraded entries for this listing in one query
    existing_result = (
        client.table("comparable_pool_entries")
        .select("id, airbnb_listing_id, tenure_runs, total_observations, outlier_count, status")
        .eq("saved_listing_id", saved_listing_id)
        .in_("status", ["active", "degraded"])
        .execute()
    )
    existing: Dict[str, Dict[str, Any]] = {
        row["airbnb_listing_id"]: row
        for row in (existing_result.data or [])
    }

    inserts: List[Dict[str, Any]] = []
    for comp in candidates:
        airbnb_id = _extract_airbnb_id(comp.get("url") or "")
        if not airbnb_id:
            continue

        sim = float(comp.get("similarity") or 0.0)
        is_outlier = bool(comp.get("priceOutlier"))

        snapshot = _build_snapshot(comp)

        if airbnb_id in existing:
            row = existing[airbnb_id]
            new_total = row["total_observations"] + 1
            new_outlier = row["outlier_count"] + (1 if is_outlier else 0)
            price_reliability = _compute_price_reliability(new_total, new_outlier)

            client.table("comparable_pool_entries").update({
                "similarity_score": sim,
                "tenure_runs": row["tenure_runs"] + 1,
                "total_observations": new_total,
                "outlier_count": new_outlier,
                "price_reliability_score": price_reliability,
                "status": "active",  # re-activate degraded entries that reappear
                **snapshot,
            }).eq("id", row["id"]).execute()
        else:
            outlier_count = 1 if is_outlier else 0
            inserts.append({
                "saved_listing_id": saved_listing_id,
                "airbnb_listing_id": airbnb_id,
                "similarity_score": sim,
                "pool_score": sim,          # initial pool_score = similarity
                "effective_rank_score": sim,
                "total_observations": 1,
                "outlier_count": outlier_count,
                "price_reliability_score": _compute_price_reliability(1, outlier_count),
                **snapshot,
            })

    if inserts:
        client.table("comparable_pool_entries").insert(inserts).execute()


def _build_snapshot(comp: Dict[str, Any]) -> Dict[str, Any]:
    """Extract listing-attribute snapshot fields from a comparableListings entry."""
    snap: Dict[str, Any] = {
        "last_seen_at": "now()",
    }
    if comp.get("url"):
        snap["listing_url"] = str(comp["url"])
    if comp.get("title"):
        snap["title"] = str(comp["title"])
    price = comp.get("nightlyPrice")
    if isinstance(price, (int, float)) and price > 0:
        snap["last_nightly_price"] = float(price)
    if comp.get("propertyType"):
        snap["property_type"] = str(comp["propertyType"])
    if isinstance(comp.get("bedrooms"), (int, float)):
        snap["bedrooms"] = int(comp["bedrooms"])
    if isinstance(comp.get("baths"), (int, float)):
        snap["baths"] = float(comp["baths"])
    if isinstance(comp.get("accommodates"), (int, float)):
        snap["accommodates"] = int(comp["accommodates"])
    if isinstance(comp.get("beds"), (int, float)):
        snap["beds"] = int(comp["beds"])
    if comp.get("location"):
        snap["location"] = str(comp["location"])
    if isinstance(comp.get("rating"), (int, float)):
        snap["rating"] = float(comp["rating"])
    if isinstance(comp.get("reviews"), (int, float)):
        snap["reviews"] = int(comp["reviews"])
    # Phase 3A: geographic coordinates and distance (best-effort, from day_query payload)
    if isinstance(comp.get("lat"), (int, float)):
        snap["comp_lat"] = float(comp["lat"])
    if isinstance(comp.get("lng"), (int, float)):
        snap["comp_lng"] = float(comp["lng"])
    if isinstance(comp.get("distanceKm"), (int, float)):
        snap["distance_to_target_km"] = float(comp["distanceKm"])
    return snap


def _compute_price_reliability(total_observations: int, outlier_count: int) -> float:
    """
    Layer 2 price reliability score: fraction of observations that were NOT
    flagged as price outliers by Layer 1.

    Starts at 1.0 for new entries (no observations yet that flagged it).
    Floor at 0.0.
    """
    if total_observations == 0:
        return 1.0
    return max(0.0, 1.0 - (outlier_count / total_observations))


def _refresh_pool_stats(client, saved_listing_id: str) -> None:
    """Update pool-level bookkeeping columns on saved_listings."""
    count_result = (
        client.table("comparable_pool_entries")
        .select("id", count="exact")
        .eq("saved_listing_id", saved_listing_id)
        .eq("status", "active")
        .execute()
    )
    active_count = count_result.count or 0
    low_coverage = active_count < 5  # fewer than 5 active comps = low coverage

    client.table("saved_listings").update({
        "comp_pool_last_built_at": "now()",
        "comp_pool_version": _next_pool_version(client, saved_listing_id),
        "comp_pool_active_size": active_count,
        "comp_pool_low_coverage": low_coverage,
    }).eq("id", saved_listing_id).execute()


def _next_pool_version(client, saved_listing_id: str) -> int:
    """Fetch current comp_pool_version and return current + 1."""
    result = (
        client.table("saved_listings")
        .select("comp_pool_version")
        .eq("id", saved_listing_id)
        .single()
        .execute()
    )
    current = (result.data or {}).get("comp_pool_version") or 0
    return current + 1
