"""
Comparable listing collection from Airbnb search results.

Builds search URLs, scrolls search pages, extracts listing cards,
and parses them into ListingSpec objects for similarity comparison.

Extracted from price_estimator.py for modularity.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
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


def collect_search_cards(page, stay_nights: int = 1) -> List[Dict[str, Any]]:
    """Extract listing card data from a search results page.

    Price extraction uses a two-layer strategy:
      A) aria-label  — authoritative accessibility text, discount-aware
         (Airbnb puts the current/discounted price first, "Originally $X" after)
      B) DOM scan    — discount-aware; distinguishes strikethrough (original)
         from visible (current) prices; fails safe when ambiguous
    """
    return page.evaluate(
        r"""(stayNights) => {
          // ── Money parser ─────────────────────────────────────────────────
          // Handles: $120, $120.50, US$120, CA$175, AU$80, NZ$90
          // Strips whitespace, currency prefix, and thousands commas.
          function parseMoneyValue(text) {
            if (!text) return null;
            const t = text.replace(/\s+/g, '');
            const m = t.match(/(?:US\$|CA\$|AU\$|NZ\$|\$)(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)/);
            if (!m) return null;
            const val = parseFloat(m[1].replace(/,/g, ''));
            return Number.isFinite(val) && val >= 10 && val <= 10000 ? val : null;
          }

          function isPerNight(text) {
            // "/night", "per night", or "for 1 night" (Airbnb 1-night card format)
            return /\/\s*night|per\s+night|for\s+1\s+night\b/i.test(text);
          }

          // Returns N (≥2) when text describes a multi-night trip total, else 0.
          // Detects "for N nights" regardless of stayNights so minimum-stay
          // listings are caught even in 1-night day queries.
          function detectTripNights(text) {
            if (!text) return 0;
            // Most reliable: "for N nights" (Airbnb trip-total aria-label)
            const m = text.match(/for\s+(\d+)\s+nights?/i);
            if (m) { const n = parseInt(m[1], 10); if (n >= 2) return n; }
            // Secondary: bare "N nights" when query length matches (multi-night criteria search)
            if (stayNights >= 2 && new RegExp('\\b' + stayNights + '\\s+nights?\\b', 'i').test(text)) {
              return stayNights;
            }
            return 0;
          }

          // ── Strikethrough detection (6-ancestor walk) ─────────────────────
          // Airbnb nests prices deeply; 4 ancestors was insufficient.
          function isLineThrough(el) {
            let node = el;
            for (let i = 0; i < 6 && node && node !== document.body; i++) {
              const s = window.getComputedStyle(node);
              const td = (s.textDecoration || '') + (s.textDecorationLine || '');
              if (td.includes('line-through')) return true;
              if (node.tagName === 'S' || node.tagName === 'DEL') return true;
              node = node.parentElement;
            }
            return false;
          }

          // ── Strategy A: aria-label ────────────────────────────────────────
          // Airbnb listing cards typically set aria-label to something like:
          //   "$189 per night. Originally $230. 4.91 (312 reviews). Superhost."
          // The CURRENT (discounted) price always appears before "Originally".
          // Parsing stops at "original"/"discounted from" so we never pick the
          // crossed-out base price.
          function extractFromAriaLabel(root) {
            const candidates = [root].concat(
              Array.from(root.querySelectorAll('[aria-label]'))
            );
            for (let i = 0; i < candidates.length; i++) {
              const el = candidates[i];
              const label = (el.getAttribute('aria-label') || '').trim();
              if (!label || label.length < 4) continue;
              const labelIsNightly = isPerNight(label);
              const labelTripNights = detectTripNights(label);
              if (!labelIsNightly && labelTripNights === 0) continue;

              // Truncate at first mention of "original" or "discounted from"
              // so we only see the current/effective price.
              const lower = label.toLowerCase();
              let cut = label.length;
              const origIdx = lower.indexOf('original');
              const discIdx = lower.indexOf('discounted from');
              if (origIdx >= 0) cut = Math.min(cut, origIdx);
              if (discIdx >= 0) cut = Math.min(cut, discIdx);
              const relevantPart = label.slice(0, cut);

              // Extract the first valid price in the relevant portion.
              const priceRe = /(?:US\$|CA\$|AU\$|NZ\$|\$)\s*(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)/g;
              let match;
              while ((match = priceRe.exec(relevantPart)) !== null) {
                const val = parseFloat(match[1].replace(/,/g, ''));
                if (Number.isFinite(val) && val >= 10 && val <= 10000) {
                  return {
                    value: val,
                    kind: labelTripNights > 0 ? 'trip_total_from_aria' : 'nightly_from_aria',
                    price_nights: labelTripNights > 0 ? labelTripNights : 1,
                    source: 'aria'
                  };
                }
              }
            }
            return null;
          }

          // ── Strategy B: DOM with strict discount awareness ────────────────
          // Scans price-bearing elements inside the card.
          // If a discount is present (some prices are struck through):
          //   → must find at least one non-strikethrough price → use it.
          //   → if ALL prices are struck through → ambiguous → return null (fail safe).
          // If no strikethrough present:
          //   → use the last visible price in DOM order.
          function extractFromDOM(root) {
            const els = Array.from(
              root.querySelectorAll('[data-testid*="price"], span, div')
            );
            const found = [];

            for (let idx = 0; idx < els.length; idx++) {
              const el = els[idx];
              const text = (el.innerText || '').trim();
              if (!text || text.length > 150) continue;
              if (!/(US\$|CA\$|AU\$|NZ\$|\$)/.test(text)) continue;
              const textIsNightly = isPerNight(text);
              const textTripNights = detectTripNights(text);
              if (!textIsNightly && textTripNights === 0) continue;
              if (/(total|tax|fee|cleaning|service|before taxes)/i.test(text)) continue;

              const val = parseMoneyValue(text);
              if (val == null) continue;

              found.push({
                value: val,
                domIndex: idx,
                strikethrough: isLineThrough(el),
                isNightly: textIsNightly,
                tripNights: textTripNights,
              });
            }

            if (found.length === 0) return null;

            const hasOriginalPrice = found.some(function(c) { return c.strikethrough; });
            const currentPrices = found.filter(function(c) { return !c.strikethrough; });

            if (hasOriginalPrice) {
              // Discount scenario: require a visible (non-struck) price.
              // If none exists, we cannot determine which number is current → fail safe.
              if (currentPrices.length === 0) return null;

              // Last non-strikethrough in DOM order = the final displayed price.
              currentPrices.sort(function(a, b) { return a.domIndex - b.domIndex; });
              const chosen = currentPrices[currentPrices.length - 1];
              return {
                value: chosen.value,
                kind: chosen.tripNights > 0 ? 'trip_total_discounted' : 'nightly_discounted',
                price_nights: chosen.tripNights > 0 ? chosen.tripNights : 1,
                source: 'dom'
              };
            } else {
              // No discount detected; last price in DOM order.
              found.sort(function(a, b) { return a.domIndex - b.domIndex; });
              const chosen = found[found.length - 1];
              return {
                value: chosen.value,
                kind: chosen.tripNights > 0 ? 'trip_total_standard' : 'nightly_standard',
                price_nights: chosen.tripNights > 0 ? chosen.tripNights : 1,
                source: 'dom'
              };
            }
          }

          // ── Two-layer fallback chain ──────────────────────────────────────
          // A → B → null (fail safe: no price is better than the wrong price)
          function selectCardPrice(root) {
            const ariaResult = extractFromAriaLabel(root);
            if (ariaResult) return ariaResult;

            const domResult = extractFromDOM(root);
            if (domResult) return domResult;

            return { value: null, kind: 'unknown', price_nights: 1, source: 'none' };
          }

          function normalizeLine(text) {
            return (text || '').replace(/\s+/g, ' ').trim();
          }

          function isLikelyBadgeOrMeta(line) {
            if (!line) return true;
            if (line.length < 8 || line.length > 140) return true;
            if (/^(top\s+guest\s+favorite|guest\s+favorite|superhost|rare\s+find|new)$/i.test(line)) return true;
            if (/(US\$|CA\$|AU\$|NZ\$|\$)\s*\d/.test(line)) return true;
            if (/\b(?:night|nights|total|tax|taxes|fee|fees|cleaning|discounted|originally|before taxes)\b/i.test(line)) return true;
            if (/\b(?:review|reviews)\b/i.test(line)) return true;
            if (/\b(?:guest|guests|bedroom|bedrooms|bed|beds|bath|baths)\b/i.test(line) && /\d/.test(line)) return true;
            if (/^\d(?:\.\d+)?(?:\s*\(|\s*$)/.test(line)) return true;
            if (/^(entire|private|shared)\s.+\sin\s.+/i.test(line)) return true;
            if (/^(room in|home in|apartment in|condo in|townhouse in|rental unit in|villa in|cabin in)\b/i.test(line)) return true;
            return false;
          }

          function extractListingTitle(root, anchor) {
            const candidates = [];

            function pushCandidate(raw) {
              const line = normalizeLine(raw);
              if (!line) return;
              if (!candidates.includes(line)) candidates.push(line);
            }

            const headingNodes = root.querySelectorAll('h1, h2, h3, [role="heading"]');
            for (const el of headingNodes) {
              pushCandidate(el.textContent || '');
            }

            if (anchor) {
              const anchorLines = (anchor.innerText || '').split('\n');
              for (const line of anchorLines) pushCandidate(line);
            }

            const rootLines = (root.innerText || '').split('\n');
            for (const line of rootLines) pushCandidate(line);

            const preferred = candidates.find(function(line) {
              return !isLikelyBadgeOrMeta(line);
            });
            if (preferred) return preferred;

            return candidates.find(function(line) { return line.length >= 6; }) || '';
          }

          // ── Card collection ───────────────────────────────────────────────
          const cardRoots = []
            .concat(Array.from(
              document.querySelectorAll('div[data-testid="card-container"]')
            ))
            .concat(Array.from(
              document.querySelectorAll('div[data-testid^="listing-card"]')
            ))
            .concat(
              Array.from(document.querySelectorAll('a[href*="/rooms/"]'))
                .map(function(a) { return a.closest('div[data-testid],div') || a; })
            );

          const uniq = new Set();
          const cards = [];

          for (let ci = 0; ci < cardRoots.length; ci++) {
            const root = cardRoots[ci];
            if (!root) continue;
            const a = root.querySelector('a[href*="/rooms/"]');
            if (!a) continue;
            const href = a.getAttribute('href') || '';
            const abs = href.startsWith('http')
              ? href
              : location.origin + href;
            const m = abs.match(/\/rooms\/(\d+)/);
            const roomId = m ? m[1] : abs;
            if (uniq.has(roomId)) continue;
            uniq.add(roomId);

            const text = (root.innerText || '').trim();
            const title = extractListingTitle(root, a);

            const priceChoice = selectCardPrice(root);

            let rating = null;
            let reviews = null;
            const rateMatch = text.match(/(\d\.\d\d|\d\.\d)\s*(?:\(|·|・)?\s*(\d+)?/);
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
              price_text: priceChoice.value != null ? String(priceChoice.value) : '',
              price_value: priceChoice.value,
              price_kind: priceChoice.kind,
              price_nights: priceChoice.price_nights || 1,
              price_source: priceChoice.source,
              rating,
              reviews,
            });
          }
          return cards;
        }""",
        stay_nights,
    )


def scroll_and_collect(
    page,
    max_rounds: int = 12,
    max_cards: int = 80,
    pause_ms: int = 900,
    rate_limit_seconds: float = 1.0,
    stay_nights: int = 1,
) -> List[Dict[str, Any]]:
    """Scroll the search page and collect listing cards with rate limiting."""
    all_cards: Dict[str, Dict[str, Any]] = {}
    no_new = 0

    for rd in range(1, max_rounds + 1):
        try:
            page.wait_for_timeout(400)
            cards = collect_search_cards(page, stay_nights=stay_nights)
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

        time.sleep(rate_limit_seconds)

        try:
            page.mouse.wheel(0, 1600)
        except Exception:
            pass
        page.wait_for_timeout(pause_ms)

    return list(all_cards.values())


def parse_card_to_spec(card: Dict[str, Any]) -> ListingSpec:
    """Convert a raw card dict into a ListingSpec.

    Price comes from the JS two-layer extractor (aria-label first, DOM fallback).
    If the JS could not produce a trustworthy price it returns null; we do not
    invent a fallback here — a missing price is safer than a wrong one.

    Trip-total prices are always normalised to per-night using the night count
    detected by the JS extractor (card["price_nights"]).  This handles minimum-
    stay listings (e.g. "for 2 nights") even when the query was for 1 night.
    """
    text = clean(card.get("text") or "")
    price: Optional[float] = None

    raw = card.get("price_value")
    if isinstance(raw, (int, float)) and 10 <= raw <= 10000:
        price = float(raw)
    else:
        # JS returned null (unknown/ambiguous) — do not guess from raw text.
        price = None

    price_source = card.get("price_source", "unknown")
    price_kind = card.get("price_kind", "unknown")
    price_nights = int(card.get("price_nights") or 1)
    if (
        price is not None
        and price_nights > 1
        and str(price_kind).startswith("trip_total")
    ):
        price = round(price / price_nights, 2)
    if price is not None:
        logger.debug(
            f"[card] room={card.get('room_id')} price=${price} "
            f"source={price_source} kind={price_kind} nights={price_nights}"
        )
    else:
        logger.debug(
            f"[card] room={card.get('room_id')} price=null "
            f"source={price_source} kind={price_kind} — excluded from comps"
        )

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
        scrape_nights=price_nights,
    )
