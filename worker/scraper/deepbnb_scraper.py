from typing import Any, Dict, Optional, Tuple

import requests

from worker.scraper.deepbnb_backend import DeepBnbBackend


class DeepBnbScraper:
    """Primary scraper strategy backed by DeepBnb GraphQL adapters."""

    def __init__(self, config: Dict[str, Any], base_url: str, session: Optional[requests.Session] = None):
        self.backend = DeepBnbBackend(config=config, base_url=base_url, session=session)

    def search_listings(self) -> Tuple[int, Dict[str, Any]]:
        result = self.backend.search_listings_with_overrides({})
        if result is None:
            raise RuntimeError("DeepBnb search_listings returned no result")
        return result

    def search_listings_with_overrides(self, overrides: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        result = self.backend.search_listings_with_overrides(overrides)
        if result is None:
            raise RuntimeError("DeepBnb search_listings_with_overrides returned no result")
        return result

    def get_listing_details(
        self,
        listing_id: str,
        *,
        checkin: str,
        checkout: str,
        adults: int,
    ) -> Dict[str, Any]:
        result = self.backend.get_listing_details(
            listing_id=listing_id,
            checkin=checkin,
            checkout=checkout,
            adults=adults,
        )
        if result is None:
            raise RuntimeError("DeepBnb get_listing_details returned no result")
        return result
