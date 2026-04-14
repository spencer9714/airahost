from __future__ import annotations

import re
from typing import Any, Dict, Optional

from worker.scraper.target_extractor import ListingSpec, clean


_LOCATION_IN_RE = re.compile(
    r"(?:entire\s+\w+|private\s+room|shared\s+room|room|hotel\s+room)\s+in\s+(.+)",
    re.IGNORECASE,
)
_BADGE_SUFFIX_RE = re.compile(r"\s*[·•★].*$")
_GUEST_RE = re.compile(r"(\d+)(?:\+)?\s*guests?", re.IGNORECASE)
_BEDROOM_RE = re.compile(r"(\d+)\s*(?:bedrooms?|bd|bdrm)\b", re.IGNORECASE)
_BED_RE = re.compile(r"(\d+)\s*beds?\b", re.IGNORECASE)
_BATH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:baths?|ba)\b", re.IGNORECASE)


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _extract_first_int(text: str, pattern: re.Pattern[str]) -> Optional[int]:
    m = pattern.search(text or "")
    return _to_int(m.group(1)) if m else None


def _extract_first_float(text: str, pattern: re.Pattern[str]) -> Optional[float]:
    m = pattern.search(text or "")
    return _to_float(m.group(1)) if m else None


def extract_search_result_location(text: str) -> str:
    """
    Extract normalized location from a search card text block.
    Examples:
      - "Entire home in Edmonds, Washington\\n10 guests..." -> "Edmonds, Washington"
      - "Private room in Seattle · Guest favorite · ★4.9" -> "Seattle"
    """
    t = clean(text)
    if not t:
        return ""
    first_line = clean((t.splitlines() or [""])[0])
    m = _LOCATION_IN_RE.search(first_line)
    if not m:
        return ""
    raw = clean(m.group(1))
    # Strip card badges/ratings trailing the location.
    return clean(_BADGE_SUFFIX_RE.sub("", raw))


def parse_card_to_spec(card: Dict[str, Any]) -> ListingSpec:
    text = clean(str(card.get("text") or ""))
    location = clean(str(card.get("location") or "")) or extract_search_result_location(text)

    accommodates = card.get("accommodates")
    bedrooms = card.get("bedrooms")
    beds = card.get("beds")
    baths = card.get("baths")

    if accommodates is None:
        accommodates = _extract_first_int(text, _GUEST_RE)
    if bedrooms is None:
        bedrooms = _extract_first_int(text, _BEDROOM_RE)
    if beds is None:
        beds = _extract_first_int(text, _BED_RE)
    if baths is None:
        baths = _extract_first_float(text, _BATH_RE)

    # Backward-compatible price handling:
    # - Prefer explicitly extracted price_value when present
    # - Enforce [10, 10000] guard
    # - If nightly_price is provided directly, accept as-is (legacy callers)
    nightly_price = card.get("nightly_price")
    if nightly_price is None:
        price_value = card.get("price_value")
        pv = _to_float(price_value)
        if pv is not None and 10 <= pv <= 10000:
            kind = str(card.get("price_kind") or "unknown")
            scrape_nights = int(card.get("scrape_nights") or card.get("price_nights") or 1)
            if kind.startswith("trip_total_"):
                nights = int(card.get("price_nights") or scrape_nights or 1)
                nights = max(1, nights)
                nightly_price = round(pv / nights, 2)
            else:
                nightly_price = pv
        else:
            nightly_price = None

    return ListingSpec(
        url=str(card.get("url") or ""),
        title=clean(str(card.get("title") or "")),
        location=location,
        accommodates=accommodates,
        bedrooms=bedrooms,
        beds=beds,
        baths=baths,
        property_type=str(card.get("property_type") or ""),
        nightly_price=nightly_price,
        rating=card.get("rating"),
        reviews=card.get("reviews"),
        amenities=list(card.get("amenities") or []),
        scrape_nights=int(card.get("scrape_nights") or card.get("price_nights") or 1),
        price_kind=str(card.get("price_kind") or "unknown"),
        lat=card.get("lat"),
        lng=card.get("lng"),
    )
