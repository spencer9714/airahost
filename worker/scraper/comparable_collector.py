"""
Comparable listing collection from Airbnb search results.

Builds search URLs, scrolls search pages, extracts listing cards,
and parses them into ListingSpec objects for similarity comparison.

Extracted from price_estimator.py for modularity.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List
from urllib.parse import quote

from worker.scraper.target_extractor import (
    BATH_RE,
    BED_RE,
    BEDROOM_RE,
    GUEST_RE,
    ListingSpec,
    clean,
    extract_amenities,
    extract_first_float,
    extract_first_int,
    normalize_property_type,
    parse_money_to_float,
)

logger = logging.getLogger("worker.scraper.comparable_collector")


def build_search_url(
    base_origin: str, location: str, checkin: str, checkout: str, adults: int
) -> str:
    q = quote(location)
    return (
        f"{base_origin}/s/{q}/homes"
        f"?checkin={checkin}&checkout={checkout}&adults={adults}"
    )


def collect_search_cards(page) -> List[Dict[str, Any]]:
    """Extract listing card data from a search results page."""
    return page.evaluate(
        """() => {
          const cardRoots = []
            .concat(Array.from(
              document.querySelectorAll('div[data-testid="card-container"]')
            ))
            .concat(Array.from(
              document.querySelectorAll('div[data-testid^="listing-card"]')
            ))
            .concat(
              Array.from(document.querySelectorAll('a[href*="/rooms/"]'))
                .map(a => a.closest('div[data-testid],div') || a)
            );
          const uniq = new Set();
          const cards = [];
          for (const root of cardRoots) {
            if (!root) continue;
            const a = root.querySelector('a[href*="/rooms/"]');
            if (!a) continue;
            const href = a.getAttribute('href') || '';
            const abs = href.startsWith('http')
              ? href
              : location.origin + href;
            const m = abs.match(/\\/rooms\\/(\\d+)/);
            const roomId = m ? m[1] : abs;
            if (uniq.has(roomId)) continue;
            uniq.add(roomId);
            const text = (root.innerText || '').trim();
            const aria = a.getAttribute('aria-label') || '';
            const title =
              aria || (text.split('\\n').find(x => x.trim().length > 6) || '');
            let priceText = '';
            const priceCandidates = Array.from(
              root.querySelectorAll('[data-testid*="price"],span,div')
            )
              .map(el => (el.innerText || '').trim())
              .filter(
                t =>
                  t &&
                  (t.includes('$') ||
                    t.includes('US$') ||
                    t.includes('每晚') ||
                    t.includes('晚'))
              );
            priceCandidates.sort((a, b) => a.length - b.length);
            priceText =
              priceCandidates.find(
                t => t.includes('$') || t.includes('US$')
              ) || (priceCandidates[0] || '');
            let rating = null;
            let reviews = null;
            const rateMatch = text.match(
              /(\\d\\.\\d\\d|\\d\\.\\d)\\s*(?:\\(|·|・)?\\s*(\\d+)?/
            );
            if (rateMatch) {
              const r = parseFloat(rateMatch[1]);
              if (!isNaN(r) && r >= 2.5 && r <= 5.0) rating = r;
              if (rateMatch[2]) {
                const n = parseInt(rateMatch[2], 10);
                if (!isNaN(n)) reviews = n;
              }
            }
            cards.push({
              room_id: roomId,
              url: abs,
              title,
              text,
              price_text: priceText,
              rating,
              reviews,
            });
          }
          return cards;
        }"""
    )


def scroll_and_collect(
    page,
    max_rounds: int = 12,
    max_cards: int = 80,
    pause_ms: int = 900,
    rate_limit_seconds: float = 1.0,
) -> List[Dict[str, Any]]:
    """Scroll the search page and collect listing cards with rate limiting."""
    all_cards: Dict[str, Dict[str, Any]] = {}
    no_new = 0

    for rd in range(1, max_rounds + 1):
        try:
            page.wait_for_timeout(400)
            cards = collect_search_cards(page)
        except Exception:
            cards = []

        new_count = 0
        for c in cards:
            rid = str(c.get("room_id") or c.get("url") or "")
            if rid and rid not in all_cards:
                all_cards[rid] = c
                new_count += 1

        logger.info(f"[SCAN] round={rd} new={new_count} total={len(all_cards)}")
        no_new = no_new + 1 if new_count == 0 else 0

        if no_new >= 3 or len(all_cards) >= max_cards:
            break

        # Rate limit between scroll rounds
        time.sleep(rate_limit_seconds)

        try:
            page.mouse.wheel(0, 1600)
        except Exception:
            pass
        page.wait_for_timeout(pause_ms)

    return list(all_cards.values())


def parse_card_to_spec(card: Dict[str, Any]) -> ListingSpec:
    """Convert a raw card dict into a ListingSpec."""
    text = clean(card.get("text") or "")
    price_text = clean(card.get("price_text") or "")
    price = parse_money_to_float(price_text)

    return ListingSpec(
        url=str(card.get("url") or ""),
        title=clean(card.get("title") or ""),
        accommodates=extract_first_int(text, [GUEST_RE]),
        bedrooms=extract_first_int(text, [BEDROOM_RE]),
        beds=extract_first_int(text, [BED_RE]),
        baths=extract_first_float(text, [BATH_RE]),
        property_type=normalize_property_type(text),
        nightly_price=price,
        rating=card.get("rating"),
        reviews=card.get("reviews"),
        amenities=extract_amenities(text),
    )
