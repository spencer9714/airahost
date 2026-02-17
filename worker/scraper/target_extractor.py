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

    # Property type
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
