import base64
import copy
import json
import logging
import os
import random
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urlencode

import requests
from playwright.sync_api import sync_playwright
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from worker.scraper.stayspdp_template import HARDCODED_STAYS_PDP_TEMPLATE

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when Airbnb returns 401/403 requiring fresh cookies."""


class PlaywrightScraper:
    """Legacy Playwright capture/replay strategy restored from pre-deepbnb history."""
    _refresh_lock = threading.Lock()

    def __init__(self, config: dict):
        self.config = config
        self.base_url = self.config.get("AIRBNB_BASE_URL", "https://www.airbnb.ca").rstrip("/")
        self.session = requests.Session()
        self.captured_search_req = None
        self.captured_pdp_req = None
        disable_map_cfg = self.config.get("DISABLE_MAP_SEARCH", None)
        if disable_map_cfg is None:
            self.disable_map_search = bool(
                str(os.getenv("AIRBNB_DISABLE_MAP_SEARCH", "0")).strip().lower() in ("1", "true", "yes", "on")
            )
        else:
            self.disable_map_search = bool(disable_map_cfg)
        enable_ai_cfg = self.config.get("ENABLE_AI_SEARCH", None)
        if enable_ai_cfg is None:
            self.enable_ai_search = bool(
                str(os.getenv("AIRBNB_ENABLE_AI_SEARCH", "0")).strip().lower() in ("1", "true", "yes", "on")
            )
        else:
            self.enable_ai_search = bool(enable_ai_cfg)
        self.cache_path = self.config.get("SESSION_CACHE_PATH", ".airbnb_session_cache.json")
        self.session_max_age_seconds = int(self.config.get("SESSION_MAX_AGE_SECONDS", 6 * 60 * 60))
        self.refresh_cooldown_seconds = int(self.config.get("SESSION_REFRESH_COOLDOWN_SECONDS", 45))
        self._last_refresh_started_at = 0.0
        refresh_each_cfg = self.config.get("REFRESH_SESSION_BEFORE_EACH_SEARCH", None)
        if refresh_each_cfg is None:
            self.refresh_before_each_search = bool(
                str(os.getenv("AIRBNB_REFRESH_SESSION_BEFORE_EACH_SEARCH", "0")).strip().lower() in ("1", "true", "yes", "on")
            )
        else:
            self.refresh_before_each_search = bool(refresh_each_cfg)
        hardcoded_pdp_cfg = self.config.get("USE_HARDCODED_STAYSPDP_TEMPLATE", None)
        if hardcoded_pdp_cfg is None:
            self.use_hardcoded_stayspdp_template = bool(
                str(os.getenv("AIRBNB_USE_HARDCODED_STAYSPDP_TEMPLATE", "1")).strip().lower()
                in ("1", "true", "yes", "on")
            )
        else:
            self.use_hardcoded_stayspdp_template = bool(hardcoded_pdp_cfg)
        # Cache unresolved PDP booking windows to avoid repeated expensive
        # template recaptures when Airbnb consistently returns NOT_COMPLETE.
        self._pdp_unresolved_windows: Dict[str, float] = {}
        if self.use_hardcoded_stayspdp_template:
            self._load_hardcoded_stayspdp_template()

        if self._load_cached_state():
            logger.info("Loaded cached authentication/template state.")
        else:
            self.refresh_session(force_capture=True)

    def _load_hardcoded_stayspdp_template(self) -> bool:
        if not isinstance(HARDCODED_STAYS_PDP_TEMPLATE, dict):
            return False
        required = ("url", "headers", "post_data")
        if not all(key in HARDCODED_STAYS_PDP_TEMPLATE for key in required):
            return False
        self.captured_pdp_req = copy.deepcopy(HARDCODED_STAYS_PDP_TEMPLATE)
        logger.info("Loaded hardcoded StaysPdpSections template.")
        return True

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
        except Exception:
            pass

    def fork(self) -> "PlaywrightScraper":
        """
        Create an in-memory clone of the client for concurrent read-only replay.

        The clone reuses captured templates/cookies but has its own requests.Session,
        so concurrent setup tasks (e.g., fixed-pool anchor searches) don't contend
        on a single session object.
        """
        clone = PlaywrightScraper.__new__(PlaywrightScraper)
        clone.config = copy.deepcopy(self.config)
        clone.base_url = self.base_url
        clone.session = requests.Session()
        clone.captured_search_req = copy.deepcopy(self.captured_search_req)
        clone.captured_pdp_req = copy.deepcopy(self.captured_pdp_req)
        clone.disable_map_search = self.disable_map_search
        clone.enable_ai_search = self.enable_ai_search
        clone.cache_path = self.cache_path
        clone.session_max_age_seconds = self.session_max_age_seconds
        clone.refresh_cooldown_seconds = self.refresh_cooldown_seconds
        clone._last_refresh_started_at = self._last_refresh_started_at
        clone.refresh_before_each_search = self.refresh_before_each_search
        clone._pdp_unresolved_windows = copy.deepcopy(self._pdp_unresolved_windows)
        for c in self.session.cookies:
            clone.session.cookies.set(
                c.name,
                c.value,
                domain=c.domain,
                path=c.path,
                secure=c.secure,
                expires=c.expires,
            )
        return clone

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
        if self.use_hardcoded_stayspdp_template:
            self._load_hardcoded_stayspdp_template()

        # Search template is required for this run mode.
        if not self.captured_search_req:
            return False
        return True

    @staticmethod
    def _normalize_query_text(value: Any) -> str:
        return re.sub(r"\s*,\s*", ", ", str(value or "").strip())

    @staticmethod
    def _response_looks_auth_or_challenge_error(status_code: int, response_data: Dict[str, Any]) -> bool:
        if status_code in (401, 403):
            return True
        errs = response_data.get("errors")
        if not isinstance(errs, list):
            return False
        for err in errs:
            if not isinstance(err, dict):
                continue
            txt = " ".join(
                str(x or "")
                for x in (
                    err.get("message"),
                    err.get("errorType"),
                    err.get("code"),
                    (err.get("extensions") or {}).get("code"),
                    (err.get("extensions") or {}).get("errorType"),
                )
            ).lower()
            if any(k in txt for k in ("unauth", "forbidden", "csrf", "captcha", "challenge", "login", "security")):
                return True
        return False

    @staticmethod
    def _page_looks_challenged(content_html: str, page_url: str) -> bool:
        txt = f"{page_url}\n{content_html}".lower()
        markers = (
            "captcha",
            "verify you are human",
            "are you a human",
            "security check",
            "unusual traffic",
            "/challenge",
            "/checkpoint",
            "/login",
        )
        return any(m in txt for m in markers)

    def refresh_session(self, force_capture: bool = False, bypass_cooldown: bool = False):
        """
        Uses Playwright to capture fresh StaysSearch and StaysPdpSections request templates,
        along with valid session cookies.
        """
        with self._refresh_lock:
            now = time.time()
            if not force_capture and self._load_cached_state():
                logger.info("Using cached authentication/template state.")
                return
            if (
                force_capture
                and not bypass_cooldown
                and self.captured_search_req is not None
                and (now - self._last_refresh_started_at) < self.refresh_cooldown_seconds
            ):
                logger.warning(
                    "Skipping forced refresh (cooldown %ss active). Reusing current search template.",
                    self.refresh_cooldown_seconds,
                )
                return

            self._last_refresh_started_at = now
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
                            pass
                    if "/api/v3/StaysPdpSections/" in request.url and not self.captured_pdp_req:
                        try:
                            self.captured_pdp_req = {
                                "url": request.url,
                                "headers": request.headers,
                                "post_data": request.post_data_json,
                            }
                            logger.info("Captured live StaysPdpSections template.")
                        except Exception:
                            pass

                page.on("request", on_request)

                def _raise_if_challenged(context_msg: str) -> None:
                    try:
                        html = page.content()
                        current_url = page.url
                    except Exception:
                        return
                    if self._page_looks_challenged(html, current_url):
                        raise RuntimeError(f"Airbnb challenge/login page detected during {context_msg}")

                try:
                    # Seed existing requests-session cookies into browser context.
                    existing_cookies = []
                    for c in self.session.cookies:
                        try:
                            existing_cookies.append(
                                {
                                    "name": c.name,
                                    "value": c.value,
                                    "domain": c.domain,
                                    "path": c.path or "/",
                                    "secure": bool(c.secure),
                                }
                            )
                        except Exception:
                            continue
                    if existing_cookies:
                        try:
                            context.add_cookies(existing_cookies)
                        except Exception:
                            pass

                    query = self.config.get("QUERY", "Mississauga, Ontario")
                    normalized_display_query = self._normalize_query_text(query)
                    path_query = normalized_display_query.replace(", ", ",")
                    search_path = f"/s/{quote(path_query).replace('%2C', '--')}/homes"
                    params = {
                        "date_picker_type": self.config.get("DATE_PICKER_TYPE", "calendar"),
                        "center_lat": self.config.get("CENTER_LAT", ""),
                        "center_lng": self.config.get("CENTER_LNG", ""),
                        "refinement_paths[]": "/homes",
                        "place_id": self.config.get("PLACE_ID", ""),
                        "checkin": self.config.get("CHECKIN", ""),
                        "checkout": self.config.get("CHECKOUT", ""),
                        "adults": self.config.get("ADULTS", 1),
                        "query": normalized_display_query,
                        "search_type": "AUTOSUGGEST",
                    }
                    search_url = f"{self.base_url}{search_path}?{urlencode(params)}"

                    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(int(random.uniform(900, 1600)))
                    _raise_if_challenged("initial StaysSearch capture")

                    # Small UI interaction to force an XHR search if SSR populated first load.
                    page.mouse.wheel(0, 500)
                    try:
                        page.locator('button:has-text("Filters")').first.click(timeout=3000)
                        page.wait_for_timeout(int(random.uniform(500, 1100)))
                        page.keyboard.press("Escape")
                    except Exception:
                        pass

                    for _ in range(20):
                        if self.captured_search_req:
                            break
                        page.wait_for_timeout(int(random.uniform(350, 700)))
                        _raise_if_challenged("waiting for StaysSearch")

                    # Fallback navigation: nudge a filter-change search to trigger XHR.
                    if not self.captured_search_req:
                        fallback_params = dict(params)
                        fallback_params["search_type"] = "filter_change"
                        fallback_url = f"{self.base_url}{search_path}?{urlencode(fallback_params)}"
                        page.goto(fallback_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(int(random.uniform(900, 1600)))
                        page.mouse.wheel(0, 700)
                        _raise_if_challenged("fallback StaysSearch capture")
                        for _ in range(20):
                            if self.captured_search_req:
                                break
                            page.wait_for_timeout(int(random.uniform(350, 700)))
                            _raise_if_challenged("fallback wait for StaysSearch")

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
                        except Exception:
                            pass

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
        if self.use_hardcoded_stayspdp_template and not force_refresh and self.captured_pdp_req is not None:
            return
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
                        pass

            page.on("request", on_request)
            try:
                checkin = checkin or self.config.get("CHECKIN", "")
                checkout = checkout or self.config.get("CHECKOUT", "")
                listing_url = (
                    f"{self.base_url}/rooms/{listing_id}"
                    f"?check_in={checkin}&check_out={checkout}"
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

    def _refresh_before_search_if_enabled(self) -> None:
        if not self.refresh_before_each_search:
            return
        logger.info("REFRESH_SESSION_BEFORE_EACH_SEARCH enabled; refreshing session/templates before StaysSearch replay.")
        # Force a fresh capture so request tokens/cookies are rotated each search call.
        self.refresh_session(force_capture=True, bypass_cooldown=True)

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

    @staticmethod
    def _extract_pdp_sections(response_data: Dict[str, Any]) -> list[dict]:
        if not isinstance(response_data, dict):
            return []
        for path in (
            ("data", "presentation", "stayProductDetailPage", "sections", "sections"),
            ("data", "presentation", "stayproductdetailpage", "sections", "sections"),
        ):
            cur: Any = response_data
            ok = True
            for key in path:
                if not isinstance(cur, dict) or key not in cur:
                    ok = False
                    break
                cur = cur[key]
            if ok and isinstance(cur, list):
                return [x for x in cur if isinstance(x, dict)]
        return []

    @classmethod
    def _pdp_booking_has_price(cls, response_data: Dict[str, Any]) -> bool:
        sections = cls._extract_pdp_sections(response_data)
        for entry in sections:
            sid = entry.get("sectionId")
            if sid not in ("BOOK_IT_FLOATING_FOOTER", "BOOK_IT_SIDEBAR", "BOOK_IT_NAV"):
                continue
            sec = entry.get("section")
            if not isinstance(sec, dict):
                continue
            primary = ((sec.get("structuredDisplayPrice") or {}).get("primaryLine") or {})
            if not isinstance(primary, dict):
                continue
            for key in ("price", "discountedPrice", "accessibilityLabel"):
                value = primary.get(key)
                if isinstance(value, str) and value.strip():
                    return True
        return False

    @classmethod
    def _pdp_booking_unresolved(cls, response_data: Dict[str, Any]) -> bool:
        sections = cls._extract_pdp_sections(response_data)
        saw_booking = False
        saw_not_complete = False
        for entry in sections:
            sid = entry.get("sectionId")
            if sid not in ("BOOK_IT_FLOATING_FOOTER", "BOOK_IT_SIDEBAR", "BOOK_IT_NAV"):
                continue
            saw_booking = True
            status = str(entry.get("sectionContentStatus") or "").upper()
            if "NOT_COMPLETE" in status:
                saw_not_complete = True
        return saw_booking and saw_not_complete and not cls._pdp_booking_has_price(response_data)

    def search_listings(self) -> Tuple[int, Dict[str, Any]]:
        """Replay captured StaysSearch request and return response JSON."""
        self._refresh_before_search_if_enabled()
        logger.info("Replaying captured StaysSearch request...")
        payload = copy.deepcopy(self.captured_search_req["post_data"])
        self._apply_disable_map_search(payload)
        for req_key in ("staysSearchRequest", "staysMapSearchRequestV2"):
            req = payload.get("variables", {}).get(req_key, {})
            raw_params = req.get("rawParams")
            if raw_params:
                # Keep guest-favorite filter OFF unless explicitly requested via overrides path.
                if self._raw_param_exists(raw_params, "guestFavorite"):
                    self._set_raw_param(raw_params, "guestFavorite", ["false"])

        status_code, response_data = self._replay_request(self.captured_search_req, payload)
        if response_data.get("errors"):
            if self._response_looks_auth_or_challenge_error(status_code, response_data):
                logger.warning("Auth/challenge-like error in StaysSearch. Refreshing templates/session and retrying once...")
                self.refresh_session(force_capture=True)
                payload = copy.deepcopy(self.captured_search_req["post_data"])
                self._apply_disable_map_search(payload)
                status_code, response_data = self._replay_request(self.captured_search_req, payload)
            else:
                logger.warning("Non-auth GraphQL errors in StaysSearch; returning without forced auth refresh.")
        else:
            try:
                from worker.scraper.parsers import parse_search_total_listings

                total_listings = parse_search_total_listings(response_data)
                if isinstance(total_listings, int):
                    logger.info("StaysSearch total listings reported by Airbnb: %s", total_listings)
            except Exception:
                pass

        return status_code, response_data

    @staticmethod
    def _raw_param_exists(raw_params: Any, filter_name: str) -> bool:
        if not isinstance(raw_params, list):
            return False
        for p in raw_params:
            if isinstance(p, dict) and p.get("filterName") == filter_name:
                return True
        return False

    @staticmethod
    def _set_raw_param(raw_params: Any, filter_name: str, filter_values: list[str]):
        if not isinstance(raw_params, list):
            return
        for p in raw_params:
            if isinstance(p, dict) and p.get("filterName") == filter_name:
                p["filterValues"] = filter_values
                return
        raw_params.append({"filterName": filter_name, "filterValues": filter_values})

    @staticmethod
    def _remove_raw_param(raw_params: Any, filter_name: str):
        if not isinstance(raw_params, list):
            return
        raw_params[:] = [p for p in raw_params if not (isinstance(p, dict) and p.get("filterName") == filter_name)]

    def _apply_disable_map_search(self, payload: Dict[str, Any]) -> None:
        """Disable map-oriented search path while keeping persisted-query shape safe."""
        if not self.disable_map_search:
            return
        variables = payload.get("variables")
        if not isinstance(variables, dict):
            return

        stays_req = variables.get("staysSearchRequest")
        if isinstance(stays_req, dict):
            stays_req["maxMapItems"] = 0

        map_req = variables.get("staysMapSearchRequestV2")
        if isinstance(map_req, dict):
            map_req["metadataOnly"] = True
            map_req["rawParams"] = []

    def search_listings_with_overrides(
        self,
        overrides: Dict[str, Any],
        _already_retried: bool = False,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Replay captured StaysSearch with selected rawParams overridden.
        Supported keys: checkin, checkout, adults, centerLat, centerLng, placeId,
        query, locationSearch, location, itemsPerGrid, itemsOffset, guestFavorite, guests, minBedrooms,
        minBeds, minBathrooms, searchByMap, neLat, neLng, swLat, swLng.
        """
        self._refresh_before_search_if_enabled()
        logger.info("Replaying captured StaysSearch with overrides...")
        payload = copy.deepcopy(self.captured_search_req["post_data"])
        self._apply_disable_map_search(payload)

        for req_key in ("staysSearchRequest", "staysMapSearchRequestV2"):
            req = payload.get("variables", {}).get(req_key, {})
            raw_params = req.get("rawParams")
            if not raw_params:
                continue

            # The captured template's placeId is tied to the city where the browser
            # session was originally recorded.  Reusing it for a different city causes
            # Airbnb to ignore the query/centerLat/centerLng overrides and return
            # results for the wrong city (or nothing at all).  Strip it out whenever
            # the caller has not supplied an explicit replacement placeId.
            if "placeId" not in overrides:
                self._remove_raw_param(raw_params, "placeId")

            mapping = {
                "checkin": "checkin",
                "checkout": "checkout",
                "adults": "adults",
                "guests": "guests",
                "centerLat": "centerLat",
                "centerLng": "centerLng",
                "placeId": "placeId",
                "query": "query",
                # Some captured templates use location_search/location keys instead of query.
                "locationSearch": "location_search",
                "location": "location",
                "guestFavorite": "guestFavorite",
                "minBedrooms": "minBedrooms",
                "minBeds": "minBeds",
                "minBathrooms": "minBathrooms",
                "searchByMap": "searchByMap",
                "neLat": "neLat",
                "neLng": "neLng",
                "swLat": "swLat",
                "swLng": "swLng",
            }
            for key, raw_name in mapping.items():
                if key in overrides and overrides[key] is not None:
                    val = overrides[key]
                    if key in ("query", "locationSearch", "location"):
                        val = self._normalize_query_text(val)
                    if isinstance(val, bool):
                        val = "true" if val else "false"
                    if self._raw_param_exists(raw_params, raw_name):
                        self._set_raw_param(raw_params, raw_name, [str(val)])
            if "guestFavorite" not in overrides:
                if self._raw_param_exists(raw_params, "guestFavorite"):
                    self._set_raw_param(raw_params, "guestFavorite", ["false"])

            # Default to classic search behavior unless explicitly enabled.
            if self._raw_param_exists(raw_params, "aiSearchEnabled"):
                self._set_raw_param(
                    raw_params,
                    "aiSearchEnabled",
                    ["true" if self.enable_ai_search else "false"],
                )

            if "itemsPerGrid" in overrides and req_key == "staysSearchRequest":
                if self._raw_param_exists(raw_params, "itemsPerGrid"):
                    self._set_raw_param(raw_params, "itemsPerGrid", [str(overrides["itemsPerGrid"])])
            if "itemsOffset" in overrides and req_key == "staysSearchRequest":
                self._set_raw_param(raw_params, "itemsOffset", [str(overrides["itemsOffset"])])

        status_code, response_data = self._replay_request(self.captured_search_req, payload)
        if response_data.get("errors"):
            if _already_retried:
                return status_code, response_data
            if self._response_looks_auth_or_challenge_error(status_code, response_data):
                logger.warning("Auth/challenge-like error in StaysSearch with overrides. Refreshing and retrying once...")
                self.refresh_session(force_capture=True)
                payload = copy.deepcopy(self.captured_search_req["post_data"])
                self._apply_disable_map_search(payload)
                return self.search_listings_with_overrides(overrides, _already_retried=True)
            logger.warning("Non-auth GraphQL errors in StaysSearch with overrides; returning without forced auth refresh.")
            return status_code, response_data
        else:
            try:
                from worker.scraper.parsers import parse_search_total_listings

                total_listings = parse_search_total_listings(response_data)
                if isinstance(total_listings, int):
                    logger.info("StaysSearch(with overrides) total listings reported by Airbnb: %s", total_listings)
            except Exception:
                pass

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
        effective_checkin = checkin or self.config.get("CHECKIN", "")
        effective_checkout = checkout or self.config.get("CHECKOUT", "")
        effective_adults = int(adults if adults is not None else self.config.get("ADULTS", 1))
        pdp_fetch_url = (
            f"{self.base_url}/rooms/{listing_id}"
            f"?check_in={effective_checkin}&check_out={effective_checkout}"
        )
        logger.info("StaysPdpSections fetch url=%s", pdp_fetch_url)

        # Reuse the in-memory PDP template within a run; capture only when missing.
        # If replay returns GraphQL errors, recapture once and retry.
        if self.captured_pdp_req is None:
            if not (self.use_hardcoded_stayspdp_template and self._load_hardcoded_stayspdp_template()):
                self._capture_pdp_template_for_listing(
                    str(listing_id),
                    checkin=effective_checkin,
                    checkout=effective_checkout,
                    adults=effective_adults,
                    force_refresh=False,
                )

        payload = copy.deepcopy(self.captured_pdp_req["post_data"])
        stay_gid = self._to_global_id("StayListing", str(listing_id))
        demand_gid = self._to_global_id("DemandStayListing", str(listing_id))
        self._replace_listing_ids(payload, str(listing_id), stay_gid, demand_gid)

        try:
            request_vars = payload["variables"].get("pdpSectionsRequest", {})
            if request_vars:
                request_vars["adults"] = str(effective_adults)
                request_vars["checkIn"] = effective_checkin
                request_vars["checkOut"] = effective_checkout
                # Captured templates often pin only booking-related sections.
                # Dropping sectionIds asks backend for default/full section composition.
                # request_vars.pop("sectionIds", None)
        except (KeyError, TypeError):
            pass

        _, response_data = self._replay_request(self.captured_pdp_req, payload)
        if response_data.get("errors") and not self.use_hardcoded_stayspdp_template:
            logger.warning("GraphQL errors in StaysPdpSections. Refreshing PDP template and retrying once...")
            self._capture_pdp_template_for_listing(
                str(listing_id),
                checkin=effective_checkin,
                checkout=effective_checkout,
                adults=effective_adults,
                force_refresh=True,
            )
            payload = copy.deepcopy(self.captured_pdp_req["post_data"])
            self._replace_listing_ids(payload, str(listing_id), stay_gid, demand_gid)
            try:
                request_vars = payload["variables"].get("pdpSectionsRequest", {})
                if request_vars:
                    request_vars["adults"] = str(effective_adults)
                    request_vars["checkIn"] = effective_checkin
                    request_vars["checkOut"] = effective_checkout
                    request_vars.pop("sectionIds", None)
            except (KeyError, TypeError):
                pass
            _, response_data = self._replay_request(self.captured_pdp_req, payload)

        unresolved_key = f"{listing_id}|{effective_checkin}|{effective_checkout}|{effective_adults}"
        unresolved_until = float(self._pdp_unresolved_windows.get(unresolved_key, 0.0))
        should_retry_unresolved = time.time() >= unresolved_until
        if (
            self._pdp_booking_unresolved(response_data)
            and should_retry_unresolved
            and not self.use_hardcoded_stayspdp_template
        ):
            logger.warning(
                "StaysPdpSections booking payload unresolved (NOT_COMPLETE with no booking price). "
                "Recapturing PDP template and retrying once..."
            )
            self._capture_pdp_template_for_listing(
                str(listing_id),
                checkin=effective_checkin,
                checkout=effective_checkout,
                adults=effective_adults,
                force_refresh=True,
            )
            payload = copy.deepcopy(self.captured_pdp_req["post_data"])
            self._replace_listing_ids(payload, str(listing_id), stay_gid, demand_gid)
            try:
                request_vars = payload["variables"].get("pdpSectionsRequest", {})
                if request_vars:
                    request_vars["adults"] = str(effective_adults)
                    request_vars["checkIn"] = effective_checkin
                    request_vars["checkOut"] = effective_checkout
                    request_vars.pop("sectionIds", None)
            except (KeyError, TypeError):
                pass
            _, response_data = self._replay_request(self.captured_pdp_req, payload)
            if self._pdp_booking_unresolved(response_data):
                # Back off repeated recapture attempts for this exact window.
                self._pdp_unresolved_windows[unresolved_key] = time.time() + 20 * 60
        return response_data
