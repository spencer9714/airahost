"""
Target listing spec extraction.

Navigates to an Airbnb listing page via Playwright and extracts
structured property specs (title, location, capacity, amenities, etc.).

Extracted from price_estimator.py for modularity.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger("worker.scraper.target_extractor")

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
    amenities: List[str] = field(default_factory=list)
    # Number of nights the scraped price covered before per-night normalization.
    # 1 = normal 1-night search card; 2 = "for 2 nights" trip total, divided by 2; etc.
    scrape_nights: int = 1
    # Approximate WGS-84 coordinates (optional — best-effort from geocoding or page state).
    lat: Optional[float] = None
    lng: Optional[float] = None
    # Distance from the target listing (set by geo_filter, None if coords unavailable).
    distance_to_target_km: Optional[float] = None


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

MONEY_RE = re.compile(
    r"(?<!\w)(?:US)?\s?\$?\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?(?!\w)"
)
BEDROOM_RE = re.compile(r"(\d+)\s*(?:bedroom|bedrooms|間臥室|卧室)", re.I)
BED_RE = re.compile(r"(\d+)\s*(?:bed|beds|張床|床)", re.I)
BATH_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:bath|baths|衛浴|浴室|衛生間|卫生间)", re.I
)
GUEST_RE = re.compile(r"(\d+)\s*(?:guest|guests|位|人)", re.I)
PROPERTY_TYPE_HINTS = {
    "entire_home": [
        "entire home",
        "entire place",
        "entire rental unit",
        "entire condo",
        "entire townhouse",
    ],
    "private_room": ["private room"],
    "shared_room": ["shared room"],
}
AMENITY_HINTS = {
    "wifi": ["wifi", "wi-fi"],
    "kitchen": ["kitchen"],
    "washer": ["washer", "washing machine", "laundry"],
    "dryer": ["dryer"],
    "ac": ["air conditioning", "a/c", "ac"],
    "heating": ["heating", "heater"],
    "pool": ["pool"],
    "hot_tub": ["hot tub", "jacuzzi"],
    "free_parking": ["free parking", "parking on premises"],
    "gym": ["gym", "fitness"],
    "bbq": ["bbq", "barbecue", "grill"],
    "fire_pit": ["fire pit"],
    "pets_allowed": ["pets allowed", "pet-friendly", "pet friendly"],
}


def clean(s: str) -> str:
    return (s or "").replace("\u00a0", " ").strip()


def to_int(x: str) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def to_float(x: str) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def extract_first_int(text: str, patterns: list) -> Optional[int]:
    for p in patterns:
        m = p.search(text or "")
        if m:
            return to_int(m.group(1))
    return None


def extract_first_float(text: str, patterns: list) -> Optional[float]:
    for p in patterns:
        m = p.search(text or "")
        if m:
            return to_float(m.group(1))
    return None


def parse_money_to_float(text: str) -> Optional[float]:
    if not text:
        return None
    m = MONEY_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def normalize_property_type(text: str) -> str:
    t = (text or "").lower()
    for key, hints in PROPERTY_TYPE_HINTS.items():
        if any(h in t for h in hints):
            return key
    return ""


def extract_amenities(text: str) -> List[str]:
    t = (text or "").lower()
    out: List[str] = []
    for code, hints in AMENITY_HINTS.items():
        if any(h in t for h in hints):
            out.append(code)
    return out


# ---------------------------------------------------------------------------
# CDP / URL helpers
# ---------------------------------------------------------------------------


def check_cdp_endpoint(cdp_url: str, timeout_seconds: float = 2.0) -> Tuple[bool, str]:
    probe = cdp_url.rstrip("/") + "/json/version"
    try:
        req = Request(probe, headers={"User-Agent": "AiraHost-Worker/1.0"})
        with urlopen(req, timeout=timeout_seconds) as r:
            if r.status == 200:
                return True, "ok"
            return False, f"HTTP {r.status}"
    except Exception as exc:
        return False, str(exc)


def safe_domain_base(url: str) -> str:
    p = urlparse(url)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return "https://www.airbnb.com"


# ---------------------------------------------------------------------------
# Page-level extraction
# ---------------------------------------------------------------------------


def extract_target_spec(page, listing_url: str) -> Tuple[ListingSpec, List[str]]:
    """
    Navigate to a listing page and extract specs.

    Returns (ListingSpec, extraction_warnings).
    """
    warnings: List[str] = []

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
        warnings.append("Failed to extract JSON-LD data")

    body_text = ""
    try:
        body_text = page.inner_text("body", timeout=8000)
    except Exception:
        body_text = ""
        warnings.append("Failed to extract body text")

    title = ""
    location = ""
    accommodates = None
    bedrooms = None
    beds = None
    baths = None
    property_type = ""

    # Title
    try:
        title = clean(page.locator("h1").first.inner_text(timeout=4000))
    except Exception:
        title = clean((body_text.splitlines() or [""])[0])
        warnings.append("Title extracted from body text fallback")

    # ── Location extraction (multi-strategy) ─────────────────────
    #
    # Airbnb listing pages show location in several places.  We try
    # multiple strategies in order of reliability.

    # Patterns matching "Entire home in City, State" / "整套房源位於 台北"
    _LOC_SUBTITLE_RE = [
        re.compile(
            r"(?:Entire\s+\w+|Private\s+room|Shared\s+room|Room|Hotel\s+room)"
            r"\s+in\s+(.+)",
            re.I,
        ),
        re.compile(
            r"(?:整套|獨立房間|合住房間|房間|飯店房間)\s*[·位於在]+\s*(.+)",
        ),
    ]

    def _clean_loc(raw: str) -> str:
        """Strip trailing noise like ★4.95 · 2 guests."""
        return re.split(r"\s*[·•★]", raw)[0].strip()

    # Strategy 1: DOM — subtitle near <h1> + breadcrumbs + meta tags
    dom_hints: List[str] = []
    try:
        dom_hints = page.evaluate(
            """() => {
              const out = [];
              // 1a) Elements near <h1> that describe location
              const h1 = document.querySelector('h1');
              if (h1) {
                let el = h1.nextElementSibling || h1.parentElement?.nextElementSibling;
                for (let i = 0; i < 6 && el; i++) {
                  const t = (el.innerText || '').trim().split('\\n')[0];
                  if (t && t.length > 3 && t.length < 200) out.push(t);
                  el = el.nextElementSibling;
                }
                // Also check the parent container's children
                const parent = h1.closest('section') || h1.closest('div[data-section-id]') || h1.parentElement;
                if (parent) {
                  for (const child of parent.querySelectorAll('span, div, h2, h3')) {
                    const t = (child.innerText || '').trim().split('\\n')[0];
                    if (t && t.length > 5 && t.length < 150 &&
                        (t.toLowerCase().includes(' in ') || t.includes('位於') || t.includes('·'))) {
                      out.push(t);
                    }
                  }
                }
              }
              // 1b) Breadcrumb navigation
              const bcLinks = document.querySelectorAll(
                'nav[aria-label*="readcrumb"] a, nav[aria-label*="breadcrumb"] a, ' +
                'ol[role="list"] a[href*="/s/"], nav a[href*="/s/"]'
              );
              if (bcLinks.length > 0) {
                const parts = Array.from(bcLinks)
                  .map(a => (a.innerText || '').trim())
                  .filter(t => t && t.length > 1 &&
                    !t.toLowerCase().includes('airbnb') &&
                    t.toLowerCase() !== 'home');
                if (parts.length > 0) out.push('BC:' + parts.join(', '));
              }
              // 1c) Meta og:title
              const og = document.querySelector('meta[property="og:title"]');
              if (og) out.push('META:' + (og.getAttribute('content') || ''));
              // 1d) Document title
              if (document.title) out.push('TITLE:' + document.title);
              return out;
            }"""
        )
    except Exception:
        dom_hints = []
        warnings.append("Failed to extract DOM hints for location")

    # 1a) Subtitle — "Entire home in City, State, Country"
    for hint in dom_hints:
        if hint.startswith(("BC:", "META:", "TITLE:")):
            continue
        for pat in _LOC_SUBTITLE_RE:
            m = pat.search(hint)
            if m:
                loc = _clean_loc(clean(m.group(1)))
                if loc and len(loc) >= 3:
                    location = loc
                    break
        if location:
            break

    # 1b) Breadcrumbs — "Country, Region, City" → use last 2 parts
    if not location:
        for hint in dom_hints:
            if hint.startswith("BC:"):
                bc_parts = [p.strip() for p in hint[3:].split(",") if p.strip()]
                if len(bc_parts) >= 2:
                    location = ", ".join(bc_parts[-2:])
                elif bc_parts:
                    location = bc_parts[-1]
                break

    # 1c/1d) Meta / title — "Name - vacation rental in City, State"
    if not location:
        for hint in dom_hints:
            if not hint.startswith(("META:", "TITLE:")):
                continue
            text = hint.split(":", 1)[1].strip()
            loc_m = re.search(
                r"(?:rental|home|apartment|place|room|stay|cabin|villa|condo)"
                r"\s+in\s+([^·|]+)",
                text, re.I,
            )
            if loc_m:
                loc = _clean_loc(clean(loc_m.group(1)))
                if loc and len(loc) >= 3:
                    location = loc
                    break
            # Fallback: split by · and find a part with commas (looks like "City, State")
            for part in re.split(r"\s*[·|]\s*", text):
                part = part.strip()
                if ("," in part and 5 <= len(part) <= 80
                        and not re.search(r"^\d|★|Airbnb|review", part, re.I)):
                    location = part
                    break
            if location:
                break

    # Strategy 2: Body-text scan — look for "in City, State" near top
    if not location:
        top_lines = [
            ln.strip() for ln in (body_text.splitlines()[:80]) if ln.strip()
        ]
        for ln in top_lines:
            for pat in _LOC_SUBTITLE_RE:
                m = pat.search(ln)
                if m:
                    loc = _clean_loc(clean(m.group(1)))
                    if loc and len(loc) >= 3:
                        location = loc
                        break
            if location:
                break

    if location:
        logger.info(f"Extracted location: '{location}'")
    else:
        warnings.append("Could not extract location from listing page")

    # Specs from body text
    accommodates = extract_first_int(body_text, [GUEST_RE])
    bedrooms = extract_first_int(body_text, [BEDROOM_RE])
    beds = extract_first_int(body_text, [BED_RE])
    baths = extract_first_float(body_text, [BATH_RE])

    if accommodates is None:
        warnings.append("Could not extract guest capacity")
    if bedrooms is None:
        warnings.append("Could not extract bedroom count")

    # Property type — scan top ~80 lines of body text
    top_slice = "\n".join(
        ln.strip() for ln in (body_text.splitlines()[:80]) if ln.strip()
    )
    for ln in top_slice.splitlines():
        if any(k in ln.lower() for k in ["entire", "private room", "shared room"]) or any(
            k in ln for k in ["整套", "獨立房間", "合住房間"]
        ):
            property_type = clean(ln)
            break

    # Rating / reviews
    rating = None
    reviews = None
    rate_match = re.search(
        r"(\d\.\d\d|\d\.\d)\s*(?:\(|·|・)?\s*(\d+)\s*review",
        body_text[:3000],
        re.I,
    )
    if rate_match:
        try:
            r = float(rate_match.group(1))
            if 2.5 <= r <= 5.0:
                rating = r
            n = int(rate_match.group(2))
            reviews = n
        except Exception:
            pass

    # Enrich from JSON-LD if available
    if isinstance(ld, list) and ld:
        for block in ld:
            try:
                obj = json.loads(block)
            except Exception:
                continue
            ld_items = obj if isinstance(obj, list) else [obj]
            for it in ld_items:
                if not isinstance(it, dict):
                    continue
                t = it.get("@type") or ""
                if t and any(
                    x in str(t)
                    for x in [
                        "LodgingBusiness", "Hotel", "Apartment",
                        "House", "Accommodation", "VacationRental",
                        "SingleFamilyResidence", "Residence",
                    ]
                ):
                    title = title or clean(str(it.get("name") or ""))
                    addr = it.get("address") or {}
                    if isinstance(addr, dict):
                        # Build location from most specific to least
                        locality = str(addr.get("addressLocality") or "").strip()
                        region = str(addr.get("addressRegion") or "").strip()
                        country = str(addr.get("addressCountry") or "").strip()
                        # Prefer "City, State" over broader heuristic matches
                        ld_parts = [p for p in [locality, region, country] if p]
                        if ld_parts:
                            ld_location = ", ".join(ld_parts)
                            if not location or len(locality) > 0:
                                location = ld_location
                                logger.info(f"Location from JSON-LD: '{location}'")
                    # aggregateRating
                    agg = it.get("aggregateRating") or {}
                    if isinstance(agg, dict):
                        if rating is None and agg.get("ratingValue"):
                            try:
                                rating = float(agg["ratingValue"])
                            except Exception:
                                pass
                        if reviews is None and agg.get("reviewCount"):
                            try:
                                reviews = int(agg["reviewCount"])
                            except Exception:
                                pass
                    break

    amenities = extract_amenities(body_text)

    spec = ListingSpec(
        url=listing_url,
        title=title,
        location=location,
        accommodates=accommodates,
        bedrooms=bedrooms,
        beds=beds,
        baths=baths,
        property_type=normalize_property_type(property_type or body_text),
        amenities=amenities,
        rating=rating,
        reviews=reviews,
    )

    return spec, warnings


def extract_listing_page_title(page, listing_url: str) -> Tuple[str, List[str]]:
    """
    Navigate to an Airbnb listing page and extract the canonical listing title.

    Returns (title, warnings). This is lighter-weight than extract_target_spec
    and is intended for post-processing comparable listings whose search-card
    title looks suspicious.
    """
    warnings: List[str] = []
    title = ""

    try:
        page.goto(listing_url, wait_until="domcontentloaded")
        page.wait_for_timeout(600)
    except Exception as exc:
        warnings.append(f"Failed to open listing page: {exc}")
        return "", warnings

    try:
        title = clean(page.locator("h1").first.inner_text(timeout=3000))
    except Exception:
        title = ""

    if title:
        return title, warnings

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
        ld = []

    if isinstance(ld, list):
        for block in ld:
            try:
                obj = json.loads(block)
            except Exception:
                continue
            items = obj if isinstance(obj, list) else [obj]
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = clean(str(it.get("name") or ""))
                if name:
                    return name, warnings

    try:
        body_text = page.inner_text("body", timeout=5000)
        for line in body_text.splitlines():
            line = clean(line)
            if len(line) >= 8:
                warnings.append("Title extracted from body text fallback")
                return line, warnings
    except Exception:
        pass

    warnings.append("Could not extract listing page title")
    return "", warnings


# ---------------------------------------------------------------------------
# Benchmark price extraction — direct listing page visit with dates
# ---------------------------------------------------------------------------

_NIGHTLY_PRICE_RES = [
    re.compile(r"\$\s*(\d{1,4}(?:,\d{3})?)\s*/\s*night", re.I),
    re.compile(r"\$\s*(\d{1,4}(?:,\d{3})?)\s+per\s+night", re.I),
    re.compile(r"(\d{1,4}(?:,\d{3})?)\s+USD\s*/\s*night", re.I),
]

# JavaScript run inside the browser to classify price candidates in the booking widget.
# For each price element found near a "/night" label it records:
#   value       — numeric price
#   strikethrough — True if the element (or an ancestor ≤4 levels) has
#                   CSS text-decoration:line-through or is a <s>/<del> tag
#   domIndex    — position in the widget's element list (document order)
#
# The caller (Python) then picks the last non-strikethrough candidate, which is
# the current/discounted nightly price rather than the crossed-out original.
_BOOKING_WIDGET_PRICE_JS = """
() => {
  // Returns true if el or any ancestor up to 4 levels has line-through styling.
  function isLineThrough(el) {
    let node = el;
    for (let i = 0; i < 4 && node && node !== document.body; i++) {
      const s = window.getComputedStyle(node);
      const td = (s.textDecoration || '') + (s.textDecorationLine || '');
      if (td.includes('line-through')) return true;
      if (node.tagName === 'S' || node.tagName === 'DEL') return true;
      node = node.parentElement;
    }
    return false;
  }

  // Matches a bare dollar amount with no surrounding text: "$465", "$1,234"
  const BARE_PRICE_RE = /^\\$?\\s*(\\d{1,4}(?:,\\d{3})*)\\s*$/;
  // Matches "$X /night" or "$X per night" somewhere in a string
  const PRICE_NIGHT_RE = /\\$\\s*(\\d{1,4}(?:,\\d{3})?)\\s*(?:\\/\\s*night|per\\s+night)/i;

  // Find the booking widget container (most-to-least specific).
  const WIDGET_SELS = [
    '[data-testid="book-it-default"]',
    '[data-testid="price-block"]',
    '[data-testid="book-it-price-breakdown"]',
  ];
  let widget = null;
  for (const s of WIDGET_SELS) {
    widget = document.querySelector(s);
    if (widget) break;
  }
  if (!widget) return { candidates: [], hasWidget: false, widgetText: '' };

  // All inline elements in the widget, in document order.
  const allEls = Array.from(widget.querySelectorAll('span, b, strong, em'));

  // Find the smallest elements whose innerText matches the "$X /night" compound
  // pattern — these are the price+night containers (e.g. the header span that
  // wraps both the price number and the "/night" label).
  const nightContainers = [];
  for (const el of allEls) {
    const t = (el.innerText || '').trim();
    if (PRICE_NIGHT_RE.test(t) && t.length < 200) {
      nightContainers.push(el);
    }
  }
  // Smallest first → most specific container processed first.
  nightContainers.sort((a, b) =>
    (a.innerText || '').length - (b.innerText || '').length
  );

  const candidates = [];
  const seenEls = new Set();

  for (const container of nightContainers.slice(0, 3)) {
    const priceEls = container.querySelectorAll('span, b, strong, em');
    for (const el of priceEls) {
      if (seenEls.has(el)) continue;
      // Skip elements that contain block children (they are containers, not leaves).
      if (el.querySelectorAll('div, p, h1, h2, h3').length > 0) continue;
      const text = (el.textContent || '').trim();
      const m = text.match(BARE_PRICE_RE);
      if (!m) continue;
      const value = parseFloat(m[1].replace(',', ''));
      if (isNaN(value) || value < 10 || value > 15000) continue;
      seenEls.add(el);
      candidates.push({
        value,
        strikethrough: isLineThrough(el),
        domIndex: allEls.indexOf(el),
      });
    }
    if (candidates.length > 0) break; // stop at the most-specific container
  }

  return {
    candidates,
    hasWidget: true,
    widgetText: (widget.innerText || '').substring(0, 600),
  };
}
"""


def select_nightly_price_from_candidates(
    candidates: List[Dict[str, Any]],
) -> Optional[Tuple[float, str]]:
    """
    Select the current nightly price from structured DOM price candidates.

    Rules
    -----
    * Non-strikethrough candidates are the current/discounted price.
    * Strikethrough candidates are the original/crossed-out price — excluded.
    * When multiple non-strikethrough candidates exist, pick the **last** one
      in DOM order: Airbnb always places the original price before the discounted
      price in the DOM, so the last non-strikethrough entry is the discounted one.
    * Returns (price, price_kind) or None if no valid candidate exists.
      price_kind: "nightly_discounted" if strikethrough originals are also present,
                  "nightly_standard" otherwise.
    """
    if not candidates:
        return None

    non_st = [c for c in candidates if not c.get("strikethrough")]
    has_strikethrough = any(c.get("strikethrough") for c in candidates)

    if not non_st:
        return None

    # Sort by domIndex (document order); take the last (= discounted price when
    # a discount is active, or the sole price when there is no discount).
    non_st_sorted = sorted(non_st, key=lambda c: c.get("domIndex", 0))
    best = non_st_sorted[-1]
    price_val = float(best["value"])

    if not (10 <= price_val <= 10000):
        return None

    price_kind = "nightly_discounted" if has_strikethrough else "nightly_standard"
    return price_val, price_kind


def extract_nightly_price_from_listing_page(
    page,
    listing_url: str,
    checkin: str,
    checkout: str,
) -> Tuple[Optional[float], str]:
    """
    Navigate to a listing page with check-in/check-out dates appended and
    extract the displayed nightly price from the booking widget.

    Returns (nightly_price, confidence) where confidence is one of:
      "high"   — price found in ld+json structured data
      "medium" — price found via DOM data-testid selectors
      "low"    — price found via body-text regex
      "failed" — price could not be extracted

    Extraction layers run in confidence order and stop at first success.
    One retry is attempted on navigation failure before giving up.
    """
    parsed = urlparse(listing_url)
    # Reconstruct with only check_in / check_out — drop any pre-existing params.
    url_with_dates = (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        f"?check_in={checkin}&check_out={checkout}&adults=2"
    )

    # Navigate with one retry on transient failure
    for attempt in range(2):
        try:
            page.goto(url_with_dates, wait_until="domcontentloaded", timeout=15000)
            break
        except Exception as exc:
            if attempt == 1:
                logger.warning(
                    f"[benchmark] Failed to navigate to benchmark page after retry: {exc}"
                )
                return None, "failed"
            logger.warning(
                f"[benchmark] Navigation attempt {attempt + 1} failed, retrying in 2s: {exc}"
            )
            time.sleep(2.0)

    # Wait for the booking widget to render (React hydration happens after domcontentloaded).
    # L2 DOM selectors are client-rendered; the ld+json (L1) and body text (L3) are server-
    # rendered and available immediately, but the widget is the most reliable price source.
    # If the widget doesn't appear within 6s, proceed anyway — L1/L3 may still succeed.
    try:
        page.wait_for_selector(
            '[data-testid="book-it-default"], [data-testid="price-block"]',
            timeout=6000,
        )
    except Exception:
        page.wait_for_timeout(800)  # short fixed wait as last resort before extraction

    # ── Layer 1: ld+json structured data (high confidence) ────────────────
    # Airbnb's LodgingBusiness schema includes rating and address but deliberately
    # omits per-night pricing. This layer is a forward-looking check: if Airbnb
    # ever adds priceSpecification to their ld+json, we capture it at high confidence.
    # In current practice this layer almost always falls through to L2.
    try:
        ld_texts: List[str] = page.evaluate(
            """() => Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                         .map(s => s.textContent || '')
                         .filter(Boolean)"""
        )
        for blob in (ld_texts or []):
            try:
                obj = json.loads(blob)
                items = obj if isinstance(obj, list) else [obj]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    ld_candidates = item.get("priceSpecification") or item.get("offers") or []
                    if isinstance(ld_candidates, dict):
                        ld_candidates = [ld_candidates]
                    for spec in (ld_candidates if isinstance(ld_candidates, list) else []):
                        if not isinstance(spec, dict):
                            continue
                        for key in ("price", "lowPrice", "minPrice"):
                            raw = spec.get(key)
                            if raw is None:
                                continue
                            try:
                                price = float(str(raw).replace(",", ""))
                                if 10 <= price <= 10000:
                                    logger.info(
                                        f"[benchmark] ld+json price=${price} "
                                        f"from {listing_url}"
                                    )
                                    return price, "high"
                                else:
                                    logger.warning(
                                        f"[benchmark] ld+json price ${price} out of range "
                                        f"(raw={raw!r}), skipping"
                                    )
                            except Exception:
                                continue
            except Exception:
                continue
    except Exception:
        pass

    # ── Layer 2: DOM price-candidate classification (medium confidence) ────
    # Runs _BOOKING_WIDGET_PRICE_JS to find price elements near the "/night"
    # label and classify each as strikethrough (original/crossed-out) or
    # current (discounted or standard).  select_nightly_price_from_candidates()
    # then picks the last non-strikethrough price in DOM order, which is the
    # discounted price when a discount is active.
    try:
        dom_result: Dict[str, Any] = page.evaluate(_BOOKING_WIDGET_PRICE_JS)
        if isinstance(dom_result, dict):
            dom_candidates: List[Dict[str, Any]] = dom_result.get("candidates") or []
            widget_text: str = dom_result.get("widgetText") or ""
            has_widget: bool = bool(dom_result.get("hasWidget"))

            logger.debug(
                f"[benchmark] DOM candidates (n={len(dom_candidates)}): {dom_candidates}"
            )

            selected = select_nightly_price_from_candidates(dom_candidates)
            if selected is not None:
                price, price_kind = selected
                non_st_count = sum(1 for c in dom_candidates if not c.get("strikethrough"))
                st_count = sum(1 for c in dom_candidates if c.get("strikethrough"))
                logger.info(
                    f"[benchmark] DOM price=${price} kind={price_kind} "
                    f"(non_strikethrough={non_st_count}, strikethrough={st_count}) "
                    f"from {listing_url}"
                )
                return price, "medium"

            # Structured scan found nothing — fall back to regex on the widget text.
            # When multiple prices are present prefer the LAST match (the discounted
            # price follows the original in the DOM / text order).
            if widget_text:
                text_matches: List[Tuple[int, float]] = []
                for pat in _NIGHTLY_PRICE_RES:
                    for m in pat.finditer(widget_text):
                        try:
                            p = float(m.group(1).replace(",", ""))
                            if 10 <= p <= 10000:
                                text_matches.append((m.start(), p))
                        except Exception:
                            continue
                if text_matches:
                    text_matches.sort(key=lambda x: x[0])
                    price = text_matches[-1][1]  # last = discounted if discount present
                    logger.info(
                        f"[benchmark] DOM widget-text fallback price=${price} "
                        f"(last of {len(text_matches)} matches) from {listing_url}"
                    )
                    return price, "medium"

            if not has_widget:
                logger.debug(
                    f"[benchmark] No booking widget found via data-testid, "
                    "falling through to body-text layer"
                )
    except Exception as exc:
        logger.debug(f"[benchmark] Layer 2 JS evaluation failed: {exc}")

    # ── Layer 3: body-text regex (low confidence) ──────────────────────────
    # Most fragile — depends on rendered text order, but catches edge cases
    # where booking widget data-testid attributes are absent.
    try:
        body = page.inner_text("body", timeout=8000)
    except Exception:
        logger.warning("[benchmark] Failed to read body text from listing page")
        return None, "failed"

    if not body:
        return None, "failed"

    # Scan the first 8 000 characters where the booking widget typically renders.
    # Collect ALL matches then pick the LAST one that appears before any
    # "Show price breakdown" section — the discounted price always follows the
    # original in text order, so the last pre-breakdown match is the current price.
    search_area = body[:8000]

    breakdown_pos = search_area.lower().find("show price breakdown")
    cutoff = breakdown_pos if breakdown_pos > 0 else len(search_area)

    body_matches: List[Tuple[int, float]] = []
    for pat in _NIGHTLY_PRICE_RES:
        for m in pat.finditer(search_area):
            try:
                price = float(m.group(1).replace(",", ""))
                if 10 <= price <= 10000:
                    body_matches.append((m.start(), price))
                else:
                    logger.warning(
                        f"[benchmark] Body-text price ${price} out of range "
                        f"(raw='{m.group(0)}'), skipping"
                    )
            except Exception:
                continue

    if body_matches:
        body_matches.sort(key=lambda x: x[0])
        pre_breakdown = [(pos, p) for pos, p in body_matches if pos < cutoff]
        chosen_pos, price = (pre_breakdown[-1] if pre_breakdown else body_matches[-1])
        logger.info(
            f"[benchmark] Body-text price=${price} "
            f"(last of {len(body_matches)} matches, pre_breakdown={len(pre_breakdown)}) "
            f"from {listing_url}"
        )
        return price, "low"

    logger.warning(
        f"[benchmark] Could not extract nightly price from listing page: {listing_url}"
    )
    return None, "failed"
