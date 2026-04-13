from __future__ import annotations

import logging
import re
from typing import Any, Dict
from urllib.parse import quote

from worker.scraper.target_extractor import ListingSpec, clean
logger = logging.getLogger("worker")


def build_search_url(base_origin: str, location: str, checkin: str, checkout: str, adults: int) -> str:
    normalized_location = re.sub(r"\s*,\s*", ",", clean(location))
    q = quote(normalized_location, safe=",")
    logger.info(
        "check in date: %s, check out date: %s", checkin, checkout
    )
    return f"{base_origin}/s/{q}/homes?checkin={checkin}&checkout={checkout}&adults={adults}"


def parse_card_to_spec(card: Dict[str, Any]) -> ListingSpec:
    return ListingSpec(
        url=str(card.get("url") or ""),
        title=clean(str(card.get("title") or "")),
        location=clean(str(card.get("location") or "")),
        accommodates=card.get("accommodates"),
        bedrooms=card.get("bedrooms"),
        beds=card.get("beds"),
        baths=card.get("baths"),
        property_type=str(card.get("property_type") or ""),
        nightly_price=card.get("nightly_price"),
        rating=card.get("rating"),
        reviews=card.get("reviews"),
        amenities=list(card.get("amenities") or []),
        scrape_nights=int(card.get("scrape_nights") or 1),
        price_kind=str(card.get("price_kind") or "unknown"),
        lat=card.get("lat"),
        lng=card.get("lng"),
    )


def collect_search_cards(*args, **kwargs):
    raise RuntimeError("collect_search_cards is obsolete in HTTP-based scraper")


def wait_for_cards(*args, **kwargs):
    raise RuntimeError("wait_for_cards is obsolete in HTTP-based scraper")


def scroll_and_collect(*args, **kwargs):
    raise RuntimeError("scroll_and_collect is obsolete in HTTP-based scraper")


def extract_comp_coords(*args, **kwargs):
    return {}
