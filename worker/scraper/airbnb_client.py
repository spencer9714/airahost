import logging
import os
from typing import Any, Dict, Optional, Tuple

import requests

from worker.scraper.deepbnb_scraper import DeepBnbScraper
from worker.scraper.playwright_scraper import PlaywrightScraper
from worker.scraper.scraper_errors import ScraperForbiddenError

logger = logging.getLogger(__name__)


class AirbnbClient:
    """Controller that routes scraping requests across isolated scraper strategies."""

    def __init__(self, config: dict):
        self.config = config
        self.base_url = self.config.get("AIRBNB_BASE_URL", "https://www.airbnb.ca").rstrip("/")
        self.guest_favorite_only = bool(
            str(
                self.config.get(
                    "GUEST_FAVORITE_ONLY",
                    os.getenv("AIRBNB_GUEST_FAVORITE_ONLY", "1"),
                )
            ).strip().lower()
            in ("1", "true", "yes", "on")
        )
        self._playwright_scraper: Optional[PlaywrightScraper] = None
        self._deepbnb_session = requests.Session()

        use_deepbnb_cfg = self.config.get("USE_DEEPBNB_BACKEND", None)
        if use_deepbnb_cfg is None:
            self.use_deepbnb_backend = bool(
                str(os.getenv("AIRBNB_USE_DEEPBNB_BACKEND", "1")).strip().lower() in ("1", "true", "yes", "on")
            )
        else:
            self.use_deepbnb_backend = bool(use_deepbnb_cfg)

        self.deepbnb_scraper: Optional[DeepBnbScraper] = (
            DeepBnbScraper(config=self.config, base_url=self.base_url, session=self._deepbnb_session)
            if self.use_deepbnb_backend
            else None
        )

    @property
    def session(self):
        return self._get_playwright_scraper().session

    def _get_playwright_scraper(self) -> PlaywrightScraper:
        if self._playwright_scraper is None:
            self._playwright_scraper = PlaywrightScraper(self.config)
        return self._playwright_scraper

    def sync_fetch_session_cookies_from_playwright(self) -> None:
        """Copy current Playwright session cookies into the fetch backend session."""
        scraper = self._get_playwright_scraper()
        self._deepbnb_session.cookies.clear()
        for cookie in scraper.session.cookies:
            self._deepbnb_session.cookies.set(
                cookie.name,
                cookie.value,
                domain=cookie.domain,
                path=cookie.path,
                secure=cookie.secure,
                expires=cookie.expires,
            )

    def refresh_session(self, force_capture: bool = False, bypass_cooldown: bool = False):
        return self._get_playwright_scraper().refresh_session(force_capture=force_capture, bypass_cooldown=bypass_cooldown)

    def fork(self) -> "AirbnbClient":
        clone = AirbnbClient.__new__(AirbnbClient)
        clone.config = dict(self.config)
        clone.base_url = self.base_url
        clone.guest_favorite_only = self.guest_favorite_only
        clone._playwright_scraper = self._playwright_scraper.fork() if self._playwright_scraper is not None else None
        clone._deepbnb_session = requests.Session()
        if clone._playwright_scraper is not None:
            for cookie in clone._playwright_scraper.session.cookies:
                clone._deepbnb_session.cookies.set(
                    cookie.name,
                    cookie.value,
                    domain=cookie.domain,
                    path=cookie.path,
                    secure=cookie.secure,
                    expires=cookie.expires,
                )
        clone.use_deepbnb_backend = self.use_deepbnb_backend
        clone.deepbnb_scraper = (
            DeepBnbScraper(config=clone.config, base_url=clone.base_url, session=clone._deepbnb_session)
            if clone.use_deepbnb_backend
            else None
        )
        return clone

    def _search_via_playwright(self, overrides: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        scraper = self._get_playwright_scraper()
        if overrides is None:
            return scraper.search_listings()
        return scraper.search_listings_with_overrides(overrides)

    def search_listings(self) -> Tuple[int, Dict[str, Any]]:
        if self.deepbnb_scraper is not None:
            try:
                self.sync_fetch_session_cookies_from_playwright()
                return self.deepbnb_scraper.search_listings()
            except ScraperForbiddenError as exc:
                logger.error(
                    "DeepBnbScraper returned 403 Forbidden/block challenge for search_listings; "
                    "falling back to PlaywrightScraper: %s",
                    exc,
                )
            except Exception as exc:
                logger.warning("DeepBnbScraper search_listings failed; falling back to PlaywrightScraper: %s", exc)
        return self._search_via_playwright()

    def search_listings_with_overrides(
        self,
        overrides: Dict[str, Any],
    ) -> Tuple[int, Dict[str, Any]]:
        if self.deepbnb_scraper is not None:
            try:
                self.sync_fetch_session_cookies_from_playwright()
                return self.deepbnb_scraper.search_listings_with_overrides(overrides)
            except ScraperForbiddenError as exc:
                logger.error(
                    "DeepBnbScraper returned 403 Forbidden/block challenge for search_listings_with_overrides; "
                    "falling back to PlaywrightScraper: %s",
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "DeepBnbScraper search_listings_with_overrides failed; "
                    "falling back to PlaywrightScraper: %s",
                    exc,
                )
        return self._search_via_playwright(overrides)

    def get_listing_details(
        self,
        listing_id: str,
        checkin: Optional[str] = None,
        checkout: Optional[str] = None,
        adults: Optional[int] = None,
    ) -> Dict[str, Any]:
        effective_checkin = checkin or self.config.get("CHECKIN", "")
        effective_checkout = checkout or self.config.get("CHECKOUT", "")
        effective_adults = int(adults if adults is not None else self.config.get("ADULTS", 1))

        if self.deepbnb_scraper is not None:
            try:
                self.sync_fetch_session_cookies_from_playwright()
                return self.deepbnb_scraper.get_listing_details(
                    str(listing_id),
                    checkin=effective_checkin,
                    checkout=effective_checkout,
                    adults=effective_adults,
                )
            except ScraperForbiddenError as exc:
                logger.error(
                    "DeepBnbScraper returned 403 Forbidden/block challenge for get_listing_details; "
                    "falling back to PlaywrightScraper: %s",
                    exc,
                )
            except Exception as exc:
                logger.warning("DeepBnbScraper get_listing_details failed; falling back to PlaywrightScraper: %s", exc)

        return self._get_playwright_scraper().get_listing_details(
            listing_id=str(listing_id),
            checkin=effective_checkin,
            checkout=effective_checkout,
            adults=effective_adults,
        )
