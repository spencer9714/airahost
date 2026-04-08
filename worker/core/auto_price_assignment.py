
import datetime
import json
import logging
import urllib.request
import urllib.error
import os
from playwright.sync_api import sync_playwright

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

def load_cookies_from_cdp(cdp_url: str, domain: str = ".airbnb.ca") -> dict:
    """Grabs cookies from a running Playwright CDP browser session."""
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            cookies = context.cookies()
            
            result = {}
            for cookie in cookies:
                if domain in cookie['domain']:
                    result[cookie['name']] = cookie['value']
            return result
        except Exception as e:
            logger.error(f"Failed to load cookies from CDP {cdp_url}: {e}")
            return {}

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
        logger.info(f"Extracting cookies from CDP: {cdp_url}")
        cookies = load_cookies_from_cdp(cdp_url)
        if not cookies:
            return {"ok": False, "error": "Could not extract cookies from CDP session."}

    return assign_price_request(listing_id=listing_id, date=date, price=price, cookies=cookies)

def assign_prices_calendar(listing_id: str, calendar: dict[str, int], *, cdp_url: str, cookies: dict | None = None) -> dict:
    """
    Batch assigns prices for a given listing and a dictionary of dates.
    """
    if cookies is None:
        logger.info(f"Extracting cookies from CDP: {cdp_url}")
        cookies = load_cookies_from_cdp(cdp_url)
        if not cookies:
            return {"ok": False, "error": "Could not extract cookies from CDP session."}

    return assign_prices_calendar_request(listing_id=listing_id, calendar_dict=calendar, cookies=cookies)