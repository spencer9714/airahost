"""
Target geocoding — Nominatim (OpenStreetMap).

Best-effort only: every failure path returns None and is logged, never
propagated.  A geocoding failure must never block a pricing run.

OSM Nominatim usage policy:
  - Identify the application with a descriptive User-Agent.
  - Honour rate limiting: at most 1 request per second.
  - We only call this once per *new* saved listing (lazy, cached in DB),
    so sustained rate is well below the limit.
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Tuple
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("worker.core.geocoding")

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "AiraHost/1.0 (pricing-worker; contact@airahost.com)"
_DEFAULT_TIMEOUT_S: int = 5


def geocode_address(
    address: str,
    timeout: int = _DEFAULT_TIMEOUT_S,
) -> Optional[Tuple[float, float]]:
    """
    Geocode a free-text address to (lat, lng) using Nominatim.

    Returns:
        (lat, lng) float tuple on success, None on any failure.

    Never raises — all exceptions are caught and logged as warnings.
    """
    if not address or not address.strip():
        return None

    params = urlencode({
        "q": address.strip(),
        "format": "json",
        "limit": "1",
        "addressdetails": "0",
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
        return None

    try:
        lat = float(data[0]["lat"])
        lng = float(data[0]["lon"])
    except (KeyError, ValueError, IndexError) as exc:
        logger.warning(f"[geocoding] Unexpected response shape for {address!r}: {exc}")
        return None

    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        logger.warning(f"[geocoding] Out-of-range coords for {address!r}: ({lat}, {lng})")
        return None

    logger.info(f"[geocoding] {address!r} → ({lat:.5f}, {lng:.5f})")
    return lat, lng
