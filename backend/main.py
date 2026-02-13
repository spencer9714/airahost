"""
AiraHost Pricing API — FastAPI service wrapping Airbnb comparable-listing scraper.

Endpoints:
  POST /api/v1/estimate  — scrape target + nearby listings, return pricing recommendation.
  GET  /health           — liveness check.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import traceback
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("airahost")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="AiraHost Pricing API", version="1.0.0")

# CORS — allow Vercel frontend + local dev
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://airahost.vercel.app,http://localhost:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request / Response schemas (Pydantic)
# ---------------------------------------------------------------------------


class EstimateRequest(BaseModel):
    listing_url: str = Field(..., description="Full Airbnb listing URL")
    checkin: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")
    checkout: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")
    adults: int = Field(default=2, ge=1, le=16)
    top_k: int = Field(default=15, ge=3, le=50)
    max_scroll_rounds: int = Field(default=12, ge=1, le=30)
    new_listing_discount: float = Field(
        default=0.10, ge=0.0, le=0.35,
        description="Discount applied to weighted median (0.10 = 10%)",
    )
    location: Optional[str] = Field(
        default=None,
        description="Override auto-detected location, e.g. 'Redwood City, CA'",
    )


class ListingSpecOut(BaseModel):
    url: str
    title: str
    location: str
    accommodates: Optional[int] = None
    bedrooms: Optional[int] = None
    beds: Optional[int] = None
    baths: Optional[float] = None
    property_type: str = ""
    nightly_price: Optional[float] = None
    currency: str = "USD"
    rating: Optional[float] = None
    reviews: Optional[int] = None
    similarity: Optional[float] = None


class DiscountSuggestion(BaseModel):
    weekly_discount_pct: float = Field(description="Suggested 7-night discount %")
    monthly_discount_pct: float = Field(description="Suggested 28-night discount %")
    non_refundable_discount_pct: float = Field(
        description="Suggested non-refundable discount %"
    )
    weekly_nightly: Optional[float] = None
    monthly_nightly: Optional[float] = None
    non_refundable_nightly: Optional[float] = None


class RecommendationStats(BaseModel):
    picked_n: int
    weighted_median: Optional[float] = None
    discount_applied: float
    recommended_nightly: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None


class EstimateResponse(BaseModel):
    target: ListingSpecOut
    comparables: List[ListingSpecOut]
    recommendation: RecommendationStats
    discount_suggestions: DiscountSuggestion
    total_comparables_found: int


# ---------------------------------------------------------------------------
# Data structures (internal)
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
# Scraping logic (Playwright, headless)
# ---------------------------------------------------------------------------


def extract_target_spec(page, listing_url: str) -> ListingSpec:
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
    page, max_rounds: int = 12, pause_ms: int = 900
) -> List[Dict[str, Any]]:
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
        if no_new >= 3:
            break

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
# Similarity & recommendation
# ---------------------------------------------------------------------------


def similarity_score(target: ListingSpec, cand: ListingSpec) -> float:
    score = 0.0
    weight_sum = 0.0

    def add_num(
        t: Optional[float], c: Optional[float], w: float, tol: float
    ):
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
    top_k: int,
    new_listing_discount: float,
) -> Tuple[Optional[float], Dict[str, Any]]:
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
# Discount suggestions
# ---------------------------------------------------------------------------


def compute_discount_suggestions(
    recommended_nightly: Optional[float],
) -> DiscountSuggestion:
    """
    Generate sensible discount suggestions for:
      - Weekly stays (7+ nights): typically 5–10 %
      - Monthly stays (28+ nights): typically 15–25 %
      - Non-refundable bookings: typically 8–12 %
    """
    weekly_pct = 8.0
    monthly_pct = 18.0
    non_refundable_pct = 10.0

    weekly_nightly = None
    monthly_nightly = None
    non_refundable_nightly = None

    if recommended_nightly is not None:
        weekly_nightly = round(recommended_nightly * (1 - weekly_pct / 100), 2)
        monthly_nightly = round(
            recommended_nightly * (1 - monthly_pct / 100), 2
        )
        non_refundable_nightly = round(
            recommended_nightly * (1 - non_refundable_pct / 100), 2
        )

    return DiscountSuggestion(
        weekly_discount_pct=weekly_pct,
        monthly_discount_pct=monthly_pct,
        non_refundable_discount_pct=non_refundable_pct,
        weekly_nightly=weekly_nightly,
        monthly_nightly=monthly_nightly,
        non_refundable_nightly=non_refundable_nightly,
    )


# ---------------------------------------------------------------------------
# Core estimation pipeline
# ---------------------------------------------------------------------------


def run_estimate(req: EstimateRequest) -> EstimateResponse:
    from playwright.sync_api import sync_playwright

    listing_url = req.listing_url.strip()
    base_origin = _safe_domain_base(listing_url)

    with sync_playwright() as p:
        # Headless Chromium — no CDP, no local Chrome dependency
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        # Apply stealth patches
        try:
            from playwright_stealth import stealth_sync  # type: ignore[import-untyped]
            stealth_sync(context)
        except ImportError:
            logger.warning(
                "playwright-stealth not installed; skipping stealth patches"
            )

        page = context.new_page()

        try:
            # ---- Step 1: Extract target listing spec ----
            logger.info(f"Opening target listing: {listing_url}")
            target = extract_target_spec(page, listing_url)

            if req.location:
                target.location = req.location.strip()

            if not target.location:
                tokens = [
                    t.strip()
                    for t in re.split(r"[-|•,]", target.title)
                    if t.strip()
                ]
                target.location = tokens[-1] if tokens else ""
                logger.warning(
                    f"Location fallback from title: '{target.location}'"
                )

            if not target.location:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Cannot determine location from listing page. "
                        "Please provide 'location' parameter, e.g. 'Redwood City, CA'."
                    ),
                )

            # ---- Step 2: Search nearby comparables ----
            search_url = build_search_url(
                base_origin,
                target.location,
                req.checkin,
                req.checkout,
                req.adults,
            )
            logger.info(f"Search URL: {search_url}")

            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(900)

            # Dismiss modals
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

            logger.info("Scrolling and collecting comparable cards...")
            raw_cards = scroll_and_collect(
                page,
                max_rounds=req.max_scroll_rounds,
                pause_ms=900,
            )

            comps = [parse_card_to_spec(c) for c in raw_cards]
            comps = [c for c in comps if c.url and c.nightly_price]

            if len(comps) == 0:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "No comparable listings with prices found. "
                        "Try adjusting dates, adults, or provide a more specific location."
                    ),
                )

            # ---- Step 3: Rank & recommend ----
            comps_scored = [
                (c, similarity_score(target, c)) for c in comps
            ]
            comps_scored.sort(key=lambda x: x[1], reverse=True)

            recommended, debug = recommend_price(
                target,
                [c for c, _ in comps_scored],
                top_k=req.top_k,
                new_listing_discount=req.new_listing_discount,
            )

            logger.info(
                f"Result: location={target.location}, "
                f"comps={len(comps_scored)}, "
                f"recommended={recommended}"
            )

            # ---- Step 4: Discount suggestions ----
            discount_suggestions = compute_discount_suggestions(recommended)

            # ---- Build response ----
            top_comps = comps_scored[: req.top_k]

            return EstimateResponse(
                target=ListingSpecOut(
                    url=target.url,
                    title=target.title,
                    location=target.location,
                    accommodates=target.accommodates,
                    bedrooms=target.bedrooms,
                    beds=target.beds,
                    baths=target.baths,
                    property_type=target.property_type,
                    nightly_price=target.nightly_price,
                    currency=target.currency,
                    rating=target.rating,
                    reviews=target.reviews,
                ),
                comparables=[
                    ListingSpecOut(
                        url=c.url,
                        title=c.title,
                        location=c.location,
                        accommodates=c.accommodates,
                        bedrooms=c.bedrooms,
                        beds=c.beds,
                        baths=c.baths,
                        property_type=c.property_type,
                        nightly_price=c.nightly_price,
                        currency=c.currency,
                        rating=c.rating,
                        reviews=c.reviews,
                        similarity=round(sim, 4),
                    )
                    for c, sim in top_comps
                ],
                recommendation=RecommendationStats(
                    picked_n=debug.get("picked_n", 0),
                    weighted_median=debug.get("weighted_median"),
                    discount_applied=debug.get("discount_applied", 0),
                    recommended_nightly=debug.get("recommended_nightly"),
                    p25=debug.get("p25"),
                    p75=debug.get("p75"),
                    min=debug.get("min"),
                    max=debug.get("max"),
                ),
                discount_suggestions=discount_suggestions,
                total_comparables_found=len(comps_scored),
            )

        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok", "service": "airahost-pricing"}


@app.post("/api/v1/estimate", response_model=EstimateResponse)
def estimate(req: EstimateRequest):
    """
    Scrape the target Airbnb listing and nearby comparables,
    then return a pricing recommendation with discount suggestions.
    """
    try:
        return run_estimate(req)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Estimate failed: {exc}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Scraping failed: {str(exc)}",
        )
