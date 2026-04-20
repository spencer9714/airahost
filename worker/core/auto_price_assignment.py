
import datetime
import json
import logging
import urllib.request
import urllib.error
import os
from datetime import timedelta

import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_cookies_from_file(filepath: str) -> dict:
    """Helper to load cookies from a JSON file (e.g., exported from browser)."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # If it's a list of cookie dicts (e.g., from Playwright or EditThisCookie)
            if isinstance(data, list):
                return {cookie['name']: cookie['value'] for cookie in data if 'name' in cookie and 'value' in cookie}
            # If it's already a dictionary of key-value pairs
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.error(f"Failed to load cookies from {filepath}: {e}")
    return {}

def _parse_cookie_header(cookie_header: str | None) -> dict:
    if not isinstance(cookie_header, str) or not cookie_header.strip():
        return {}
    out: dict = {}
    for part in cookie_header.split(";"):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k:
            out[k] = v
    return out


def load_cookies_from_cdp(cdp_url: str, domain: str = ".airbnb.ca") -> dict:
    """
    Legacy entrypoint retained for compatibility.
    CDP/browser scraping is intentionally disabled; use Deepbnb-style HTTP bootstrap.
    """
    _ = cdp_url
    base_url = "https://www.airbnb.ca"
    locale = "en-CA"
    currency = "CAD"
    api_key = "d306zoyjsyarp7ifhu67rjxn52tv0t20"
    stays_search_hash = (
        os.getenv("AIRBNB_STAYSSEARCH_HASH")
        or "753d97c7b19a1a402d2fa63882ff4d6802004d11f2499647deef923a19a1641a"
    )

    # Optional direct cookie injection from env for authenticated mutations.
    env_cookie_header = os.getenv("AIRBNB_COOKIE_HEADER")
    env_cookies = _parse_cookie_header(env_cookie_header)
    if env_cookies:
        logger.info("Loaded Airbnb cookies from AIRBNB_COOKIE_HEADER.")
        return env_cookies

    session = requests.Session()
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "x-airbnb-api-key": api_key,
        "x-airbnb-graphql-platform": "web",
        "x-airbnb-graphql-platform-client": "minimalist-niobe",
        "x-csrf-without-token": "1",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
    }

    try:
        # Warm-up requests to collect baseline anti-bot/session cookies without browser.
        session.get(base_url, headers=headers, timeout=15)
        today = datetime.date.today()
        checkin = (today + timedelta(days=7)).strftime("%Y-%m-%d")
        checkout = (today + timedelta(days=14)).strftime("%Y-%m-%d")
        payload = {
            "operationName": "StaysSearch",
            "variables": {
                "staysSearchRequest": {
                    "metadataOnly": False,
                    "requestedPageType": "STAYS_SEARCH",
                    "searchType": "user_map_move",
                    "rawParams": [
                        {"filterName": "adults", "filterValues": ["2"]},
                        {"filterName": "query", "filterValues": ["Toronto, Ontario"]},
                        {"filterName": "checkin", "filterValues": [checkin]},
                        {"filterName": "checkout", "filterValues": [checkout]},
                        {"filterName": "screenSize", "filterValues": ["large"]},
                        {"filterName": "tabId", "filterValues": ["home_tab"]},
                        {"filterName": "version", "filterValues": ["1.8.8"]},
                        {"filterName": "searchMode", "filterValues": ["regular_search"]},
                    ],
                },
                "staysMapSearchRequestV2": {
                    "metadataOnly": False,
                    "requestedPageType": "STAYS_SEARCH",
                    "searchType": "user_map_move",
                    "rawParams": [
                        {"filterName": "adults", "filterValues": ["2"]},
                        {"filterName": "query", "filterValues": ["Toronto, Ontario"]},
                        {"filterName": "checkin", "filterValues": [checkin]},
                        {"filterName": "checkout", "filterValues": [checkout]},
                        {"filterName": "screenSize", "filterValues": ["large"]},
                        {"filterName": "tabId", "filterValues": ["home_tab"]},
                        {"filterName": "version", "filterValues": ["1.8.8"]},
                        {"filterName": "searchMode", "filterValues": ["regular_search"]},
                    ],
                },
                "isLeanTreatment": False,
                "aiSearchEnabled": False,
            },
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": stays_search_hash}},
        }
        search_url = (
            f"{base_url}/api/v3/StaysSearch/{stays_search_hash}"
            f"?operationName=StaysSearch&locale={locale}&currency={currency}"
        )
        session.post(search_url, json=payload, headers=headers, timeout=20)
    except Exception as e:
        logger.warning(f"HTTP cookie bootstrap failed: {e}")

    result = {}
    for c in session.cookies:
        domain_ok = isinstance(c.domain, str) and (domain in c.domain or "airbnb" in c.domain)
        if domain_ok:
            result[c.name] = c.value
    if result:
        logger.info("Bootstrapped Airbnb cookies via HTTP session (no browser).")
    return result

def assign_price_request(listing_id: str, date: str, price: int, *, locale="en-CA", currency="CAD", cookies: dict | None = None) -> dict:
    """
    Sets the nightly price directly using Airbnb's GraphQL API.
    """
    if not cookies:
        return {"ok": False, "error": "auth required"}

    if not listing_id:
        return {"ok": False, "error": "listing_id must be provided."}
    
    try:
        datetime.datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return {"ok": False, "error": "Invalid date format. Use YYYY-MM-DD."}
        
    if price < 1:
        return {"ok": False, "error": "Price must be a positive number."}

    # Prepare URL
    sha256_hash = "1de2c4649768cacd7bf82368dc9d298e7e7472b57ec8086c087b7524be2f03ef"
    url = f"https://www.airbnb.ca/api/v3/EditPanelPricingSettingsMutation/{sha256_hash}?operationName=EditPanelPricingSettingsMutation&locale={locale}&currency={currency}"

    # Prepare Headers
    cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    headers = {
        "content-type": "application/json",
        "x-airbnb-api-key": "d306zoyjsyarp7ifhu67rjxn52tv0t20",
        "x-airbnb-graphql-platform": "web",
        "x-airbnb-graphql-platform-client": "minimalist-niobe",
        "x-airbnb-supports-airlock-v2": "true",
        "x-csrf-without-token": "1",
        "accept": "*/*",
        "cookie": cookie_str
    }

    # Prepare Payload
    payload = {
        "operationName": "EditPanelPricingSettingsMutation",
        "variables": {
            "input": {
                "listingId": listing_id,
                "selectedDateRanges": [
                    { "startDate": date, "endDate": date }
                ],
                "nightlyPriceAmount": price,
                "turnOnSmartPricing": False,
                "isCalendarV2": True
            }
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": sha256_hash
            }
        }
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req) as response:
            if response.status != 200:
                return {"ok": False, "error": f"HTTP {response.status}: {response.reason}"}
            
            resp_data = json.loads(response.read().decode('utf-8'))
            
            if "errors" in resp_data:
                return {"ok": False, "error": resp_data["errors"]}
                
            return {"ok": True}
            
    except urllib.error.HTTPError as e:
        try:
            error_resp = json.loads(e.read().decode('utf-8'))
            if "errors" in error_resp:
                return {"ok": False, "error": error_resp["errors"]}
            return {"ok": False, "error": f"HTTPError {e.code}: {error_resp}"}
        except json.JSONDecodeError:
            return {"ok": False, "error": f"HTTPError {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": f"Request failed: {str(e)}"}

def assign_prices_calendar_request(listing_id: str, calendar_dict: dict, *, locale="en-CA", currency="CAD", cookies: dict | None = None) -> dict:
    """
    Batch assigns prices for a given listing and a dictionary of dates.
    """
    summary = {"ok": True, "results": {}, "errors": [], "skipped": []}
    if not cookies:
        return {"ok": False, "error": "auth required"}
    for date in sorted(calendar_dict.keys()):
        price = calendar_dict[date]
        logger.info(f"Setting price for {date} to ${price} via API")
        result = assign_price_request(listing_id=listing_id, date=date, price=price, locale=locale, currency=currency, cookies=cookies)
        summary["results"][date] = result
        if not result.get("ok"):
            summary["ok"] = False
            summary["errors"].append({date: result.get("error")})
    return summary

def assign_price(listing_id: str, date: str, price: int, *, cdp_url: str, cookies: dict | None = None) -> dict:
    """
    Sets the price for a given listing and date on Airbnb using a direct API request.

    Args:
        listing_id: The ID of the Airbnb listing.
        date: The date to set the price for (YYYY-MM-DD).
        price: The price to set.
        cdp_url: The CDP URL of the running browser instance.
        cookies: Optional dictionary of cookies. If not provided, will be fetched via CDP.

    Returns:
        A dictionary with "ok": True on success, or "ok": False and an "error" message on failure.
    """

    # Input validation
    if not listing_id:
        return {"ok": False, "error": "listing_id must be provided."}
    try:
        datetime.datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return {"ok": False, "error": "Invalid date format. Use YYYY-MM-DD."}
    if price < 1:
        return {"ok": False, "error": "Price must be a positive number."}

    if cookies is None:
        logger.info("Bootstrapping cookies via HTTP session (Deepbnb-style, no browser).")
        cookies = load_cookies_from_cdp(cdp_url)
        if not cookies:
            return {"ok": False, "error": "Could not bootstrap cookies via HTTP session."}

    return assign_price_request(listing_id=listing_id, date=date, price=price, cookies=cookies)

def assign_prices_calendar(listing_id: str, calendar: dict[str, int], *, cdp_url: str, cookies: dict | None = None) -> dict:
    """
    Batch assigns prices for a given listing and a dictionary of dates.
    """
    if cookies is None:
        logger.info("Bootstrapping cookies via HTTP session (Deepbnb-style, no browser).")
        cookies = load_cookies_from_cdp(cdp_url)
        if not cookies:
            return {"ok": False, "error": "Could not bootstrap cookies via HTTP session."}

    return assign_prices_calendar_request(listing_id=listing_id, calendar_dict=calendar, cookies=cookies)
