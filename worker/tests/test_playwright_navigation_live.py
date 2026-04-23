import os

import pytest
from playwright.sync_api import sync_playwright


TARGET_URL = (
    "https://www.airbnb.ca/rooms/1408676238256636483"
    "?check_in=2026-05-17&check_out=2026-05-18&guests=1&adults=1"
)


def test_playwright_navigate_room_url_not_about_blank():
    """
    Live debug test for the about:blank navigation issue.

    Opt-in:
      RUN_PLAYWRIGHT_LIVE_NAV_TEST=1
      CDP_URL=http://127.0.0.1:9222
    """

    cdp_url = str(os.getenv("CDP_URL", "http://127.0.0.1:9222")).strip()
    created_context = None

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url, timeout=15000)
        had_contexts = bool(browser.contexts)
        context = browser.contexts[0] if had_contexts else browser.new_context()
        if not had_contexts:
            created_context = context

        page = context.new_page()
        try:
            print(f"[playwright_nav_test] request_url={TARGET_URL}")
            response = page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
            final_url = str(page.url or "")
            status = None
            try:
                status = response.status if response is not None else None
            except Exception:
                status = None
            print(f"[playwright_nav_test] final_url={final_url} status={status}")

            assert final_url, "Playwright final URL is empty"
            assert not final_url.lower().startswith("about:blank"), (
                "Playwright remained on about:blank "
                f"(request_url={TARGET_URL}, final_url={final_url}, status={status})"
            )
        finally:
            try:
                page.close()
            except Exception:
                pass
            if created_context is not None:
                try:
                    created_context.close()
                except Exception:
                    pass

test_playwright_navigate_room_url_not_about_blank()