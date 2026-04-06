from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("worker.core.geocode_details")

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "AiraHost/1.0 (pricing-worker; contact@airahost.com)"
_DEFAULT_TIMEOUT_S = 5

COUNTRY_NAME_TO_CODE = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "taiwan": "TW",
    "tw": "TW",
}


def _clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _normalize_country_code(value: Any) -> Optional[str]:
    text = _clean_text(value)
    if not text:
        return None

    upper = text.upper()
    if len(upper) == 2 and upper.isalpha():
        return upper

    return COUNTRY_NAME_TO_CODE.get(text.casefold())


def geocode_address_details(
    address: str,
    timeout: int = _DEFAULT_TIMEOUT_S,
) -> Optional[Dict[str, Any]]:
    """
    Geocode a free-text address and return coordinates plus structured fields.

    Returns None on any failure and never raises.
    """
    if not address or not address.strip():
        return None

    params = urlencode({
        "q": address.strip(),
        "format": "json",
        "limit": "1",
        "addressdetails": "1",
    })
    url = f"{_NOMINATIM_URL}?{params}"
    req = Request(url, headers={"User-Agent": _USER_AGENT})

    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
    except (URLError, OSError, TimeoutError) as exc:
        logger.warning(f"[geocoding] Network error for {address!r}: {exc}")
        return None
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning(f"[geocoding] Parse error for {address!r}: {exc}")
        return None

    if not data:
        logger.debug(f"[geocoding] No results for: {address!r}")
        return None

    try:
        result = data[0]
        lat = float(result["lat"])
        lng = float(result["lon"])
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        logger.warning(f"[geocoding] Unexpected response shape for {address!r}: {exc}")
        return None

    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        logger.warning(f"[geocoding] Out-of-range coords for {address!r}: ({lat}, {lng})")
        return None

    address_parts = result.get("address") or {}
    city = (
        _clean_text(address_parts.get("city"))
        or _clean_text(address_parts.get("town"))
        or _clean_text(address_parts.get("village"))
        or _clean_text(address_parts.get("municipality"))
        or _clean_text(address_parts.get("county"))
    )
    state = (
        _clean_text(address_parts.get("state"))
        or _clean_text(address_parts.get("state_district"))
        or _clean_text(address_parts.get("region"))
    )
    postal_code = _clean_text(address_parts.get("postcode"))
    country = _clean_text(address_parts.get("country"))
    country_code = _normalize_country_code(
        address_parts.get("country_code") or country
    )

    logger.info(f"[geocoding] {address!r} -> ({lat:.5f}, {lng:.5f})")
    return {
        "lat": lat,
        "lng": lng,
        "city": city,
        "state": state,
        "postal_code": postal_code.upper() if postal_code else None,
        "country": country,
        "country_code": country_code,
        "display_name": _clean_text(result.get("display_name")),
    }
