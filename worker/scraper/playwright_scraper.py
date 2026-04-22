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
from worker.scraper.stayspdp_template import HARDCODED_STAYS_PDP_TEMPLATE

logger = logging.getLogger(__name__)


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
                str(os.getenv("AIRBNB_USE_HARDCODED_STAYSPDP_TEMPLATE", "0")).strip().lower()
                in ("1", "true", "yes", "on")
            )
        else:
            self.use_hardcoded_stayspdp_template = bool(hardcoded_pdp_cfg)
        # Cache unresolved PDP booking windows to avoid repeated expensive
        # template recaptures when Airbnb consistently returns NOT_COMPLETE.
        self._pdp_unresolved_windows: Dict[str, float] = {}
        self._browser_lock = threading.Lock()
        self._pw = None
        self._browser = None
        self._context = None
        self._cdp_url = str(
            self.config.get("CDP_URL")
            or os.getenv("CDP_URL", "")
        ).strip()
        if self.use_hardcoded_stayspdp_template:
            self._load_hardcoded_stayspdp_template()

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
        clone._browser_lock = threading.Lock()
        clone._pw = None
        clone._browser = None
        clone._context = None
        clone._cdp_url = self._cdp_url
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
        Browser-only mode: do not capture/replay API templates or headless cookies.
        """
        with self._refresh_lock:
            self._last_refresh_started_at = time.time()
            self.captured_search_req = None
            self.captured_pdp_req = None
            logger.info("Playwright refresh_session is a no-op in browser-only mode.")

    def _capture_pdp_template_for_listing(
        self,
        listing_id: str,
        checkin: Optional[str] = None,
        checkout: Optional[str] = None,
        adults: Optional[int] = None,
        force_refresh: bool = True,
    ):
        """Deprecated in browser-only mode (no API template capture/replay)."""
        logger.info(
            "Skipping _capture_pdp_template_for_listing in browser-only mode for listing_id=%s",
            listing_id,
        )
        self.captured_pdp_req = None

    def _refresh_before_search_if_enabled(self) -> None:
        if not self.refresh_before_each_search:
            return
        logger.info("REFRESH_SESSION_BEFORE_EACH_SEARCH enabled; refreshing session/templates before StaysSearch replay.")
        # Force a fresh capture so request tokens/cookies are rotated each search call.
        self.refresh_session(force_capture=True, bypass_cooldown=True)

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
        """Browser-only StaysSearch (no API replay)."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, 3):
            try:
                status_code, response_data = self._search_via_browser()
                if response_data.get("errors") and self._response_looks_auth_or_challenge_error(status_code, response_data):
                    raise RuntimeError("Browser StaysSearch returned auth/challenge-like GraphQL error")
                return status_code, response_data
            except Exception as exc:
                last_exc = exc
                logger.warning("Browser StaysSearch attempt %s/2 failed: %s", attempt, exc)
                if attempt < 2:
                    time.sleep(1.0)
        raise RuntimeError(f"Playwright browser search failed after 2 attempts: {last_exc}")

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

    def _build_search_navigation_url(self, overrides: Optional[Dict[str, Any]] = None) -> str:
        ov = overrides or {}
        query_raw = (
            ov.get("query")
            or ov.get("locationSearch")
            or ov.get("location")
            or self.config.get("QUERY", "Mississauga, Ontario")
        )
        normalized_display_query = self._normalize_query_text(query_raw)
        path_query = normalized_display_query.replace(", ", ",")
        search_path = f"/s/{quote(path_query).replace('%2C', '--')}/homes"

        params: Dict[str, Any] = {
            "date_picker_type": self.config.get("DATE_PICKER_TYPE", "calendar"),
            "center_lat": ov.get("centerLat", self.config.get("CENTER_LAT", "")),
            "center_lng": ov.get("centerLng", self.config.get("CENTER_LNG", "")),
            "refinement_paths[]": "/homes",
            "place_id": ov.get("placeId", self.config.get("PLACE_ID", "")),
            "checkin": ov.get("checkin", self.config.get("CHECKIN", "")),
            "checkout": ov.get("checkout", self.config.get("CHECKOUT", "")),
            "adults": ov.get("adults", ov.get("guests", self.config.get("ADULTS", 1))),
            "query": normalized_display_query,
            "search_type": "AUTOSUGGEST",
        }
        if "itemsPerGrid" in ov and ov.get("itemsPerGrid") is not None:
            params["items_per_grid"] = ov.get("itemsPerGrid")
        if "itemsOffset" in ov and ov.get("itemsOffset") is not None:
            params["items_offset"] = ov.get("itemsOffset")
        return f"{self.base_url}{search_path}?{urlencode(params)}"

    def _ensure_browser_context(self):
        if self._context is not None:
            return self._context

        self._pw = sync_playwright().start()
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        )
        viewport = {"width": 1280, "height": 800}

        if self._cdp_url:
            logger.info("Connecting Playwright to existing browser via CDP: %s", self._cdp_url)
            self._browser = self._pw.chromium.connect_over_cdp(self._cdp_url, timeout=15000)
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
            else:
                self._context = self._browser.new_context(user_agent=user_agent, viewport=viewport)
        else:
            logger.info("Launching dedicated Playwright browser (no CDP URL configured)")
            self._browser = self._pw.chromium.launch(headless=False)
            self._context = self._browser.new_context(user_agent=user_agent, viewport=viewport)

        return self._context

    def _sync_session_cookies_into_context(self) -> None:
        context = self._ensure_browser_context()
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

    def _sync_context_cookies_into_session(self) -> None:
        context = self._ensure_browser_context()
        self.session.cookies.clear()
        for cookie in context.cookies():
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie["domain"],
                path=cookie["path"],
            )

    def close_browser(self) -> None:
        with self._browser_lock:
            try:
                if self._browser is not None:
                    self._browser.close()
            except Exception:
                pass
            try:
                if self._pw is not None:
                    self._pw.stop()
            except Exception:
                pass
            self._context = None
            self._browser = None
            self._pw = None

    def __del__(self):
        try:
            self.close_browser()
        except Exception:
            pass

    def _search_via_browser(self, overrides: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        """Run a real browser search and capture the live StaysSearch JSON response."""
        with self._browser_lock:
            context = self._ensure_browser_context()
            self._sync_session_cookies_into_context()
            page = context.new_page()
            try:

                captured_status: int = 0
                captured_data: Optional[Dict[str, Any]] = None

                def _on_response(resp):
                    nonlocal captured_status, captured_data
                    try:
                        if resp.request.method != "POST":
                            return
                        if "/api/v3/StaysSearch/" not in resp.url:
                            return
                        captured_status = int(resp.status)
                        payload = resp.json()
                        if isinstance(payload, dict):
                            captured_data = payload
                    except Exception:
                        return

                page.on("response", _on_response)

                search_url = self._build_search_navigation_url(overrides)
                logger.info("Playwright browser search navigate: %s", search_url)
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(int(random.uniform(900, 1600)))
                page.mouse.wheel(0, 600)

                for _ in range(24):
                    if captured_data is not None:
                        break
                    page.wait_for_timeout(int(random.uniform(250, 550)))

                if captured_data is None:
                    # One fallback nudge to trigger XHR search.
                    fallback_url = search_url + ("&search_type=filter_change" if "search_type=" in search_url else "")
                    page.goto(fallback_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(int(random.uniform(900, 1600)))
                    page.mouse.wheel(0, 700)
                    for _ in range(24):
                        if captured_data is not None:
                            break
                        page.wait_for_timeout(int(random.uniform(250, 550)))

                self._sync_context_cookies_into_session()
                self._save_cached_state()

                if captured_data is None:
                    raise RuntimeError("Playwright browser search did not capture StaysSearch response")
                return (captured_status or 200), captured_data
            finally:
                try:
                    page.close()
                except Exception:
                    pass

    def _get_listing_details_via_browser(
        self,
        listing_id: str,
        checkin: str,
        checkout: str,
        adults: int,
    ) -> Tuple[int, Dict[str, Any]]:
        """Run a real browser PDP visit and capture live StaysPdpSections JSON response."""
        with self._browser_lock:
            context = self._ensure_browser_context()
            self._sync_session_cookies_into_context()
            page = context.new_page()
            try:

                captured_status: int = 0
                captured_data: Optional[Dict[str, Any]] = None

                def _on_response(resp):
                    nonlocal captured_status, captured_data
                    try:
                        if resp.request.method != "POST":
                            return
                        if "/api/v3/StaysPdpSections/" not in resp.url:
                            return
                        captured_status = int(resp.status)
                        payload = resp.json()
                        if isinstance(payload, dict):
                            captured_data = payload
                    except Exception:
                        return

                page.on("response", _on_response)

                listing_url = (
                    f"{self.base_url}/rooms/{listing_id}"
                    f"?check_in={checkin}&check_out={checkout}&guests={adults}&adults={adults}"
                )
                logger.info("Playwright browser PDP navigate: %s", listing_url)
                page.goto(listing_url, wait_until="domcontentloaded", timeout=35000)
                page.wait_for_timeout(int(random.uniform(900, 1600)))
                if self._page_looks_challenged(page.content(), str(page.url or "")):
                    raise RuntimeError("Airbnb challenge/login page detected during browser PDP fetch")
                page.mouse.wheel(0, 1200)

                for _ in range(28):
                    if captured_data is not None:
                        break
                    page.wait_for_timeout(int(random.uniform(250, 550)))

                self._sync_context_cookies_into_session()
                self._save_cached_state()

                if captured_data is None:
                    raise RuntimeError("Playwright browser PDP fetch did not capture StaysPdpSections response")
                if captured_data.get("errors") and self._response_looks_auth_or_challenge_error(
                    captured_status,
                    captured_data,
                ):
                    raise RuntimeError("Playwright browser PDP returned auth/challenge-like GraphQL error")
                return (captured_status or 200), captured_data
            finally:
                try:
                    page.close()
                except Exception:
                    pass

    def search_listings_with_overrides(
        self,
        overrides: Dict[str, Any],
        _already_retried: bool = False,
    ) -> Tuple[int, Dict[str, Any]]:
        """Browser-only StaysSearch with overrides (no API replay)."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, 3):
            try:
                status_code, response_data = self._search_via_browser(overrides)
                if response_data.get("errors") and self._response_looks_auth_or_challenge_error(status_code, response_data):
                    raise RuntimeError("Browser StaysSearch(with overrides) returned auth/challenge-like GraphQL error")
                return status_code, response_data
            except Exception as exc:
                last_exc = exc
                logger.warning("Browser StaysSearch(with overrides) attempt %s/2 failed: %s", attempt, exc)
                if attempt < 2:
                    time.sleep(1.0)
        raise RuntimeError(f"Playwright browser search(with overrides) failed after 2 attempts: {last_exc}")

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
        """Browser-only PDP capture (no request replay/session.post)."""
        effective_checkin = checkin or self.config.get("CHECKIN", "")
        effective_checkout = checkout or self.config.get("CHECKOUT", "")
        effective_adults = int(adults if adults is not None else self.config.get("ADULTS", 1))
        last_exc: Optional[Exception] = None
        for attempt in range(1, 3):
            try:
                _, response_data = self._get_listing_details_via_browser(
                    listing_id=str(listing_id),
                    checkin=effective_checkin,
                    checkout=effective_checkout,
                    adults=effective_adults,
                )
                return response_data
            except Exception as exc:
                last_exc = exc
                logger.warning("Browser PDP attempt %s/2 failed for listing=%s: %s", attempt, listing_id, exc)
                if attempt < 2:
                    time.sleep(1.0)
        raise RuntimeError(f"Playwright browser PDP failed after 2 attempts for listing={listing_id}: {last_exc}")

