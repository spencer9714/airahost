"""Reusable Airbnb search collection functions."""
import argparse
import logging
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

try:
    from airbnb_client import AirbnbClient
    from parsers import (
        parse_search_listing_context,
        parse_search_response,
    )
except ImportError:
    from worker.scraper.airbnb_client import AirbnbClient
    from worker.scraper.parsers import (
        parse_search_listing_context,
        parse_search_response,
    )

try:
    from worker.core.concurrent_runner import execute_day_queries_concurrently
except ImportError:
    from core.concurrent_runner import execute_day_queries_concurrently

# ---------------------------------------------------------
# SCRAPER CONFIGURATION
# ---------------------------------------------------------
CONFIG = {
    "AIRBNB_BASE_URL": "https://www.airbnb.ca",
    "CHECKIN": "2026-04-04",
    "CHECKOUT": "2026-04-10",
    "ADULTS": 1,
    "CENTER_LAT": 43.67,
    "CENTER_LNG": -79.42,
    "CHANNEL": "EXPLORE",
    "DATE_PICKER_TYPE": "calendar",
    "FLEXIBLE_TRIP_LENGTHS": ["one_week"],
    "ITEMS_PER_GRID": 18,
    "MONTHLY_END_DATE": "2026-08-01",
    "MONTHLY_LENGTH": 3,
    "MONTHLY_START_DATE": "2026-05-01",
    "PLACE_ID": "ChIJtwVr559GK4gR22ZZ175sFAM",
    "PRICE_FILTER_INPUT_TYPE": 2,
    "PRICE_FILTER_NUM_NIGHTS": 6,
    "QUERY": "Mississauga, Ontario",
    "REFINEMENT_PATHS": ["/homes"],
    "SCREEN_SIZE": "large",
    "SEARCH_MODE": "regular_search",
    "TAB_ID": "home_tab",
    "VERSION": "1.8.8",
    "CURRENCY": "CAD",
    "LOCALE": "en-CA",
    "MAX_LISTINGS": 20,
    "SESSION_CACHE_PATH": ".airbnb_session_cache.json",
    "SESSION_MAX_AGE_SECONDS": 21600,
    "CAPTURE_PDP_ON_START": False,
    "DEBUG": False,
}

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("worker")


def _date_iter(start_yyyy_mm_dd: str, end_yyyy_mm_dd: str):
    start_d = datetime.strptime(start_yyyy_mm_dd, "%Y-%m-%d").date()
    end_d = datetime.strptime(end_yyyy_mm_dd, "%Y-%m-%d").date()
    cur = start_d
    while cur <= end_d:
        yield cur
        cur += timedelta(days=1)


def _build_search_url(*, checkin: str, checkout: str, adults: int) -> str:
    base_origin = CONFIG.get("AIRBNB_BASE_URL", "https://www.airbnb.ca").rstrip("/")
    query = str(CONFIG.get("QUERY", "Mississauga, Ontario"))
    center_lat = CONFIG.get("CENTER_LAT")
    center_lng = CONFIG.get("CENTER_LNG")
    place_id = CONFIG.get("PLACE_ID")
    date_picker_type = CONFIG.get("DATE_PICKER_TYPE", "calendar")
    refinement_paths = CONFIG.get("REFINEMENT_PATHS") or ["/homes"]
    search_type = CONFIG.get("SEARCH_MODE", "AUTOSUGGEST")

    query_path = quote(query.replace(",", "--"), safe="-")
    params = {
        "date_picker_type": date_picker_type,
        "center_lat": center_lat,
        "center_lng": center_lng,
        "refinement_paths[]": refinement_paths,
        "place_id": place_id,
        "checkin": checkin,
        "checkout": checkout,
        "adults": adults,
        "search_type": search_type,
    }
    encoded = urlencode(params, doseq=True)
    return f"{base_origin}/s/{query_path}/homes?{encoded}"


def collect_houses_around_listing_for_date_range(
    client: AirbnbClient,
    anchor_listing_id: str,
    range_start: str,
    range_end: str,
    *,
    max_listings: int | None = None,
):
    """
    For each date in [range_start, range_end], search houses near configured location and collect prices.
    """
    max_listings = int(max_listings or CONFIG["MAX_LISTINGS"])

    # Use configured location context directly for speed.
    center_lat = CONFIG.get("CENTER_LAT")
    center_lng = CONFIG.get("CENTER_LNG")
    place_id = CONFIG.get("PLACE_ID")
    query = CONFIG.get("QUERY")

    logger.info(
        "Searching around anchor %s with context: lat=%s lng=%s place_id=%s query=%s",
        anchor_listing_id,
        center_lat,
        center_lng,
        place_id,
        query,
    )

    def _collect_rows_for_date(d):
        checkin = d.strftime("%Y-%m-%d")
        checkout = (d + timedelta(days=1)).strftime("%Y-%m-%d")
        search_url = _build_search_url(
            checkin=checkin,
            checkout=checkout,
            adults=int(CONFIG.get("ADULTS", 1)),
        )

        logger.info("Searching nearby listings for date %s -> %s ...", checkin, checkout)
        status_code, search_data = client.search_listings_with_overrides(
            {
                "checkin": checkin,
                "checkout": checkout,
                "adults": int(CONFIG.get("ADULTS", 1)),
                "centerLat": center_lat,
                "centerLng": center_lng,
                "placeId": place_id,
                "query": query,
                "itemsPerGrid": max_listings,
            }
        )
        logger.info("StaysSearch status for %s: %s", checkin, status_code)

        listing_ids = parse_search_response(search_data)[:max_listings]
        # Ensure the anchor listing is included for each date window.
        if anchor_listing_id not in listing_ids:
            listing_ids = [anchor_listing_id] + listing_ids
            listing_ids = listing_ids[:max_listings]
        search_context = parse_search_listing_context(search_data)

        date_rows = []
        logger.info("Found %s listings for %s", len(listing_ids), checkin)
        for idx, listing_id in enumerate(listing_ids, 1):
            logger.info("Date %s: processing listing %s/%s (%s)", checkin, idx, len(listing_ids), listing_id)
            fallback = search_context.get(str(listing_id), {})
            is_anchor = str(listing_id) == str(anchor_listing_id)
            is_available = bool(fallback.get("is_available", True))
            has_price = isinstance(fallback.get("nightly_price"), (int, float)) or isinstance(
                fallback.get("total_price"), (int, float)
            )
            # Keep priced rows even when availability heuristics are noisy.
            effective_available = bool(is_available or has_price)
            if not is_anchor and (not effective_available or not has_price):
                logger.info(
                    "Date %s: skipping listing %s (available=%s has_price=%s reason=%s min_nights=%s)",
                    checkin,
                    listing_id,
                    is_available,
                    has_price,
                    fallback.get("availability_reason"),
                    fallback.get("min_nights"),
                )
                continue

            row = {
                "date": checkin,
                "anchor_listing_id": anchor_listing_id,
                "listing_id": str(listing_id),
                "title": None,
                "nightly_price": None,
                "total_price": None,
                "cleaning_fee": None,
                "service_fee": None,
                "amenities": [],
                "listing_url": f"{CONFIG['AIRBNB_BASE_URL']}/rooms/{listing_id}",
                "search_url": search_url,
            }

            # Search-card price/title fallback (fast)
            row["title"] = fallback.get("title")
            row["nightly_price"] = fallback.get("nightly_price")
            row["total_price"] = fallback.get("total_price")

            if not row["title"]:
                row["title"] = f"Listing {listing_id}"
            date_rows.append(row)

        return date_rows

    date_args = list(_date_iter(range_start, range_end))
    date_row_batches, _ = execute_day_queries_concurrently(
        query_func=_collect_rows_for_date,
        args_list=date_args,
        max_workers=3,
    )
    all_rows = []
    for rows in date_row_batches:
        all_rows.extend(rows)

    return all_rows


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Airbnb nearby listings.")
    parser.add_argument("--anchor-listing-id", required=True, help="Anchor Airbnb listing id")
    parser.add_argument("--start-date", default=CONFIG["CHECKIN"], help="Range start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=CONFIG["CHECKOUT"], help="Range end date (YYYY-MM-DD)")
    parser.add_argument("--max-listings", type=int, default=CONFIG["MAX_LISTINGS"], help="Max listings per day")
    return parser


def main() -> None:
    args = _build_cli().parse_args()

    client = AirbnbClient(CONFIG)
    rows = collect_houses_around_listing_for_date_range(
        client=client,
        anchor_listing_id=str(args.anchor_listing_id),
        range_start=str(args.start_date),
        range_end=str(args.end_date),
        max_listings=int(args.max_listings),
    )
    logger.info("Collected %s rows", len(rows))


if __name__ == "__main__":
    main()
