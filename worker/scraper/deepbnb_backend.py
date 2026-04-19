import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

import requests

from worker.scraper.parsers_deepbnb import (
    parse_deepbnb_pdp_to_stayspdp_payload,
    parse_deepbnb_search_to_stayssearch_payload,
)
from worker.scraper.scraper_errors import ScraperForbiddenError

logger = logging.getLogger(__name__)


class DeepBnbBackend:
    """
    Standalone backend adapter inspired by airbnb-scraper/deepbnb.

    This adapter is intentionally separated from AirbnbClient replay logic.
    It builds GraphQL requests directly and then converts responses into the
    existing parser-friendly payload shape used by this worker.
    """

    def __init__(self, config: Dict[str, Any], base_url: str, session: Optional[requests.Session] = None):
        self.config = config
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.locale = str(config.get("LOCALE", os.getenv("AIRBNB_LOCALE", "en-CA")) or "en-CA")
        self.currency = str(config.get("CURRENCY", os.getenv("AIRBNB_CURRENCY", "CAD")) or "CAD")
        self.api_key = str(
            config.get("AIRBNB_API_KEY")
            or os.getenv("AIRBNB_API_KEY")
            or "d306zoyjsyarp7ifhu67rjxn52tv0t20"
        )
        # Keep hashes configurable because Airbnb rotates them frequently.
        self.stays_search_hash = str(
            config.get("AIRBNB_STAYSSEARCH_HASH")
            or os.getenv("AIRBNB_STAYSSEARCH_HASH")
            or "753d97c7b19a1a402d2fa63882ff4d6802004d11f2499647deef923a19a1641a"
        )
        self.pdp_platform_hash = str(
            config.get("AIRBNB_PDPLATFORM_HASH")
            or os.getenv("AIRBNB_PDPLATFORM_HASH")
            or "625a4ba56ba72f8e8585d60078eb95ea0030428cac8772fde09de073da1bcdd0"
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "x-airbnb-api-key": self.api_key,
            "x-airbnb-graphql-platform": "web",
            "x-airbnb-graphql-platform-client": "minimalist-niobe",
            "x-csrf-without-token": "1",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
        }

    def _stays_search_url(self) -> str:
        return (
            f"{self.base_url}/api/v3/StaysSearch/{self.stays_search_hash}"
            f"?operationName=StaysSearch&locale={self.locale}&currency={self.currency}"
        )

    def _pdp_platform_url(self) -> str:
        return (
            f"{self.base_url}/api/v3/PdpPlatformSections/{self.pdp_platform_hash}"
            f"?operationName=PdpPlatformSections&locale={self.locale}&currency={self.currency}"
        )

    @staticmethod
    def _looks_blocked(status: int, response_json: Dict[str, Any]) -> bool:
        if status in (401, 403):
            return True
        if not isinstance(response_json, dict):
            return False
        errors = response_json.get("errors")
        if not isinstance(errors, list):
            return False
        for err in errors:
            if not isinstance(err, dict):
                continue
            text = " ".join(
                str(x or "")
                for x in (
                    err.get("message"),
                    err.get("errorType"),
                    err.get("code"),
                    (err.get("extensions") or {}).get("code"),
                    (err.get("extensions") or {}).get("errorType"),
                )
            ).lower()
            if any(k in text for k in ("forbidden", "unauth", "challenge", "captcha", "security", "login", "block")):
                return True
        return False

    @staticmethod
    def _raw_params_from_overrides(overrides: Dict[str, Any]) -> list[Dict[str, Any]]:
        mapping = {
            "checkin": "checkin",
            "checkout": "checkout",
            "adults": "adults",
            "guests": "guests",
            "query": "query",
            "placeId": "placeId",
            "itemsOffset": "itemsOffset",
            "itemsPerGrid": "itemsPerGrid",
            "searchByMap": "searchByMap",
            "neLat": "neLat",
            "neLng": "neLng",
            "swLat": "swLat",
            "swLng": "swLng",
            "centerLat": "lat",
            "centerLng": "lng",
            "searchMode": "searchMode",
            "searchType": "searchType",
            "guestFavorite": "guestFavorite",
            "minBedrooms": "minBedrooms",
            "minBeds": "minBeds",
            "minBathrooms": "minBathrooms",
        }
        out: list[Dict[str, Any]] = []
        for key, raw_name in mapping.items():
            val = overrides.get(key)
            if val is None:
                continue
            if isinstance(val, bool):
                val = "true" if val else "false"
            out.append({"filterName": raw_name, "filterValues": [str(val)]})
        return out

    def search_listings_with_overrides(self, overrides: Dict[str, Any]) -> Optional[Tuple[int, Dict[str, Any]]]:
        checkin = str(overrides.get("checkin") or self.config.get("CHECKIN", "") or "")
        checkout = str(overrides.get("checkout") or self.config.get("CHECKOUT", "") or "")
        query = str(overrides.get("query") or self.config.get("QUERY", "") or "").strip()
        adults = int(overrides.get("adults") or self.config.get("ADULTS", 2) or 2)
        raw_params = self._raw_params_from_overrides(overrides)
        # Ensure required baseline params always exist.
        baseline = [
            {"filterName": "adults", "filterValues": [str(adults)]},
            {"filterName": "query", "filterValues": [query]},
            {"filterName": "checkin", "filterValues": [checkin]},
            {"filterName": "checkout", "filterValues": [checkout]},
            {"filterName": "screenSize", "filterValues": ["large"]},
            {"filterName": "tabId", "filterValues": ["home_tab"]},
            {"filterName": "version", "filterValues": ["1.8.8"]},
            {"filterName": "searchMode", "filterValues": [str(overrides.get("searchMode") or "regular_search")]},
        ]
        seen = {p["filterName"] for p in raw_params}
        for p in baseline:
            if p["filterName"] not in seen and p["filterValues"][0]:
                raw_params.append(p)

        payload = {
            "operationName": "StaysSearch",
            "variables": {
                "staysSearchRequest": {
                    "metadataOnly": False,
                    "requestedPageType": "STAYS_SEARCH",
                    "searchType": str(overrides.get("searchType") or "user_map_move"),
                    "rawParams": raw_params,
                },
                "staysMapSearchRequestV2": {
                    "metadataOnly": False,
                    "requestedPageType": "STAYS_SEARCH",
                    "searchType": str(overrides.get("searchType") or "user_map_move"),
                    "rawParams": raw_params,
                },
                "isLeanTreatment": False,
                "aiSearchEnabled": False,
            },
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": self.stays_search_hash}},
        }
        url = self._stays_search_url()
        logger.info("deepbnb search url=%s", url)
        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=25)
            status = int(resp.status_code)
            if status in (401, 403):
                raise ScraperForbiddenError(f"DeepBnb StaysSearch blocked with status={status}")
            raw_json = resp.json() if resp.content else {}
            if self._looks_blocked(status, raw_json if isinstance(raw_json, dict) else {}):
                raise ScraperForbiddenError(f"DeepBnb StaysSearch blocked with status={status}")
        except Exception as exc:
            if isinstance(exc, ScraperForbiddenError):
                raise
            logger.warning("deepbnb StaysSearch failed: %s", exc)
            return None

        converted = parse_deepbnb_search_to_stayssearch_payload(
            raw_json if isinstance(raw_json, dict) else {},
            checkin=checkin,
            checkout=checkout,
            currency=self.currency,
        )
        return status, converted

    def get_listing_details(
        self,
        listing_id: str,
        *,
        checkin: str,
        checkout: str,
        adults: int,
    ) -> Optional[Dict[str, Any]]:
        payload = {
            "operationName": "PdpPlatformSections",
            "variables": {
                "request": {
                    "id": str(listing_id),
                    "layouts": ["SIDEBAR", "SINGLE_COLUMN"],
                    "adults": str(adults),
                    "checkIn": checkin,
                    "checkOut": checkout,
                    "sectionIds": None,
                }
            },
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": self.pdp_platform_hash}},
        }
        url = self._pdp_platform_url()
        logger.info("deepbnb pdp url=%s", url)
        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=25)
            status = int(resp.status_code)
            if status in (401, 403):
                raise ScraperForbiddenError(f"DeepBnb PdpPlatformSections blocked with status={status}")
            raw_json = resp.json() if resp.content else {}
            if self._looks_blocked(status, raw_json if isinstance(raw_json, dict) else {}):
                raise ScraperForbiddenError(f"DeepBnb PdpPlatformSections blocked with status={status}")
        except Exception as exc:
            if isinstance(exc, ScraperForbiddenError):
                raise
            logger.warning("deepbnb PdpPlatformSections failed: %s", exc)
            return None

        converted = parse_deepbnb_pdp_to_stayspdp_payload(
            raw_json if isinstance(raw_json, dict) else {},
            listing_id=str(listing_id),
            checkin=checkin,
            checkout=checkout,
            currency=self.currency,
        )
        return converted
