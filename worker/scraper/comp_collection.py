from __future__ import annotations

import logging
import math
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from worker.core.similarity import comp_urls_match
from worker.scraper.parsers import (
    parse_pdp_baths_property_type_fast,
    parse_search_listing_context,
    parse_search_response,
)
from worker.scraper.target_extractor import ListingSpec, normalize_property_type

logger = logging.getLogger("worker.scraper.comp_collection")
_ROOM_ID_RE = re.compile(r"/rooms/(\d+)")


def _compute_bbox_from_radius_km(
    center_lat: float,
    center_lng: float,
    radius_km: float,
) -> Tuple[float, float, float, float]:
    """Return (ne_lat, ne_lng, sw_lat, sw_lng) for a center/radius."""
    lat_delta = float(radius_km) / 111.32
    cos_lat = max(0.01, math.cos(math.radians(center_lat)))
    lng_delta = float(radius_km) / (111.32 * cos_lat)
    ne_lat = center_lat + lat_delta
    ne_lng = center_lng + lng_delta
    sw_lat = center_lat - lat_delta
    sw_lng = center_lng - lng_delta
    return ne_lat, ne_lng, sw_lat, sw_lng


def _matches_structural_filters(
    comp: ListingSpec,
    *,
    target_accommodates: Optional[int],
    target_bedrooms: Optional[int],
    target_beds: Optional[int],
    target_baths: Optional[float],
) -> bool:
    """
    Hard filter for comp eligibility.

    Current policy: guests/accommodates only.
    Bedrooms/beds/baths remain signals for similarity scoring, but are not
    used to exclude candidates from the comp pool.
    """
    if target_accommodates is not None:
        if comp.accommodates is None or int(comp.accommodates) < int(target_accommodates):
            return False
    return True


def _map_search_row_to_spec(
    listing_id: str,
    row: Dict[str, Any],
    base_origin: str,
    query_nights: int,
) -> ListingSpec:
    nightly = row.get("nightly_price")
    total = row.get("total_price")
    price_nights = int(row.get("price_nights") or 1)
    price_kind = "unknown"
    scrape_nights = max(1, int(query_nights or 1))
    query_total_price = None
    if isinstance(nightly, (int, float)) and nightly > 0:
        effective_nightly = float(nightly)
        price_kind = "nightly_from_search"
        if price_nights > 1:
            scrape_nights = max(scrape_nights, int(price_nights))
    elif isinstance(total, (int, float)) and total > 0:
        nights = max(1, price_nights)
        # If this card came from a 2-night query but the parser did not
        # capture "for 2 nights", force ÷2 normalization for trip totals.
        if int(query_nights or 1) == 2 and nights <= 1:
            nights = 2
        if int(query_nights or 1) == 1 and nights > 1:
            # Strict 1-night mode: reject multi-night totals to avoid 2-night bias.
            effective_nightly = None
            price_kind = "multi_night_total_skipped"
            scrape_nights = nights
        else:
            effective_nightly = round(float(total) / nights, 2)
            price_kind = "trip_total_from_search"
            scrape_nights = nights
            query_total_price = float(total)
    else:
        effective_nightly = None

    return ListingSpec(
        url=f"{base_origin.rstrip('/')}/rooms/{listing_id}",
        title=str(row.get("title") or ""),
        location=str(row.get("location") or ""),
        accommodates=row.get("accommodates"),
        bedrooms=row.get("bedrooms"),
        beds=row.get("beds"),
        baths=row.get("baths"),
        property_type=str(row.get("property_type") or ""),
        nightly_price=effective_nightly,
        query_total_price=query_total_price,
        currency=str(row.get("currency") or "USD"),
        rating=row.get("rating"),
        reviews=row.get("reviews"),
        amenities=list(row.get("amenities") or []),
        lat=row.get("lat"),
        lng=row.get("lng"),
        scrape_nights=scrape_nights,
        price_kind=price_kind,
    )


def _extract_listing_id_from_url(url: str) -> Optional[str]:
    if not isinstance(url, str) or not url:
        return None
    m = _ROOM_ID_RE.search(url)
    return m.group(1) if m else None


def _enrich_comps_baths_and_property_type_from_pdp(
    client,
    comps: List[ListingSpec],
    *,
    checkin: str,
    checkout: str,
    adults: int,
) -> None:
    """
    Enrich comps with accurate baths/property_type from individual PDP fetches.
    Fast parser exits after reading just those two fields.
    """
    if not comps or not hasattr(client, "get_listing_details"):
        return

    # Strict PDP-only mode for these two structural fields:
    # discard any StaysSearch-derived values before enrichment.
    for comp in comps:
        comp.baths = None
        comp.property_type = ""

    updated = 0
    attempted = 0
    for comp in comps:
        lid = _extract_listing_id_from_url(str(comp.url or ""))
        if not lid:
            continue
        attempted += 1
        try:
            pdp_data = client.get_listing_details(
                lid,
                checkin=checkin,
                checkout=checkout,
                adults=adults,
            )
            fast = parse_pdp_baths_property_type_fast(pdp_data)
            baths = fast.get("baths")
            ptype_raw = fast.get("property_type")
            ptype_norm = normalize_property_type(str(ptype_raw or ""))

            changed = False
            if isinstance(baths, (int, float)):
                b = float(baths)
                if comp.baths != b:
                    comp.baths = b
                    changed = True
            if isinstance(ptype_norm, str) and ptype_norm.strip():
                if comp.property_type != ptype_norm.strip():
                    comp.property_type = ptype_norm.strip()
                    changed = True
            if changed:
                updated += 1
        except Exception:
            continue

    if attempted:
        logger.info(
            "[comp_collection] PDP structural enrichment attempted=%s updated=%s",
            attempted,
            updated,
        )


def collect_search_comps(
    client,
    search_location: str,
    base_origin: str,
    date_i: date,
    adults: int,
    *,
    max_scroll_rounds: int,
    max_cards: int,
    rate_limit_seconds: float,
    timeout_ms: int = 15000,
    exclude_url: Optional[str] = None,
    log_prefix: str = "search",
    page_offsets: Optional[List[int]] = None,
    center_lat: Optional[float] = None,
    center_lng: Optional[float] = None,
    map_radius_km: Optional[float] = None,
    target_accommodates: Optional[int] = None,
    target_bedrooms: Optional[int] = None,
    target_beds: Optional[int] = None,
    target_baths: Optional[float] = None,
    pdp_structural_enrichment: bool = False,
    prefer_two_night: bool = False,
    prefer_one_night: bool = False,
) -> Tuple[List[ListingSpec], int]:
    checkin_str = date_i.isoformat()
    base_offsets = page_offsets or [0]
    # Daily path usually calls with page_offsets=None (single-page).
    # When priced comps are zero, retry one deeper pass to match fixed-pool depth better.
    deep_offsets = [0, max(1, int(max_cards)), max(1, int(max_cards)) * 2]
    offset_sets: List[List[int]] = [base_offsets]
    if page_offsets is None:
        offset_sets.append(deep_offsets)
    fallback_unpriced_comps: List[ListingSpec] = []
    fallback_unpriced_query_nights: int = 1
    map_bounds: Optional[Tuple[float, float, float, float]] = None
    if (
        center_lat is not None
        and center_lng is not None
        and isinstance(map_radius_km, (int, float))
        and float(map_radius_km) > 0
    ):
        map_bounds = _compute_bbox_from_radius_km(
            float(center_lat),
            float(center_lng),
            float(map_radius_km),
        )

    # Default: 1-night primary, 2-night fallback.
    # Optional modes:
    #   - prefer_two_night=True  -> 2-night only
    #   - prefer_one_night=True  -> 1-night only
    if prefer_two_night:
        query_night_sequence = (2,)
    elif prefer_one_night:
        query_night_sequence = (1,)
    else:
        query_night_sequence = (1, 2)
    for query_nights in query_night_sequence:
        checkout_str = (date_i + timedelta(days=query_nights)).isoformat()
        last_reason = "unknown"
        for offset_set_idx, offsets in enumerate(offset_sets):
            listing_ids: List[str] = []
            context: Dict[str, Dict[str, Any]] = {}
            seen_ids: set[str] = set()
            page_ok = False

            for offset in offsets:
                overrides = {
                    "checkin": checkin_str,
                    "checkout": checkout_str,
                    "adults": adults,
                    "query": search_location,
                    "locationSearch": search_location,
                    "location": search_location,
                    "dailySearch": True,
                    "itemsPerGrid": max_cards,
                }
                if center_lat is not None:
                    overrides["centerLat"] = center_lat
                if center_lng is not None:
                    overrides["centerLng"] = center_lng
                if map_bounds is not None:
                    ne_lat, ne_lng, sw_lat, sw_lng = map_bounds
                    overrides["searchByMap"] = True
                    overrides["neLat"] = ne_lat
                    overrides["neLng"] = ne_lng
                    overrides["swLat"] = sw_lat
                    overrides["swLng"] = sw_lng
                # Explicitly disable Guest Favorite filter for raw search requests.
                overrides["guestFavorite"] = False
                if target_accommodates is not None:
                    overrides["guests"] = int(target_accommodates)
                if offset > 0:
                    overrides["itemsOffset"] = offset
                logger.info(
                    "[%s] %s: query_nights=%s offset=%s",
                    log_prefix,
                    checkin_str,
                    query_nights,
                    offset,
                )
                status, search_data = client.search_listings_with_overrides(overrides)
                if status < 200 or status >= 300:
                    logger.warning(
                        "[%s] %s: search status=%s offset=%s",
                        log_prefix,
                        checkin_str,
                        status,
                        offset,
                    )
                    continue
                page_ok = True

                page_ids = parse_search_response(search_data)
                page_ctx = parse_search_listing_context(search_data)
                for lid in page_ids:
                    sid = str(lid)
                    if sid not in seen_ids:
                        listing_ids.append(sid)
                        seen_ids.add(sid)
                    row = page_ctx.get(sid, {})
                    existing = context.get(sid)
                    if existing is None:
                        context[sid] = row
                    else:
                        existing_has_price = bool(
                            (existing.get("nightly_price") or 0) > 0
                            or (existing.get("total_price") or 0) > 0
                        )
                        row_has_price = bool(
                            (row.get("nightly_price") or 0) > 0
                            or (row.get("total_price") or 0) > 0
                        )
                        if row_has_price and not existing_has_price:
                            context[sid] = row

            if not page_ok:
                continue

            if len(offsets) > 1:
                logger.info(
                    "[%s] %s: merged %s search results across %s offsets (query_nights=%s)",
                    log_prefix,
                    checkin_str,
                    len(listing_ids),
                    len(offsets),
                    query_nights,
                )

            parsed_comps: List[ListingSpec] = []
            for listing_id in listing_ids:
                row = context.get(str(listing_id), {})
                spec = _map_search_row_to_spec(str(listing_id), row, base_origin, query_nights)
                parsed_comps.append(spec)

            comps = parsed_comps
            self_excluded = 0
            if exclude_url:
                comps = [c for c in comps if not (c.url and comp_urls_match(c.url, exclude_url))]
                self_excluded = len(parsed_comps) - len(comps)
            structural_excluded = 0
            if target_accommodates is not None:
                before = len(comps)
                comps = [
                    c for c in comps
                    if _matches_structural_filters(
                        c,
                        target_accommodates=target_accommodates,
                        target_bedrooms=target_bedrooms,
                        target_beds=target_beds,
                        target_baths=target_baths,
                    )
                ]
                structural_excluded = before - len(comps)

            # Keep only listings with a positive nightly price AND marked available.
            priced: List[ListingSpec] = []
            unavailable_count = 0
            min_stay_blocked_count = 0
            for c in comps:
                lid = c.url.rsplit("/", 1)[-1] if c.url else ""
                row = context.get(str(lid), {})
                is_available = bool(row.get("is_available", True))
                min_nights = row.get("min_nights")
                if isinstance(min_nights, int) and min_nights > int(query_nights):
                    min_stay_blocked_count += 1
                if not is_available:
                    unavailable_count += 1
                has_price = bool(c.nightly_price and c.nightly_price > 0)
                # Availability heuristics can be noisy; if Airbnb returned a positive
                # price for this card, keep it even when availability was mis-flagged.
                effective_available = bool(is_available or has_price)
                if c.url and has_price and effective_available:
                    priced.append(c)

            if priced:
                if pdp_structural_enrichment:
                    _enrich_comps_baths_and_property_type_from_pdp(
                        client,
                        priced,
                        checkin=checkin_str,
                        checkout=checkout_str,
                        adults=adults,
                    )
                if self_excluded > 0 or structural_excluded > 0:
                    logger.info(
                        "[%s] %s: exclusions self=%s structural=%s",
                        log_prefix,
                        checkin_str,
                        self_excluded,
                        structural_excluded,
                    )
                return priced, query_nights
            if comps and not fallback_unpriced_comps:
                # Keep first non-empty candidate set even when prices are missing,
                # so downstream can still render a transparent report.
                fallback_unpriced_comps = comps
                fallback_unpriced_query_nights = query_nights

            reason = "unknown"
            if min_stay_blocked_count > 0:
                reason = "minimum_night_requirement_likely"
            elif unavailable_count > 0:
                reason = "sold_out_or_unavailable_likely"
            last_reason = reason

            # No priced comps from single-page daily query: retry once with deeper offsets.
            if (
                query_nights == 1
                and page_offsets is None
                and len(offsets) == 1
                and offset_set_idx == 0
            ):
                logger.info(
                    "[%s] %s: no priced comps on offset=0; retrying deeper offsets=%s "
                    "(query_nights=%s, reason=%s)",
                    log_prefix,
                    checkin_str,
                    deep_offsets,
                    query_nights,
                    reason,
                )
                continue

            break

        if query_nights == 1 and len(query_night_sequence) > 1:
            logger.info(
                "[%s] %s: no priced comps from 1-night query; retrying 2-night fallback (reason=%s)",
                log_prefix,
                checkin_str,
                last_reason,
            )

    if fallback_unpriced_comps:
        logger.info(
            "[%s] %s: returning %s comps without nightly prices (query_nights=%s)",
            log_prefix,
            checkin_str,
            len(fallback_unpriced_comps),
            fallback_unpriced_query_nights,
        )
        return fallback_unpriced_comps, fallback_unpriced_query_nights

    return [], 1
