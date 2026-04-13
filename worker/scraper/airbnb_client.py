import base64
import copy
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urlencode

import requests
from playwright.sync_api import sync_playwright
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when Airbnb returns 401/403 requiring fresh cookies."""


class AirbnbClient:
    def __init__(self, config: dict):
        self.config = config
        self.base_url = self.config.get("AIRBNB_BASE_URL", "https://www.airbnb.ca").rstrip("/")
        self.session = requests.Session()
        self.captured_search_req = None
        self.captured_pdp_req = None
        self.debug = bool(self.config.get("DEBUG", False))
        self.cache_path = self.config.get("SESSION_CACHE_PATH", ".airbnb_session_cache.json")
        self.session_max_age_seconds = int(self.config.get("SESSION_MAX_AGE_SECONDS", 6 * 60 * 60))

        if self._load_cached_state():
            logger.info("Loaded cached authentication/template state.")
        else:
            self.refresh_session(force_capture=True)

    def _cookies_to_records(self):
        records = []
        for c in self.session.cookies:
            records.append(
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                    "expires": c.expires,
                    "secure": c.secure,
                }
            )
        return records

    def _restore_cookies(self, cookies):
        self.session.cookies.clear()
        for c in cookies:
            if not isinstance(c, dict):
                continue
            self.session.cookies.set(
                c.get("name", ""),
                c.get("value", ""),
                domain=c.get("domain"),
                path=c.get("path", "/"),
                secure=bool(c.get("secure", False)),
                expires=c.get("expires"),
            )

    def _is_cache_valid(self, saved_at: float, cookies) -> bool:
        if not saved_at or (time.time() - saved_at) > self.session_max_age_seconds:
            return False

        now = time.time()
        has_any_cookie = False
        for c in cookies or []:
            if not isinstance(c, dict):
                continue
            has_any_cookie = True
            exp = c.get("expires")
            if exp is None:
                # Session cookie; consider valid while cache max age is valid.
                return True
            try:
                if float(exp) > now:
                    return True
            except (TypeError, ValueError):
                continue
        return has_any_cookie and False

    def _save_cached_state(self):
        payload = {
            "saved_at": time.time(),
            "cookies": self._cookies_to_records(),
            "captured_search_req": self.captured_search_req,
        }
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as exc:
            if self.debug:
                logger.debug("Failed to write cache file %s: %s", self.cache_path, exc)

    @staticmethod
    def _log_scraped_result(kind: str, payload: Dict[str, Any]) -> None:
        """Always log raw scraped payloads for diagnostics."""
        try:
            logger.info("[SCRAPED_RESULT][%s] %s", kind, json.dumps(payload, ensure_ascii=False))
        except Exception:
            # Logging must never break scraping flow.
            logger.info("[SCRAPED_RESULT][%s] <unserializable>", kind)

    def _load_cached_state(self) -> bool:
        if not os.path.exists(self.cache_path):
            return False
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return False

        saved_at = payload.get("saved_at")
        cookies = payload.get("cookies", [])
        if not self._is_cache_valid(saved_at, cookies):
            return False

        self._restore_cookies(cookies)
        self.captured_search_req = payload.get("captured_search_req")
        # PDP template is intentionally not restored from cache.
        # It is captured fresh on each listing details fetch.
        self.captured_pdp_req = None

        # Search template is required for this run mode.
        if not self.captured_search_req:
            return False
        return True

    def refresh_session(self, force_capture: bool = False):
        """
        Uses Playwright to capture fresh StaysSearch and StaysPdpSections request templates,
        along with valid session cookies.
        """
        if not force_capture and self._load_cached_state():
            logger.info("Using cached authentication/template state.")
            return

        logger.info("Refreshing authentication and capturing API request templates via Playwright...")
        self.captured_search_req = None
        self.captured_pdp_req = None

        capture_pdp_on_start = bool(self.config.get("CAPTURE_PDP_ON_START", False))

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            def on_request(request):
                if request.method != "POST":
                    return
                if "/api/v3/StaysSearch/" in request.url and not self.captured_search_req:
                    try:
                        self.captured_search_req = {
                            "url": request.url,
                            "headers": request.headers,
                            "post_data": request.post_data_json,
                        }
                        logger.info("Captured live StaysSearch template.")
                    except Exception:
                        if self.debug:
                            logger.debug("Failed to parse StaysSearch request payload.")
                if "/api/v3/StaysPdpSections/" in request.url and not self.captured_pdp_req:
                    try:
                        self.captured_pdp_req = {
                            "url": request.url,
                            "headers": request.headers,
                            "post_data": request.post_data_json,
                        }
                        logger.info("Captured live StaysPdpSections template.")
                    except Exception:
                        if self.debug:
                            logger.debug("Failed to parse StaysPdpSections request payload.")

            page.on("request", on_request)

            try:
                query = self.config.get("QUERY", "Mississauga, Ontario")
                search_path = f"/s/{quote(query).replace('%2C', '--')}/homes"
                params = {
                    "date_picker_type": self.config.get("DATE_PICKER_TYPE", "calendar"),
                    "center_lat": self.config.get("CENTER_LAT", ""),
                    "center_lng": self.config.get("CENTER_LNG", ""),
                    "refinement_paths[]": "/homes",
                    "place_id": self.config.get("PLACE_ID", ""),
                    "checkin": self.config.get("CHECKIN", ""),
                    "checkout": self.config.get("CHECKOUT", ""),
                    "adults": self.config.get("ADULTS", 1),
                    "search_type": "AUTOSUGGEST",
                }
                search_url = f"{self.base_url}{search_path}?{urlencode(params)}"

                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)

                # Small UI interaction to force an XHR search if SSR populated first load.
                page.mouse.wheel(0, 500)
                try:
                    page.locator('button:has-text("Filters")').first.click(timeout=3000)
                    page.wait_for_timeout(700)
                    page.keyboard.press("Escape")
                except Exception:
                    if self.debug:
                        logger.debug("Could not click Filters; continuing.")

                for _ in range(20):
                    if self.captured_search_req:
                        break
                    page.wait_for_timeout(500)

                # Optionally capture PDP template at startup; default is off for speed.
                if capture_pdp_on_start and not self.captured_pdp_req:
                    try:
                        with page.expect_popup(timeout=12000) as popup_info:
                            page.locator('a[href*="/rooms/"]').first.click()
                        popup = popup_info.value
                        popup.wait_for_load_state("domcontentloaded")
                        popup.mouse.wheel(0, 1600)
                        for _ in range(24):
                            if self.captured_pdp_req:
                                break
                            popup.wait_for_timeout(500)
                        popup.close()
                    except Exception as exc:
                        if self.debug:
                            logger.debug("Failed to capture PDP request via popup: %s", exc)

                self.session.cookies.clear()
                for cookie in context.cookies():
                    self.session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie["domain"],
                        path=cookie["path"],
                    )

                logger.info("Successfully acquired fresh session cookies.")
                if not self.captured_search_req:
                    raise RuntimeError("Timed out waiting for required API request: StaysSearch")
                if self.captured_pdp_req:
                    logger.info("Successfully captured request templates.")
                else:
                    logger.info("StaysPdpSections template not captured yet; will capture on first detail request.")

                self._save_cached_state()
            finally:
                browser.close()

    def _capture_pdp_template_for_listing(
        self,
        listing_id: str,
        checkin: Optional[str] = None,
        checkout: Optional[str] = None,
        adults: Optional[int] = None,
        force_refresh: bool = True,
    ):
        """Capture a live StaysPdpSections template by opening a specific listing page."""
        if self.captured_pdp_req is not None and not force_refresh:
            return
        logger.info("Capturing StaysPdpSections template on-demand...")
        # Refresh on demand when explicitly requested.
        if force_refresh:
            self.captured_pdp_req = None
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            def on_request(request):
                if request.method == "POST" and "/api/v3/StaysPdpSections/" in request.url and not self.captured_pdp_req:
                    try:
                        self.captured_pdp_req = {
                            "url": request.url,
                            "headers": request.headers,
                            "post_data": request.post_data_json,
                        }
                        logger.info("Captured live StaysPdpSections template.")
                    except Exception:
                        if self.debug:
                            logger.debug("Failed to parse on-demand StaysPdpSections payload.")

            page.on("request", on_request)
            try:
                checkin = checkin or self.config.get("CHECKIN", "")
                checkout = checkout or self.config.get("CHECKOUT", "")
                adults = int(adults if adults is not None else self.config.get("ADULTS", 1))
                listing_url = (
                    f"{self.base_url}/rooms/{listing_id}"
                    f"?check_in={checkin}&check_out={checkout}&guests={adults}&adults={adults}"
                )
                page.goto(listing_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)
                page.mouse.wheel(0, 1800)
                for _ in range(24):
                    if self.captured_pdp_req:
                        break
                    page.wait_for_timeout(500)

                # refresh cookies to keep session in sync
                self.session.cookies.clear()
                for cookie in context.cookies():
                    self.session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie["domain"],
                        path=cookie["path"],
                    )
            finally:
                browser.close()

        if not self.captured_pdp_req:
            raise RuntimeError("Timed out waiting for required API request: StaysPdpSections")

        self._save_cached_state()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.RequestException, AuthError)),
    )
    def _replay_request(self, captured_template: dict, payload_override: dict) -> Tuple[int, Dict[str, Any]]:
        """Send a POST request mirroring captured browser request shape."""
        url = captured_template["url"]

        url_hash = url.split("?")[0].split("/")[-1]
        body_hash = payload_override.get("extensions", {}).get("persistedQuery", {}).get("sha256Hash")
        if body_hash and url_hash != body_hash:
            url = url.replace(url_hash, body_hash)

        required_keys = {
            "content-type",
            "x-airbnb-api-key",
            "x-airbnb-graphql-platform",
            "x-airbnb-graphql-platform-client",
            "x-client-version",
            "accept",
            "accept-language",
        }
        headers = {k: v for k, v in captured_template["headers"].items() if k.lower() in required_keys}

        response = self.session.post(url, json=payload_override, headers=headers, timeout=20)
        if response.status_code in (401, 403):
            logger.warning("Auth error (%s). Refreshing session...", response.status_code)
            self.refresh_session(force_capture=True)
            raise AuthError(f"Authentication failed with status {response.status_code}")

        response.raise_for_status()
        return response.status_code, response.json()

    def search_listings(self) -> Tuple[int, Dict[str, Any]]:
        """Replay captured StaysSearch request and return response JSON."""
        logger.info("Replaying captured StaysSearch request...")
        payload = copy.deepcopy(self.captured_search_req["post_data"])

        status_code, response_data = self._replay_request(self.captured_search_req, payload)
        if response_data.get("errors"):
            logger.warning("GraphQL errors in StaysSearch. Refreshing templates/session and retrying once...")
            self.refresh_session(force_capture=True)
            payload = copy.deepcopy(self.captured_search_req["post_data"])
            status_code, response_data = self._replay_request(self.captured_search_req, payload)

        self._log_scraped_result("StaysSearch", response_data)
        return status_code, response_data

    @staticmethod
    def _set_raw_param(raw_params: Any, filter_name: str, filter_values: list[str]):
        if not isinstance(raw_params, list):
            return
        for p in raw_params:
            if isinstance(p, dict) and p.get("filterName") == filter_name:
                p["filterValues"] = filter_values
                return
        raw_params.append({"filterName": filter_name, "filterValues": filter_values})

    def search_listings_with_overrides(
        self,
        overrides: Dict[str, Any],
        _already_retried: bool = False,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Replay captured StaysSearch with selected rawParams overridden.
        Supported keys: checkin, checkout, adults, centerLat, centerLng, placeId, query.
        """
        logger.info("Replaying captured StaysSearch with overrides...")
        payload = copy.deepcopy(self.captured_search_req["post_data"])

        for req_key in ("staysSearchRequest", "staysMapSearchRequestV2"):
            req = payload.get("variables", {}).get(req_key, {})
            raw_params = req.get("rawParams")
            if not raw_params:
                continue

            mapping = {
                "checkin": "checkin",
                "checkout": "checkout",
                "adults": "adults",
                "centerLat": "centerLat",
                "centerLng": "centerLng",
                "placeId": "placeId",
                "query": "query",
            }
            for key, raw_name in mapping.items():
                if key in overrides and overrides[key] is not None:
                    self._set_raw_param(raw_params, raw_name, [str(overrides[key])])

            if "itemsPerGrid" in overrides and req_key == "staysSearchRequest":
                self._set_raw_param(raw_params, "itemsPerGrid", [str(overrides["itemsPerGrid"])])

        status_code, response_data = self._replay_request(self.captured_search_req, payload)
        if response_data.get("errors"):
            if _already_retried:
                self._log_scraped_result("StaysSearchWithOverrides", response_data)
                return status_code, response_data
            logger.warning("GraphQL errors in StaysSearch with overrides. Refreshing and retrying once...")
            self.refresh_session(force_capture=True)
            payload = copy.deepcopy(self.captured_search_req["post_data"])
            return self.search_listings_with_overrides(overrides, _already_retried=True)

        self._log_scraped_result("StaysSearchWithOverrides", response_data)
        return status_code, response_data

    @staticmethod
    def _to_global_id(prefix: str, listing_id: str) -> str:
        return base64.b64encode(f"{prefix}:{listing_id}".encode("utf-8")).decode("utf-8")

    def _replace_listing_ids(self, payload: Any, listing_id: str, stay_gid: str, demand_gid: str):
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key == "demandStayListingId" and isinstance(value, str):
                    payload[key] = demand_gid
                elif key == "id" and isinstance(value, str) and value.startswith("U3RheUxpc3Rpbmc6"):
                    payload[key] = stay_gid
                else:
                    self._replace_listing_ids(value, listing_id, stay_gid, demand_gid)
        elif isinstance(payload, list):
            for item in payload:
                self._replace_listing_ids(item, listing_id, stay_gid, demand_gid)

    def get_listing_details(
        self,
        listing_id: str,
        checkin: Optional[str] = None,
        checkout: Optional[str] = None,
        adults: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute StaysPdpSections query for listing pricing/amenities."""
        # Reuse the in-memory PDP template within a run; capture only when missing.
        # If replay returns GraphQL errors, recapture once and retry.
        self._capture_pdp_template_for_listing(
            str(listing_id),
            checkin=checkin,
            checkout=checkout,
            adults=adults,
            force_refresh=False,
        )

        payload = copy.deepcopy(self.captured_pdp_req["post_data"])
        stay_gid = self._to_global_id("StayListing", str(listing_id))
        demand_gid = self._to_global_id("DemandStayListing", str(listing_id))
        self._replace_listing_ids(payload, str(listing_id), stay_gid, demand_gid)

        try:
            request_vars = payload["variables"].get("pdpSectionsRequest", {})
            if request_vars:
                request_vars["adults"] = str(adults if adults is not None else self.config.get("ADULTS", 1))
                request_vars["checkIn"] = checkin or self.config.get("CHECKIN", "")
                request_vars["checkOut"] = checkout or self.config.get("CHECKOUT", "")
                # Captured templates often pin only booking-related sections.
                # Dropping sectionIds asks backend for default/full section composition.
                request_vars.pop("sectionIds", None)
        except (KeyError, TypeError):
            pass

        _, response_data = self._replay_request(self.captured_pdp_req, payload)
        if response_data.get("errors"):
            logger.warning("GraphQL errors in StaysPdpSections. Refreshing PDP template and retrying once...")
            self._capture_pdp_template_for_listing(
                str(listing_id),
                checkin=checkin,
                checkout=checkout,
                adults=adults,
                force_refresh=True,
            )
            payload = copy.deepcopy(self.captured_pdp_req["post_data"])
            self._replace_listing_ids(payload, str(listing_id), stay_gid, demand_gid)
            try:
                request_vars = payload["variables"].get("pdpSectionsRequest", {})
                if request_vars:
                    request_vars["adults"] = str(adults if adults is not None else self.config.get("ADULTS", 1))
                    request_vars["checkIn"] = checkin or self.config.get("CHECKIN", "")
                    request_vars["checkOut"] = checkout or self.config.get("CHECKOUT", "")
                    request_vars.pop("sectionIds", None)
            except (KeyError, TypeError):
                pass
            _, response_data = self._replay_request(self.captured_pdp_req, payload)
        self._log_scraped_result("StaysPdpSections", response_data)
        return response_data
