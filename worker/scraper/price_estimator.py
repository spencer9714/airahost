"""
Playwright-based Airbnb price estimator.

Connects to a local Chrome instance via CDP (Chrome DevTools Protocol)
to scrape target listing specs and nearby comparable listings.

Adapted from backend/main.py — refactored into reusable functions
for the worker queue system.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

logger = logging.getLogger("worker.scraper")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ListingSpec:
    url: str
    title: str = ""
    location: str = ""
    accommodates: Optional[int] = None
    bedrooms: Optional[int] = None
    beds: Optional[int] = None
    baths: Optional[float] = None
    property_type: str = ""
    nightly_price: Optional[float] = None
    currency: str = "USD"
    rating: Optional[float] = None
    reviews: Optional[int] = None


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(
    r"(?<!\w)(?:US)?\s?\$?\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?(?!\w)"
)
_BEDROOM_RE = re.compile(r"(\d+)\s*(?:bedroom|bedrooms|間臥室|卧室)", re.I)
_BED_RE = re.compile(r"(\d+)\s*(?:bed|beds|張床|床)", re.I)
_BATH_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:bath|baths|衛浴|浴室|衛生間|卫生间)", re.I
)
_GUEST_RE = re.compile(r"(\d+)\s*(?:guest|guests|位|人)", re.I)


def _clean(s: str) -> str:
    return (s or "").replace("\u00a0", " ").strip()


def _to_int(x: str) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def _to_float(x: str) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _extract_first_int(text: str, patterns: list) -> Optional[int]:
    for p in patterns:
        m = p.search(text or "")
        if m:
            return _to_int(m.group(1))
    return None


def _extract_first_float(text: str, patterns: list) -> Optional[float]:
    for p in patterns:
        m = p.search(text or "")
        if m:
            return _to_float(m.group(1))
    return None


def _parse_money_to_float(text: str) -> Optional[float]:
    if not text:
        return None
    m = _MONEY_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def _weighted_median(
    values: List[float], weights: List[float]
) -> Optional[float]:
    if not values or not weights or len(values) != len(weights):
        return None
    pairs = sorted(zip(values, weights), key=lambda x: x[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        return None
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= total / 2:
            return v
    return pairs[-1][0]


def _safe_domain_base(url: str) -> str:
    p = urlparse(url)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return "https://www.airbnb.com"


# ---------------------------------------------------------------------------
# Page-level extraction
# ---------------------------------------------------------------------------


def extract_target_spec(page, listing_url: str) -> ListingSpec:
    """Navigate to a listing page and extract specs."""
    page.goto(listing_url, wait_until="domcontentloaded")
    page.wait_for_timeout(800)

    # JSON-LD
    ld = None
    try:
        ld = page.evaluate(
            """() => {
              const scripts = Array.from(
                document.querySelectorAll('script[type="application/ld+json"]')
              );
              return scripts.map(s => s.textContent || '').filter(Boolean);
            }"""
        )
    except Exception:
        ld = None

    body_text = ""
    try:
        body_text = page.inner_text("body", timeout=8000)
    except Exception:
        body_text = ""

    title = ""
    location = ""
    accommodates = None
    bedrooms = None
    beds = None
    baths = None
    property_type = ""

    # Title
    try:
        title = _clean(page.locator("h1").first.inner_text(timeout=4000))
    except Exception:
        title = _clean((body_text.splitlines() or [""])[0])

    # Location heuristics
    top_slice = "\n".join(
        ln.strip() for ln in (body_text.splitlines()[:80]) if ln.strip()
    )
    location_keywords = [
        "United States", "Taiwan", "Canada", "日本", "台灣", "台湾",
        "France", "Deutschland", "UK", "United Kingdom", "Australia",
        "Korea", "韓國", "한국", "Hong Kong", "Singapore",
    ]
    for ln in top_slice.splitlines():
        if any(k in ln for k in location_keywords):
            if "," in ln and len(ln) <= 80:
                location = _clean(ln)
                break

    # Specs from body text
    accommodates = _extract_first_int(body_text, [_GUEST_RE])
    bedrooms = _extract_first_int(body_text, [_BEDROOM_RE])
    beds = _extract_first_int(body_text, [_BED_RE])
    baths = _extract_first_float(body_text, [_BATH_RE])

    # Property type
    for ln in top_slice.splitlines():
        if any(k in ln.lower() for k in ["entire", "private room", "shared room"]) or any(
            k in ln for k in ["整套", "獨立房間", "合住房間"]
        ):
            property_type = _clean(ln)
            break

    # Override from JSON-LD if available
    if isinstance(ld, list) and ld:
        for block in ld:
            try:
                obj = json.loads(block)
            except Exception:
                continue
            candidates = obj if isinstance(obj, list) else [obj]
            for it in candidates:
                if not isinstance(it, dict):
                    continue
                t = it.get("@type") or ""
                if t and any(
                    x in str(t)
                    for x in [
                        "LodgingBusiness", "Hotel", "Apartment",
                        "House", "Accommodation",
                    ]
                ):
                    title = title or _clean(str(it.get("name") or ""))
                    addr = it.get("address") or {}
                    if isinstance(addr, dict):
                        parts = [
                            str(addr[k])
                            for k in [
                                "addressLocality",
                                "addressRegion",
                                "addressCountry",
                            ]
                            if addr.get(k)
                        ]
                        if parts and not location:
                            location = ", ".join(parts)
                    break

    return ListingSpec(
        url=listing_url,
        title=title,
        location=location,
        accommodates=accommodates,
        bedrooms=bedrooms,
        beds=beds,
        baths=baths,
        property_type=property_type,
    )


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
    text = _clean(card.get("text") or "")
    price_text = _clean(card.get("price_text") or "")
    price = _parse_money_to_float(price_text)

    return ListingSpec(
        url=str(card.get("url") or ""),
        title=_clean(card.get("title") or ""),
        accommodates=_extract_first_int(text, [_GUEST_RE]),
        bedrooms=_extract_first_int(text, [_BEDROOM_RE]),
        beds=_extract_first_int(text, [_BED_RE]),
        baths=_extract_first_float(text, [_BATH_RE]),
        nightly_price=price,
        rating=card.get("rating"),
        reviews=card.get("reviews"),
    )


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------


def similarity_score(target: ListingSpec, cand: ListingSpec) -> float:
    score = 0.0
    weight_sum = 0.0

    def add_num(t, c, w: float, tol: float):
        nonlocal score, weight_sum
        weight_sum += w
        if t is None or c is None:
            score += 0.35 * w
            return
        diff = abs(float(t) - float(c))
        s = max(0.0, 1.0 - diff / tol)
        score += s * w

    add_num(target.accommodates, cand.accommodates, w=2.2, tol=3.0)
    add_num(target.bedrooms, cand.bedrooms, w=2.6, tol=2.0)
    add_num(target.beds, cand.beds, w=1.4, tol=3.0)
    add_num(target.baths, cand.baths, w=2.0, tol=1.5)

    if weight_sum <= 0:
        return 0.0
    return score / weight_sum


def recommend_price(
    target: ListingSpec,
    comps: List[ListingSpec],
    *,
    top_k: int = 15,
    new_listing_discount: float = 0.10,
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Pick top-K similar comps and compute a recommended nightly price."""
    comps = [c for c in comps if c.nightly_price and c.nightly_price > 0]
    if not comps:
        return None, {"reason": "No comparable prices collected."}

    ranked = sorted(
        comps, key=lambda c: similarity_score(target, c), reverse=True
    )
    picked = ranked[: max(3, top_k)]

    prices = [c.nightly_price for c in picked if c.nightly_price]
    weights = [
        max(0.05, similarity_score(target, c))
        for c in picked
        if c.nightly_price
    ]

    wm = _weighted_median(prices, weights)
    if wm is None:
        wm = statistics.median(prices) if prices else None
    if wm is None:
        return None, {"reason": "Failed to compute median."}

    rec = wm * (1.0 - max(0.0, min(0.35, new_listing_discount)))

    debug: Dict[str, Any] = {
        "picked_n": len(picked),
        "weighted_median": round(wm, 2),
        "discount_applied": new_listing_discount,
        "recommended_nightly": round(rec, 2),
        "p25": (
            round(statistics.quantiles(prices, n=4)[0], 2)
            if len(prices) >= 4
            else None
        ),
        "p75": (
            round(statistics.quantiles(prices, n=4)[2], 2)
            if len(prices) >= 4
            else None
        ),
        "min": round(min(prices), 2) if prices else None,
        "max": round(max(prices), 2) if prices else None,
    }
    return rec, debug


# ---------------------------------------------------------------------------
# Main scrape pipeline (connects to local Chrome via CDP)
# ---------------------------------------------------------------------------


def run_scrape(
    listing_url: str,
    checkin: str,
    checkout: str,
    cdp_url: str = "http://127.0.0.1:9222",
    adults: int = 2,
    top_k: int = 15,
    max_scroll_rounds: int = 12,
    max_cards: int = 80,
    max_runtime_seconds: int = 180,
    rate_limit_seconds: float = 1.0,
) -> Tuple[Optional[float], List[ListingSpec], Dict[str, Any]]:
    """
    Full scrape pipeline: extract target → search comps → rank → recommend.

    Returns (recommended_nightly, comps_list, debug_dict).
    Uses CDP to connect to a locally running Chrome with active Airbnb session.
    """
    from playwright.sync_api import sync_playwright

    start_time = time.time()
    base_origin = _safe_domain_base(listing_url)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        try:
            # Step 1: Extract target listing spec
            logger.info(f"Extracting target: {listing_url}")
            extract_start = time.time()
            target = extract_target_spec(page, listing_url)
            extract_ms = round((time.time() - extract_start) * 1000)

            if not target.location:
                tokens = [
                    t.strip()
                    for t in re.split(r"[-|•,]", target.title)
                    if t.strip()
                ]
                target.location = tokens[-1] if tokens else ""
                logger.warning(f"Location fallback from title: '{target.location}'")

            if not target.location:
                return None, [], {
                    "error": "Cannot determine location from listing page.",
                    "extract_target_ms": extract_ms,
                }

            # Step 2: Search nearby comparables
            search_url = build_search_url(
                base_origin, target.location, checkin, checkout, adults
            )
            logger.info(f"Search URL: {search_url}")

            # Rate limit before search page load
            time.sleep(rate_limit_seconds)

            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(900)

            # Dismiss modals
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

            # Check timeout before scrolling
            elapsed = time.time() - start_time
            remaining = max_runtime_seconds - elapsed
            if remaining < 10:
                return None, [], {
                    "error": "Timeout before scroll phase.",
                    "extract_target_ms": extract_ms,
                    "total_ms": round(elapsed * 1000),
                }

            scroll_start = time.time()
            raw_cards = scroll_and_collect(
                page,
                max_rounds=max_scroll_rounds,
                max_cards=max_cards,
                pause_ms=900,
                rate_limit_seconds=rate_limit_seconds,
            )
            scroll_ms = round((time.time() - scroll_start) * 1000)

            comps = [parse_card_to_spec(c) for c in raw_cards]
            comps = [c for c in comps if c.url and c.nightly_price]

            # Step 3: Rank & recommend
            comps_scored = [
                (c, similarity_score(target, c)) for c in comps
            ]
            comps_scored.sort(key=lambda x: x[1], reverse=True)

            recommended, rec_debug = recommend_price(
                target,
                [c for c, _ in comps_scored],
                top_k=top_k,
            )

            total_ms = round((time.time() - start_time) * 1000)

            debug = {
                "source": "scrape",
                "extract_target_ms": extract_ms,
                "scroll_ms": scroll_ms,
                "total_ms": total_ms,
                "comps_collected": len(comps),
                "target_location": target.location,
                "target_title": target.title,
                **rec_debug,
            }

            return recommended, comps, debug

        finally:
            try:
                page.close()
            except Exception:
                pass
