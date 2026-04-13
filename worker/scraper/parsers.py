import re
import base64
from typing import Any, Dict, List, Optional


def _find_keys(obj: Any, target_key: str) -> List[Any]:
    """Recursively search for a target key in a nested JSON object."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == target_key:
                results.append(v)
            elif isinstance(v, (dict, list)):
                results.extend(_find_keys(v, target_key))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_keys(item, target_key))
    return results


def _walk_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_dicts(item)


def _walk_strings(obj: Any):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_strings(item)
    elif isinstance(obj, str):
        yield obj


def _get_nested(data: Dict[str, Any], path: List[str]) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _decode_graphql_id(encoded: str) -> Optional[str]:
    """
    Decode global GraphQL id like:
    'RGVtYW5kU3RheUxpc3Rpbmc6MTYyMzMxODQwMDE2NTU1MDM0Mg=='
    -> 'DemandStayListing:1623318400165550342'
    """
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return None
    if ":" not in decoded:
        return None
    numeric = decoded.split(":")[-1]
    return numeric if numeric.isdigit() else None


def parse_search_response(data: Dict[str, Any]) -> List[str]:
    """Extract listing IDs from staysSearch.results.searchResults[*].demandStayListing.id."""
    listing_ids: List[str] = []
    results = _get_nested(data, ["data", "presentation", "staysSearch", "results", "searchResults"])
    if not isinstance(results, list):
        return listing_ids

    for r in results:
        if not isinstance(r, dict):
            continue
        demand = r.get("demandStayListing")
        if not isinstance(demand, dict):
            continue
        lid = _decode_graphql_id(demand.get("id"))
        if lid and lid not in listing_ids:
            listing_ids.append(lid)

    return listing_ids


def _parse_price_string(price_str: str) -> Optional[float]:
    """Extract numeric price from text like '$1,200 CAD' or 'US$241 / night'."""
    value, _prefix = _parse_price_with_prefix(price_str)
    return value


def _parse_price_with_prefix(price_str: str) -> tuple[Optional[float], Optional[str]]:
    """
    Extract numeric price plus the raw prefix string immediately before it.

    Prefix is intentionally not validated against known currencies.
    """
    if not price_str:
        return None, None
    text = str(price_str).replace("\xa0", " ")

    # Context-first match without requiring a specific prefix string:
    # "241 / night", "241 per night", "241 total", "241 for 2 nights"
    contextual = re.search(
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:/\s*night|per\s+night|night|total|for\s+\d+\s+nights?)",
        text,
        re.I,
    )
    if contextual:
        numeric = contextual.group(1).replace(",", "").strip()
        if numeric:
            prefix_match = re.search(r"([^\d\s]+)\s*$", text[:contextual.start(1)])
            prefix = prefix_match.group(1).strip() if prefix_match else None
            return float(numeric), prefix

    # Generic fallback: first plausible numeric token.
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        numeric = m.group(0).replace(",", "").strip()
        if not numeric:
            continue
        try:
            value = float(numeric)
        except Exception:
            continue
        if value > 0:
            prefix_match = re.search(r"([^\d\s]+)\s*$", text[:m.start()])
            prefix = prefix_match.group(1).strip() if prefix_match else None
            return value, prefix
    return None, None


def _extract_price_nights(text: str) -> int:
    if not isinstance(text, str) or not text:
        return 1
    m = re.search(r"for\s+(\d+)\s+nights?", text.lower())
    if not m:
        return 1
    try:
        n = int(m.group(1))
        return n if n > 0 else 1
    except Exception:
        return 1


def _parse_dollar_amount_currency(text: str) -> tuple[Optional[float], Optional[str]]:
    """
    Parse strict Airbnb primaryLine.price shapes like '$173 CAD' / 'US$241 CAD'.
    Returns (amount, currency_code).
    """
    if not isinstance(text, str) or not text.strip():
        return None, None
    s = text.replace("\xa0", " ").strip()
    m = re.search(
        r"^(?:US)?\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s+([A-Za-z]{3,8})\b",
        s,
    )
    if not m:
        return None, None
    try:
        amount = float(m.group(1).replace(",", ""))
    except Exception:
        return None, None
    currency = m.group(2).upper()
    return (amount if amount > 0 else None), currency


_GUEST_RE = re.compile(r"(\d+)\s*(?:guests?|guest|people|person)\b", re.I)
_BEDROOM_RE = re.compile(r"(\d+)\s*(?:bedrooms?|bedroom|br)\b", re.I)
_BED_RE = re.compile(r"(\d+)\s*beds?\b", re.I)
_BATH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*baths?\b", re.I)
_MIN_NIGHTS_RE = re.compile(r"(?:minimum|min\.?|at least)\s*(?:stay\s*of\s*)?(\d+)\s*nights?", re.I)


def _normalize_property_type_from_text(text: str) -> str:
    t = str(text or "").lower()
    if "private room" in t:
        return "private_room"
    if "shared room" in t:
        return "shared_room"
    if re.search(r"\bentire\b", t):
        return "entire_home"
    return ""


def _extract_structural_context_from_search_result(r: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "location": None,
        "lat": None,
        "lng": None,
        "accommodates": None,
        "bedrooms": None,
        "beds": None,
        "baths": None,
        "property_type": None,
        "rating": None,
        "reviews": None,
        "amenities": [],
    }

    # First pass: read explicit typed fields if present in nested objects.
    for d in _walk_dicts(r):
        if out["accommodates"] is None:
            for key in ("personCapacity", "maxGuestCapacity", "maxGuests", "guestCapacity", "accommodates"):
                val = d.get(key)
                if isinstance(val, (int, float)) and int(val) > 0:
                    out["accommodates"] = int(val)
                    break

        if out["bedrooms"] is None:
            for key in ("bedroomCount", "bedrooms", "numBedrooms"):
                val = d.get(key)
                if isinstance(val, (int, float)) and int(val) >= 0:
                    out["bedrooms"] = int(val)
                    break

        if out["beds"] is None:
            for key in ("bedCount", "beds", "numBeds"):
                val = d.get(key)
                if isinstance(val, (int, float)) and int(val) >= 0:
                    out["beds"] = int(val)
                    break

        if out["baths"] is None:
            for key in ("bathroomCount", "bathrooms", "numBathrooms"):
                val = d.get(key)
                if isinstance(val, (int, float)) and float(val) >= 0:
                    out["baths"] = float(val)
                    break

        if not out["property_type"]:
            for key in ("roomTypeCategory", "spaceType", "propertyType", "propertyTypeLabel", "typeName"):
                val = d.get(key)
                if isinstance(val, str) and val.strip():
                    norm = _normalize_property_type_from_text(val)
                    if norm:
                        out["property_type"] = norm
                        break

        if out["rating"] is None:
            for key in ("avgRating", "rating", "starRating", "averageRating"):
                val = d.get(key)
                if isinstance(val, (int, float)) and 0 < float(val) <= 5:
                    out["rating"] = round(float(val), 2)
                    break

        if out["reviews"] is None:
            for key in ("reviewCount", "reviewsCount", "numberOfReviews", "reviews"):
                val = d.get(key)
                if isinstance(val, (int, float)) and int(val) >= 0:
                    out["reviews"] = int(val)
                    break

        if not out["location"]:
            city = d.get("city") if isinstance(d.get("city"), str) else None
            state = d.get("state") if isinstance(d.get("state"), str) else None
            country = d.get("country") if isinstance(d.get("country"), str) else None
            parts = [p.strip() for p in (city, state, country) if isinstance(p, str) and p.strip()]
            if parts:
                out["location"] = ", ".join(parts)

        if out["lat"] is None or out["lng"] is None:
            lat = d.get("lat", d.get("latitude", d.get("locationLat")))
            lng = d.get("lng", d.get("longitude", d.get("locationLng")))
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                out["lat"] = float(lat)
                out["lng"] = float(lng)

    # Second pass: parse text fragments (titles, subtitles, labels) for missing fields.
    for s in _walk_strings(r):
        if not isinstance(s, str):
            continue
        text = s.strip()
        if not text:
            continue

        if not out["property_type"]:
            norm = _normalize_property_type_from_text(text)
            if norm:
                out["property_type"] = norm

        if out["accommodates"] is None:
            m = _GUEST_RE.search(text)
            if m:
                try:
                    out["accommodates"] = int(m.group(1))
                except Exception:
                    pass

        if out["bedrooms"] is None:
            m = _BEDROOM_RE.search(text)
            if m:
                try:
                    out["bedrooms"] = int(m.group(1))
                except Exception:
                    pass

        if out["beds"] is None:
            m = _BED_RE.search(text)
            if m:
                try:
                    out["beds"] = int(m.group(1))
                except Exception:
                    pass

        if out["baths"] is None:
            m = _BATH_RE.search(text)
            if m:
                try:
                    out["baths"] = float(m.group(1))
                except Exception:
                    pass

        if out["rating"] is None:
            m = re.search(r"([0-5](?:\.\d+)?)\s*(?:out of 5|stars?)", text, re.I)
            if m:
                try:
                    rv = float(m.group(1))
                    if 0 < rv <= 5:
                        out["rating"] = round(rv, 2)
                except Exception:
                    pass

        if out["reviews"] is None:
            m = re.search(r"\b(\d{1,6})\s*(?:reviews?|ratings?)\b", text, re.I)
            if m:
                try:
                    out["reviews"] = int(m.group(1))
                except Exception:
                    pass

    return out


def _extract_availability_context_from_search_result(r: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "is_available": True,
        "availability_reason": None,
        "min_nights": None,
    }

    strong_unavailable_patterns = (
        r"\bsold out\b",
        r"\bno longer available\b",
        r"\bnot available\b",
        r"\bbooked\b",
    )

    # Typed fields first.
    for d in _walk_dicts(r):
        for key in ("isAvailable", "available"):
            v = d.get(key)
            if isinstance(v, bool):
                out["is_available"] = bool(v)
                if not out["is_available"] and not out["availability_reason"]:
                    out["availability_reason"] = "unavailable"
        for key in ("isSoldOut", "soldOut", "isBooked"):
            v = d.get(key)
            if isinstance(v, bool) and v:
                out["is_available"] = False
                out["availability_reason"] = "sold_out"

    # Text fallback for availability/min-stay.
    for s in _walk_strings(r):
        if not isinstance(s, str):
            continue
        txt = s.strip()
        if not txt:
            continue
        txt_l = txt.lower()

        if out["min_nights"] is None:
            m = _MIN_NIGHTS_RE.search(txt)
            if m:
                try:
                    n = int(m.group(1))
                    if n > 0:
                        out["min_nights"] = n
                except Exception:
                    pass

        is_strong_unavailable = any(
            re.search(pattern, txt_l) for pattern in strong_unavailable_patterns
        )
        if is_strong_unavailable:
            out["is_available"] = False
            if "sold out" in txt_l:
                out["availability_reason"] = "sold_out"
            elif not out["availability_reason"]:
                out["availability_reason"] = "unavailable"

        if "minimum stay" in txt_l or ("at least" in txt_l and "night" in txt_l):
            if not out["availability_reason"]:
                out["availability_reason"] = "minimum_nights_not_met"

    return out


def parse_search_listing_context(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Build fallback metadata from StaysSearch response:
    listing_id -> {title, nightly_price, total_price}
    """
    context: Dict[str, Dict[str, Optional[float]]] = {}

    # Exact extraction from staysSearch.results.searchResults[*]
    search_results = _get_nested(data, ["data", "presentation", "staysSearch", "results", "searchResults"])
    if not isinstance(search_results, list):
        return context

    for r in search_results:
        if not isinstance(r, dict):
            continue

        demand = r.get("demandStayListing")
        if not isinstance(demand, dict):
            continue
        listing_id = _decode_graphql_id(demand.get("id"))
        if not listing_id:
            continue

        row = context.setdefault(
            listing_id,
            {
                "title": None,
                "nightly_price": None,
                "total_price": None,
                "currency": None,
                "price_nights": 1,
                "location": None,
                "lat": None,
                "lng": None,
                "accommodates": None,
                "bedrooms": None,
                "beds": None,
                "baths": None,
                "property_type": None,
                "rating": None,
                "reviews": None,
                "amenities": [],
                "is_available": True,
                "availability_reason": None,
                "min_nights": None,
            },
        )

        # Title source from search payload:
        # prefer subtitle/nameLocalized over generic title ("Guest suite in ...")
        subtitle = r.get("subtitle")
        name_localized = r.get("nameLocalized", {})
        localized = name_localized.get("localizedStringWithTranslationPreference") if isinstance(name_localized, dict) else None
        if isinstance(localized, str) and localized.strip():
            row["title"] = localized.strip()
        elif isinstance(subtitle, str) and subtitle.strip():
            row["title"] = subtitle.strip()
        elif isinstance(r.get("title"), str) and r.get("title", "").strip():
            row["title"] = r["title"].strip()

        # Availability/min-stay context for filtering and diagnostics.
        avail = _extract_availability_context_from_search_result(r)
        if isinstance(avail.get("is_available"), bool):
            row["is_available"] = bool(avail["is_available"])
        if avail.get("availability_reason"):
            row["availability_reason"] = avail["availability_reason"]
        if isinstance(avail.get("min_nights"), int) and avail["min_nights"] > 0:
            row["min_nights"] = int(avail["min_nights"])

        # Price source from search payload (strict path only):
        # available=true + structuredDisplayPrice.primaryLine.price
        sdp = r.get("structuredDisplayPrice", {})
        if isinstance(sdp, dict):
            primary = sdp.get("primaryLine", {})
            if isinstance(primary, dict):
                price_text = primary.get("price")
                qualifier = str(primary.get("qualifier") or "").lower()

                # Exact payload rule:
                # if available=true and primaryLine.price is '$<num> <ccy>',
                # parse it as the primary source.
                if row.get("is_available") and isinstance(price_text, str):
                    strict_val, strict_ccy = _parse_dollar_amount_currency(price_text)
                    if strict_val is not None:
                        if strict_ccy and not row.get("currency"):
                            row["currency"] = strict_ccy
                        if "night" in qualifier:
                            row["nightly_price"] = strict_val
                            row["price_nights"] = 1
                        else:
                            row["total_price"] = strict_val
                            row["price_nights"] = 1

        # Structural/context fields used for similarity scoring.
        derived = _extract_structural_context_from_search_result(r)
        for key in ("location", "lat", "lng", "accommodates", "bedrooms", "beds", "baths", "property_type", "rating", "reviews"):
            if row.get(key) in (None, "", 0):
                v = derived.get(key)
                if v not in (None, "", 0):
                    row[key] = v
        if not row.get("amenities") and isinstance(derived.get("amenities"), list):
            row["amenities"] = derived["amenities"]

    return context


def parse_pdp_response(data: Dict[str, Any], listing_id: str, base_url: str) -> Dict[str, Any]:
    """Extract listing title, price fields, and amenities from StaysPdpSections response."""
    result = {
        "listing_id": str(listing_id),
        "title": None,
        "location": None,
        "city": None,
        "state": None,
        "postal_code": None,
        "country": None,
        "country_code": None,
        "accommodates": None,
        "bedrooms": None,
        "beds": None,
        "baths": None,
        "property_type": None,
        "nightly_price": None,
        "total_price": None,
        "currency": None,
        "cleaning_fee": None,
        "service_fee": None,
        "amenities": [],
        "listing_url": f"{base_url}/rooms/{listing_id}",
    }

    generic_titles = {
        "things to know",
        "change dates",
        "choose a cancellation policy",
        "availability",
        "reviews",
        "amenities",
        "where you'll sleep",
    }

    # 1) Title extraction: data.presentation.stayProductDetailPage.sections.sections[*].section.listingTitle
    sections_root = _get_nested(data, ["data", "presentation", "stayProductDetailPage", "sections", "sections"])
    if not isinstance(sections_root, list):
        sections_root = _get_nested(data, ["data", "presentation", "stayproductdetailpage", "sections", "sections"])

    # 0) Primary location extraction from LOCATION_DEFAULT:
    # "Where you'll be" -> section.subtitle (e.g., "Mississauga, Ontario, Canada")
    if isinstance(sections_root, list):
        for entry in sections_root:
            if not isinstance(entry, dict):
                continue
            if entry.get("sectionId") not in ("LOCATION_DEFAULT", "LOCATION_PDP"):
                continue
            sec = entry.get("section")
            if not isinstance(sec, dict):
                continue
            subtitle = sec.get("subtitle")
            if not (isinstance(subtitle, str) and subtitle.strip()):
                continue

            loc = subtitle.strip()
            result["location"] = loc

            parts = [p.strip() for p in loc.split(",") if p and p.strip()]
            if len(parts) >= 1 and not result["city"]:
                result["city"] = parts[0]
            if len(parts) >= 2 and not result["state"]:
                result["state"] = parts[1]
            if len(parts) >= 3 and not result["country"]:
                result["country"] = parts[-1]
            break

    if isinstance(sections_root, list):
        for entry in sections_root:
            if not isinstance(entry, dict):
                continue
            sec = entry.get("section")
            if not isinstance(sec, dict):
                continue
            title_from_path = sec.get("listingTitle")
            if isinstance(title_from_path, str) and title_from_path.strip():
                result["title"] = title_from_path.strip()
                break

    # 1b) Fallback title extraction with filtering.
    for d in _walk_dicts(data):
        if result["title"]:
            break
        for key in ("listingName", "name", "title", "heading", "seoTitle"):
            value = d.get(key)
            if not isinstance(value, str):
                continue
            cleaned = value.strip()
            if len(cleaned) < 8:
                continue
            if cleaned.lower() in generic_titles:
                continue
            if cleaned.startswith("http") or "$" in cleaned:
                continue
            if re.search(r"[A-Za-z]", cleaned):
                result["title"] = cleaned
                break
        if result["title"]:
            break

    # 2) Structural extraction (capacity/layout/property type).
    for d in _walk_dicts(data):
        if result["accommodates"] is None:
            for key in ("personCapacity", "maxGuestCapacity", "maxGuests", "guestCapacity"):
                val = d.get(key)
                if isinstance(val, (int, float)) and int(val) > 0:
                    result["accommodates"] = int(val)
                    break

        if result["bedrooms"] is None:
            for key in ("bedroomCount", "bedrooms", "numBedrooms"):
                val = d.get(key)
                if isinstance(val, (int, float)) and int(val) >= 0:
                    result["bedrooms"] = int(val)
                    break

        if result["beds"] is None:
            for key in ("bedCount", "beds", "numBeds"):
                val = d.get(key)
                if isinstance(val, (int, float)) and int(val) >= 0:
                    result["beds"] = int(val)
                    break

        if result["baths"] is None:
            for key in ("bathroomCount", "bathrooms", "numBathrooms"):
                val = d.get(key)
                if isinstance(val, (int, float)) and float(val) >= 0:
                    result["baths"] = float(val)
                    break

        if not result["property_type"]:
            for key in ("roomTypeCategory", "spaceType", "propertyType", "propertyTypeLabel", "typeName"):
                val = d.get(key)
                if isinstance(val, str) and val.strip():
                    result["property_type"] = val.strip()
                    break

        if not result["location"]:
            city = d.get("city") if isinstance(d.get("city"), str) else None
            state = d.get("state") if isinstance(d.get("state"), str) else None
            country = d.get("country") if isinstance(d.get("country"), str) else None
            parts = [p.strip() for p in (city, state, country) if isinstance(p, str) and p.strip()]
            if parts:
                result["location"] = ", ".join(parts)

        if not result["city"] and isinstance(d.get("city"), str) and d.get("city").strip():
            result["city"] = d.get("city").strip()
        if not result["state"] and isinstance(d.get("state"), str) and d.get("state").strip():
            result["state"] = d.get("state").strip()
        if not result["postal_code"] and isinstance(d.get("postalCode"), str) and d.get("postalCode").strip():
            result["postal_code"] = d.get("postalCode").strip()
        if not result["country"] and isinstance(d.get("country"), str) and d.get("country").strip():
            result["country"] = d.get("country").strip()
        if not result["country_code"] and isinstance(d.get("countryCode"), str) and d.get("countryCode").strip():
            result["country_code"] = d.get("countryCode").strip().upper()

        # JSON-LD style address fields occasionally appear in PDP payload trees.
        if not result["city"] and isinstance(d.get("addressLocality"), str) and d.get("addressLocality").strip():
            result["city"] = d.get("addressLocality").strip()
        if not result["state"] and isinstance(d.get("addressRegion"), str) and d.get("addressRegion").strip():
            result["state"] = d.get("addressRegion").strip()
        if not result["country"] and isinstance(d.get("addressCountry"), str) and d.get("addressCountry").strip():
            result["country"] = d.get("addressCountry").strip()
        if not result["postal_code"] and isinstance(d.get("postalCode"), str) and d.get("postalCode").strip():
            result["postal_code"] = d.get("postalCode").strip()

    # 2b) Metadata fallback: sharingConfig.location is often present even when
    # section-level city/state fields are absent.
    if not result["location"]:
        for path in (
            ["data", "presentation", "stayProductDetailPage", "sections", "metadata", "sharingConfig", "location"],
            ["data", "presentation", "stayproductdetailpage", "sections", "metadata", "sharingConfig", "location"],
        ):
            val = _get_nested(data, path)
            if isinstance(val, str) and val.strip():
                result["location"] = val.strip()
                break
        # If we only got a city-like token, populate city too.
        if result["location"] and not result["city"] and "," not in result["location"]:
            result["city"] = result["location"]

    # 3) Strict booking section parser only.
    # Accept both BOOK_IT_FLOATING_FOOTER and BOOK_IT_SIDEBAR.
    if isinstance(sections_root, list):
        section_priority = ("BOOK_IT_FLOATING_FOOTER", "BOOK_IT_SIDEBAR")
        section_by_id: Dict[str, Dict[str, Any]] = {}
        for entry in sections_root:
            if not isinstance(entry, dict):
                continue
            sid = entry.get("sectionId")
            if isinstance(sid, str):
                section_by_id[sid] = entry

        for sid in section_priority:
            entry = section_by_id.get(sid)
            if not isinstance(entry, dict):
                continue
            sec = entry.get("section")
            if not isinstance(sec, dict):
                continue
            if sec.get("available") is not True:
                continue
            sdp = sec.get("structuredDisplayPrice")
            if not isinstance(sdp, dict):
                continue
            primary = sdp.get("primaryLine")
            if not isinstance(primary, dict):
                continue
            price_text = primary.get("price")
            if not isinstance(price_text, str):
                continue
            amount, ccy = _parse_dollar_amount_currency(price_text)
            if amount is None:
                continue
            qualifier = str(primary.get("qualifier") or "").lower()
            if "night" in qualifier:
                result["nightly_price"] = amount
                if result["total_price"] is None:
                    result["total_price"] = amount
            else:
                result["total_price"] = amount
                if result["nightly_price"] is None:
                    result["nightly_price"] = amount
            if ccy:
                result["currency"] = ccy
            break

    # 6) Amenities from common shapes (exclude "not included"/unavailable amenities).
    amenity_names = set()
    blocked_amenity_names = set()
    negative_group_markers = (
        "not included",
        "not available",
        "unavailable",
        "not provided",
    )

    def _is_amenity_available(item: Dict[str, Any]) -> bool:
        for key in ("available", "isAvailable", "present", "isPresent", "enabled", "isEnabled"):
            v = item.get(key)
            if isinstance(v, bool):
                return bool(v)
        if item.get("unavailable") is True or item.get("isUnavailable") is True:
            return False
        return True
    amenities_candidates = (
        _find_keys(data, "amenities")
        + _find_keys(data, "previewAmenitiesGroups")
        + _find_keys(data, "amenityGroups")
    )

    for candidate in amenities_candidates:
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict):
                    name = item.get("title") or item.get("name") or item.get("label")
                    if isinstance(name, str) and name.strip():
                        clean_name = name.strip()
                        if _is_amenity_available(item):
                            amenity_names.add(clean_name)
                        else:
                            blocked_amenity_names.add(clean_name)
                elif isinstance(item, str) and item.strip():
                    amenity_names.add(item.strip())
        elif isinstance(candidate, dict):
            group_title = str(candidate.get("title") or candidate.get("name") or "").strip().lower()
            group_is_negative = any(marker in group_title for marker in negative_group_markers)
            for k in ("title", "name", "label"):
                v = candidate.get(k)
                if isinstance(v, str) and v.strip():
                    clean_name = v.strip()
                    if group_is_negative:
                        blocked_amenity_names.add(clean_name)
                    else:
                        amenity_names.add(clean_name)

    # Parse amenityGroups with group-level include/exclude semantics.
    for group in _find_keys(data, "amenityGroups"):
        if not isinstance(group, list):
            continue
        for g in group:
            if not isinstance(g, dict):
                continue
            g_title = str(g.get("title") or g.get("name") or "").strip().lower()
            g_negative = any(marker in g_title for marker in negative_group_markers)
            for key in ("amenities", "items"):
                items = g.get(key)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        nm = item.get("title") or item.get("name") or item.get("label")
                        if isinstance(nm, str) and nm.strip():
                            clean_name = nm.strip()
                            if g_negative or not _is_amenity_available(item):
                                blocked_amenity_names.add(clean_name)
                            else:
                                amenity_names.add(clean_name)
                    elif isinstance(item, str) and item.strip():
                        if g_negative:
                            blocked_amenity_names.add(item.strip())
                        else:
                            amenity_names.add(item.strip())

    result["amenities"] = sorted(a for a in amenity_names if a not in blocked_amenity_names)

    if not result["title"]:
        result["title"] = f"Listing {listing_id}"

    return result
