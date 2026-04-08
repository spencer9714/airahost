"""
Shared market-comp search helper.

``collect_search_comps`` encapsulates the 2-night-primary / 1-night-fallback
Airbnb search loop used by both the standard day-query pipeline and the
benchmark-first pipeline.  Previously each pipeline carried its own copy of
this logic (~30 lines each); this module is the single source of truth.

Improvement over the previous benchmark path: coordinate extraction
(``extract_comp_coords``) is now applied in both pipelines, giving benchmark
market comps the same geo-distance data that standard comps have always had.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from worker.core.similarity import comp_urls_match
from worker.scraper.comparable_collector import (
    build_search_url,
    extract_comp_coords,
    parse_card_to_spec,
    scroll_and_collect,
    wait_for_cards,
)
from worker.scraper.target_extractor import ListingSpec

logger = logging.getLogger("worker.scraper.comp_collection")

_ROOM_ID_RE = re.compile(r"/rooms/(\d+)")

_DEFAULT_PAGE_TIMEOUT_MS: int = 15_000
_DEFAULT_PAUSE_MS: int = 600


def collect_search_comps(
    page,
    search_location: str,
    base_origin: str,
    date_i: date,
    adults: int,
    *,
    max_scroll_rounds: int,
    max_cards: int,
    rate_limit_seconds: float,
    timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
    exclude_url: Optional[str] = None,
    log_prefix: str = "search",
) -> Tuple[List[ListingSpec], int]:
    """
    Execute a 2-night-primary / 1-night-fallback Airbnb market search for *date_i*.

    Strategy (mirrors day_query.py's 2-night-primary rationale):
      1. Try a 2-night query (checkin=date_i, checkout=date_i+2).
         Listings with minimum_stay=2 only appear in 2-night+ queries, so this
         broadens the comp pool vs a pure 1-night search.
      2. Fall back to a 1-night query only when the 2-night search returns zero
         priced comps (rare).

    Coordinate extraction is applied after card parsing so that comps carry
    approximate lat/lng for downstream geo-distance filtering.  If extraction
    fails (e.g. page has no embedded map state), it is silently ignored.

    Args:
        page:              Playwright page object (already connected to CDP).
        search_location:   Airbnb search location string (address / city / ZIP).
        base_origin:       Base URL for Airbnb (e.g. "https://www.airbnb.com").
        date_i:            Check-in date for this day query.
        adults:            Number of guests.
        max_scroll_rounds: How many scroll rounds to perform before stopping.
        max_cards:         Maximum number of listing cards to collect.
        rate_limit_seconds: Seconds to pause between page interactions.
        timeout_ms:        Playwright navigation timeout in milliseconds.
        exclude_url:       When provided, any parsed comp whose URL matches this
                           value (via ``comp_urls_match``) is excluded from the
                           returned list.  Used by the standard path to remove
                           the target listing from its own comp pool.
                           The benchmark path leaves this ``None`` so the
                           benchmark listing stays in results for Stage-1 capture.
        log_prefix:        Logger tag prefix for contextual log lines.

    Returns:
        ``(priced_comps, query_nights_used)`` where:
        - ``priced_comps`` — ``ListingSpec`` objects with a non-zero
          ``nightly_price``, optional coords populated, optional self-listing
          removed;
        - ``query_nights_used`` — 2 if the 2-night pass found results, else 1.
    """
    checkin_str = date_i.isoformat()

    for query_nights in (2, 1):
        checkout_str = (date_i + timedelta(days=query_nights)).isoformat()
        search_url = build_search_url(
            base_origin, search_location, checkin_str, checkout_str, adults,
        )
        logger.info(
            f"[{log_prefix}] {checkin_str}: {query_nights}-night search (primary=2)"
        )

        page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
        wait_for_cards(page)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        raw_cards = scroll_and_collect(
            page,
            max_rounds=max_scroll_rounds,
            max_cards=max_cards,
            pause_ms=_DEFAULT_PAUSE_MS,
            rate_limit_seconds=rate_limit_seconds,
            stay_nights=query_nights,
        )

        # Best-effort: extract approximate lat/lng from page map state.
        # Returns {} on any parse failure — never blocks collection.
        coord_map: Dict[str, Any] = {}
        try:
            coord_map = extract_comp_coords(page)
        except Exception:
            pass

        parsed_comps = [parse_card_to_spec(c) for c in raw_cards]

        if coord_map:
            for spec in parsed_comps:
                room_m = _ROOM_ID_RE.search(spec.url or "")
                if room_m:
                    pair = coord_map.get(room_m.group(1))
                    if pair:
                        spec.lat, spec.lng = pair[0], pair[1]

        # Optional self-exclusion (standard path only; benchmark keeps all comps)
        comps = parsed_comps
        self_excluded = 0
        if exclude_url:
            comps = [
                c for c in comps
                if not (c.url and comp_urls_match(c.url, exclude_url))
            ]
            self_excluded = len(parsed_comps) - len(comps)

        priced = [
            c for c in comps
            if c.url and c.nightly_price and c.nightly_price > 0
        ]

        logger.info(
            "[%s] %s: query_nights=%d raw_cards=%d parsed=%d%s priced=%d",
            log_prefix, checkin_str, query_nights, len(raw_cards),
            len(parsed_comps),
            f" self_excluded={self_excluded}" if self_excluded else "",
            len(priced),
        )

        if priced:
            return priced, query_nights

    return [], 1
