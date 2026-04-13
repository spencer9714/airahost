from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from worker.core.similarity import comp_urls_match
from worker.scraper.parsers import parse_search_listing_context, parse_search_response
from worker.scraper.target_extractor import ListingSpec

logger = logging.getLogger("worker.scraper.comp_collection")


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
    scrape_nights = 1
    if isinstance(nightly, (int, float)) and nightly > 0:
        effective_nightly = float(nightly)
        price_kind = "nightly_from_search"
    elif isinstance(total, (int, float)) and total > 0:
        nights = max(1, price_nights)
        if int(query_nights or 1) == 1 and nights > 1:
            # Strict 1-night mode: reject multi-night totals to avoid 2-night bias.
            effective_nightly = None
            price_kind = "multi_night_total_skipped"
            scrape_nights = nights
        else:
            effective_nightly = round(float(total) / nights, 2)
            price_kind = "trip_total_from_search"
            scrape_nights = nights
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
        rating=row.get("rating"),
        reviews=row.get("reviews"),
        amenities=list(row.get("amenities") or []),
        lat=row.get("lat"),
        lng=row.get("lng"),
        scrape_nights=scrape_nights,
        price_kind=price_kind,
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
) -> Tuple[List[ListingSpec], int]:
    checkin_str = date_i.isoformat()

    # 1-night primary keeps prices aligned with nightly card pricing.
    # 2-night fallback is used only when 1-night has no usable priced listings
    # (commonly because of minimum-stay constraints).
    for query_nights in (1, 2):
        checkout_str = (date_i + timedelta(days=query_nights)).isoformat()
        status, search_data = client.search_listings_with_overrides(
            {
                "checkin": checkin_str,
                "checkout": checkout_str,
                "adults": adults,
                "query": search_location,
                "itemsPerGrid": max_cards,
            }
        )
        if status < 200 or status >= 300:
            logger.warning("[%s] %s: search status=%s", log_prefix, checkin_str, status)
            continue

        listing_ids = parse_search_response(search_data)
        context = parse_search_listing_context(search_data)
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
            if c.url and c.nightly_price and c.nightly_price > 0 and is_available:
                priced.append(c)

        if priced:
            return priced, query_nights
        if query_nights == 1:
            reason = "unknown"
            if min_stay_blocked_count > 0:
                reason = "minimum_night_requirement_likely"
            elif unavailable_count > 0:
                reason = "sold_out_or_unavailable_likely"
            logger.info(
                "[%s] %s: no priced comps from 1-night query; retrying 2-night fallback (reason=%s)",
                log_prefix,
                checkin_str,
                reason,
            )

    return [], 1
