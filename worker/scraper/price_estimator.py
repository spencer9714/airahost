"""
Playwright-based Airbnb price estimator — orchestrator.

Connects to a local Chrome instance via CDP (Chrome DevTools Protocol)
to scrape target listing specs and nearby comparable listings.

Uses day-by-day 2-night-primary queries to get accurate nightly prices and
maximize comp pool coverage (minimum-stay=2 listings).  See day_query.py
for the rationale and per-night normalisation details.

This module orchestrates the pipeline by delegating to:
  - target_extractor: listing page spec extraction
  - comparable_collector: search page scrolling & card parsing
  - similarity: scoring & filtering
  - pricing_engine: price recommendation & transparent output
  - day_query: day-by-day 1-night query engine
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from datetime import datetime as dt, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from worker.core.geo_filter import apply_geo_filter, haversine_km, DEFAULT_MAX_RADIUS_KM
from worker.core.similarity import (
    SIMILARITY_FLOOR,
    comp_urls_match,
    filter_similar_candidates,
    similarity_score,
)
from worker.scraper.comparable_collector import (
    build_search_url,
    extract_comp_coords,
    parse_card_to_spec,
    scroll_and_collect,
    wait_for_cards,
)
from worker.scraper.day_query import (
    DAY_MAX_CARDS,
    DAY_SCROLL_ROUNDS,
    MAX_NIGHTS,
    MAX_SAMPLE_QUERIES,
    PER_DAY_MAX_RETRIES,
    SAMPLE_THRESHOLD,
    SIMILARITY_FLOOR_FALLBACK,
    compute_sample_dates,
    daterange_nights,
    detect_discount_evidence,
    estimate_base_price_for_date,
    interpolate_missing_days,
)
from worker.scraper.target_extractor import (
    ListingSpec,
    check_cdp_endpoint,
    extract_listing_page_title,
    extract_target_spec,
    normalize_airbnb_url,
    safe_domain_base,
)

logger = logging.getLogger("worker.scraper")
ROOM_ID_RE = re.compile(r"/rooms/(\d+)")


def _title_looks_suspicious(title: str) -> bool:
    t = (title or "").strip()
    if len(t) < 8:
        return True
    if re.fullmatch(r"(?i)(top guest favorite|guest favorite|superhost|rare find|new|show price breakdown)", t):
        return True
    if re.fullmatch(r"(?i)[A-Za-z]{3,9}\s+\d{1,2}\s+to\s+\d{1,2}", t):
        return True
    if re.fullmatch(
        r"(?i)(?:jan|feb|mar|apr|may|jun|june|jul|july|aug|sep|sept|oct|nov|dec)\s+\d{1,2}\s+to\s+"
        r"(?:jan|feb|mar|apr|may|jun|june|jul|july|aug|sep|sept|oct|nov|dec)\s+\d{1,2}",
        t,
    ):
        return True
    if re.search(r"(?i)\b(show price breakdown|price breakdown|reserve|check in|check out)\b", t):
        return True
    if re.search(r"(?i)\b(?:may|jun|june|jul|july|aug|sep|sept|oct|nov|dec|jan|feb|mar|apr)\s+\d{1,2}\s+to\s+\d{1,2}\b", t):
        return True
    return False


def _repair_suspicious_comparable_titles(
    page,
    transparent_result: Dict[str, Any],
    extraction_warnings: List[str],
    limit: int = 8,
) -> None:
    listings = transparent_result.get("comparableListings")
    if not isinstance(listings, list) or not listings:
        return

    repaired = 0
    for item in listings:
        if repaired >= limit:
            break
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = normalize_airbnb_url(str(item.get("url") or "").strip())
        if not url or not _title_looks_suspicious(title):
            continue
        try:
            resolved_title, title_warnings = extract_listing_page_title(page, url)
        except Exception as exc:
            extraction_warnings.append(f"Comparable title repair failed for {url}: {exc}")
            continue
        extraction_warnings.extend(title_warnings)
        if resolved_title and not _title_looks_suspicious(resolved_title):
            item["title"] = resolved_title
            repaired += 1
            logger.info(f"[comp_title] repaired title for {url} -> {resolved_title!r}")


def _repair_incomplete_comparable_specs(
    page,
    transparent_result: Dict[str, Any],
    extraction_warnings: List[str],
    limit: int = 6,
) -> None:
    """Backfill comp metadata from listing pages when search-card fields are incomplete."""
    listings = transparent_result.get("comparableListings")
    if not isinstance(listings, list) or not listings:
        return

    repaired = 0
    for item in listings:
        if repaired >= limit:
            break
        if not isinstance(item, dict):
            continue

        url = normalize_airbnb_url(str(item.get("url") or "").strip())
        if not url:
            continue

        needs_repair = any(
            item.get(key) in (None, "", 0)
            for key in ("accommodates", "bedrooms", "baths")
        ) or not str(item.get("location") or "").strip()

        if not needs_repair and not _title_looks_suspicious(str(item.get("title") or "")):
            continue

        try:
            spec, warnings = extract_target_spec(page, url)
        except Exception as exc:
            extraction_warnings.append(f"Comparable spec repair failed for {url}: {exc}")
            continue

        extraction_warnings.extend(warnings)

        if spec.title and _title_looks_suspicious(str(item.get("title") or "")):
            item["title"] = spec.title
        if spec.property_type:
            item["propertyType"] = spec.property_type
        if isinstance(spec.accommodates, (int, float)):
            item["accommodates"] = int(spec.accommodates)
        if isinstance(spec.bedrooms, (int, float)):
            item["bedrooms"] = int(spec.bedrooms)
        if isinstance(spec.baths, (int, float)):
            item["baths"] = round(float(spec.baths), 1)
        if spec.location:
            item["location"] = spec.location
        if isinstance(spec.rating, (int, float)) and item.get("rating") is None:
            item["rating"] = round(float(spec.rating), 2)
        if isinstance(spec.reviews, (int, float)) and item.get("reviews") is None:
            item["reviews"] = int(spec.reviews)

        repaired += 1
        logger.info(
            "[comp_spec] repaired %s -> accommodates=%s bedrooms=%s baths=%s location=%r",
            url,
            item.get("accommodates"),
            item.get("bedrooms"),
            item.get("baths"),
            item.get("location"),
        )


# ---------------------------------------------------------------------------
# Helper: assemble transparent result from day-by-day data
# ---------------------------------------------------------------------------


def _build_daily_transparent_result(
    target: ListingSpec,
    query_criteria: Dict[str, Any],
    all_day_results: List[Dict[str, Any]],
    timings_ms: Dict[str, int],
    source: str,
    extraction_warnings: List[str],
    discount_evidence: Optional[Dict[str, Any]] = None,
    benchmark_info: Optional[Dict[str, Any]] = None,
    target_price_confidence: Optional[str] = None,
    spec_backfill: Optional[Dict[str, Any]] = None,
    spec_extraction_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Assemble the unified transparent result dict from day-by-day results.

    This replaces build_transparent_result for the day-by-day pipeline,
    aggregating per-day stats into the standard output shape.
    """
    # Aggregate prices across all days with valid medians
    valid_prices = [
        r["median_price"] for r in all_day_results
        if r.get("median_price") is not None
    ]

    sampled_days = sum(1 for r in all_day_results if r.get("is_sampled", False))
    interpolated_days = sum(
        1 for r in all_day_results
        if not r.get("is_sampled", False) and r.get("median_price") is not None
    )
    missing_days = sum(
        1 for r in all_day_results if r.get("median_price") is None
    )

    total_comps_collected = sum(r.get("comps_collected", 0) for r in all_day_results)
    total_comps_used = sum(r.get("comps_used", 0) for r in all_day_results)
    total_below_floor = sum(r.get("below_similarity_floor", 0) for r in all_day_results)
    low_comp_confidence_days = sum(
        1 for r in all_day_results
        if "low_comp_confidence" in (r.get("flags") or [])
    )
    fallback_relaxed_days = sum(
        1 for r in all_day_results if r.get("selection_mode") == "fallback_relaxed"
    )

    def _date_add(date_str: str, n: int) -> str:
        """Add n days to a YYYY-MM-DD string, return YYYY-MM-DD."""
        d = dt.strptime(date_str, "%Y-%m-%d") + timedelta(days=n)
        return d.strftime("%Y-%m-%d")

    comparable_index: Dict[str, Dict[str, Any]] = {}

    def _prefer_better_comp_value(current: Any, candidate: Any) -> Any:
        """Keep richer comp metadata when the same listing appears across days."""
        if candidate is None:
            return current
        if isinstance(candidate, str):
            cand = candidate.strip()
            if not cand:
                return current
            cur = current.strip() if isinstance(current, str) else ""
            return candidate if not cur else current
        return candidate if current is None else current

    for day_result in all_day_results:
        day_date = day_result.get("date")
        # comp_prices holds prices for ALL scraped comps (not just top_k).
        # Use it to fill priceByDate for any comp already in the index.
        day_comp_prices: Dict[str, float] = day_result.get("comp_prices") or {}

        for comp in day_result.get("top_comps", []) or []:
            comp_id = str(comp.get("id") or comp.get("url") or "").strip()
            if not comp_id:
                continue
            score = float(comp.get("similarity") or 0.0)
            # Prefer comp_prices for the day's price; fall back to top_comps value.
            price = day_comp_prices.get(comp_id) or comp.get("nightlyPrice")
            qn = int(comp.get("queryNights") or 1)
            if comp_id not in comparable_index:
                comparable_index[comp_id] = {
                    "item": dict(comp),
                    "score_sum": score,
                    "count": 1,
                    "price_by_date": {},
                    "max_query_nights": qn,
                }
            else:
                item = comparable_index[comp_id]["item"]
                for key in ("title", "propertyType", "location", "url"):
                    item[key] = _prefer_better_comp_value(item.get(key), comp.get(key))
                for key in ("accommodates", "bedrooms", "baths", "rating", "reviews"):
                    item[key] = _prefer_better_comp_value(item.get(key), comp.get(key))
                comparable_index[comp_id]["score_sum"] += score
                comparable_index[comp_id]["count"] += 1
                if qn > comparable_index[comp_id]["max_query_nights"]:
                    comparable_index[comp_id]["max_query_nights"] = qn
            if day_date and isinstance(price, (int, float)) and price > 0:
                _price_rounded = round(float(price), 2)
                # Always write the primary check-in night.
                comparable_index[comp_id]["price_by_date"][day_date] = _price_rounded
                # Expand to all nights covered by this query (queryNights > 1 means
                # the price was a multi-night total normalized to per-night; each of
                # those nights should show the same per-night rate).
                for i in range(1, qn):
                    night = _date_add(day_date, i)
                    # Only fill expanded nights when not already set by a direct query.
                    comparable_index[comp_id]["price_by_date"].setdefault(night, _price_rounded)

        # Second pass: fill priceByDate for comps already in the index that
        # appeared in this day's full results but not in top_comps.
        if day_date and day_comp_prices:
            for comp_id, price in day_comp_prices.items():
                if comp_id in comparable_index and day_date not in comparable_index[comp_id]["price_by_date"]:
                    comparable_index[comp_id]["price_by_date"][day_date] = price
                    # Also expand to covered nights using the stored max_query_nights.
                    qn = comparable_index[comp_id].get("max_query_nights", 1)
                    for i in range(1, qn):
                        night = _date_add(day_date, i)
                        comparable_index[comp_id]["price_by_date"].setdefault(night, price)

    comparable_listings: List[Dict[str, Any]] = []
    for state in comparable_index.values():
        item = dict(state["item"])
        avg_score = state["score_sum"] / max(1, state["count"])
        # Surface comps down to the fallback floor — comps used in fallback_relaxed
        # days have scores between SIMILARITY_FLOOR_FALLBACK and SIMILARITY_FLOOR
        # and should still appear in the comparable listings display.
        if avg_score < SIMILARITY_FLOOR_FALLBACK:
            continue
        item["similarity"] = round(avg_score, 3)
        item["usedInPricingDays"] = state["count"]
        price_by_date = state.get("price_by_date", {})
        if price_by_date:
            item["priceByDate"] = price_by_date
        max_qn = state.get("max_query_nights", 1)
        if max_qn > 1:
            item["queryNights"] = max_qn
        elif "queryNights" in item:
            del item["queryNights"]
        comparable_listings.append(item)
    # Sort: similarity DESC; use reviews as tie-break (more reviews = more signal).
    comparable_listings.sort(
        key=lambda row: (
            -float(row.get("similarity") or 0.0),
            -int(row.get("reviews") or 0),
        )
    )
    comparable_listings = comparable_listings[:15]

    # Price distribution from aggregated daily medians
    price_dist: Dict[str, Any] = {
        "min": None, "p25": None, "median": None, "p75": None, "max": None,
        "currency": "USD",
    }

    if valid_prices:
        price_dist["min"] = round(min(valid_prices), 2)
        price_dist["max"] = round(max(valid_prices), 2)
        price_dist["median"] = round(statistics.median(valid_prices), 2)
        if len(valid_prices) >= 4:
            q = statistics.quantiles(valid_prices, n=4)
            price_dist["p25"] = round(q[0], 2)
            price_dist["p75"] = round(q[2], 2)

    # Weekday vs weekend estimates from actual data
    weekday_prices = [
        r["median_price"] for r in all_day_results
        if r.get("median_price") is not None and not r.get("is_weekend", False)
    ]
    weekend_prices = [
        r["median_price"] for r in all_day_results
        if r.get("median_price") is not None and r.get("is_weekend", False)
    ]

    overall_median = price_dist["median"]
    weekday_est = round(statistics.median(weekday_prices)) if weekday_prices else (round(overall_median) if overall_median else None)
    weekend_est = round(statistics.median(weekend_prices)) if weekend_prices else (round(overall_median) if overall_median else None)

    return {
        "targetSpec": {
            "title": target.title or "",
            "location": target.location or "",
            "city": target.city or "",
            "state": target.state or "",
            "postalCode": target.postal_code or "",
            "country": target.country or "",
            "countryCode": target.country_code or "",
            "lat": target.lat,
            "lng": target.lng,
            "propertyType": target.property_type or "",
            "accommodates": target.accommodates,
            "bedrooms": target.bedrooms,
            "beds": target.beds,
            "baths": target.baths,
            "amenities": target.amenities or [],
            "rating": target.rating,
            "reviews": target.reviews,
            "nightlyPrice": target.nightly_price,
            "currency": target.currency or "USD",
            "priceConfidence": target_price_confidence,
            # Indicates whether spec fields were backfilled from saved attributes.
            # "live" = fully extracted; "mixed" = some fields from fallback; "partial" = still incomplete
            "specSource": (
                "live" if not spec_backfill or not spec_backfill.get("fields_filled")
                else ("partial" if spec_backfill.get("is_partial") else "mixed")
            ),
            "specFieldsBackfilled": (spec_backfill or {}).get("fields_filled") or [],
            "specFieldsMissing": (spec_backfill or {}).get("fields_still_missing") or [],
        },
        "queryCriteria": query_criteria,
        "compsSummary": {
            "collected": total_comps_collected,
            "afterFiltering": total_comps_used,
            "usedForPricing": total_comps_used,
            "filterStage": "day_by_day",
            "topSimilarity": None,
            "avgSimilarity": None,
            "sampledDays": sampled_days,
            "interpolatedDays": interpolated_days,
            "missingDays": missing_days,
            "belowSimilarityFloor": total_below_floor,
            "filterFloor": SIMILARITY_FLOOR,
            "fallbackFloor": SIMILARITY_FLOOR_FALLBACK,
            "lowCompConfidenceDays": low_comp_confidence_days,
            "fallbackRelaxedDays": fallback_relaxed_days,
        },
        "priceDistribution": price_dist,
        "recommendedPrice": {
            "nightly": overall_median,
            "weekdayEstimate": weekday_est,
            "weekendEstimate": weekend_est,
            "discountApplied": 0.0,
            "notes": "",
        },
        "comparableListings": comparable_listings,
        "benchmarkInfo": benchmark_info,
        "debug": {
            "source": source,
            "extractionWarnings": extraction_warnings,
            "timingsMs": timings_ms,
            "pipelineVersion": "day-by-day-v1",
            "discountEvidence": discount_evidence,
            "specBackfill": spec_backfill,
            "specExtractionMeta": spec_extraction_meta,
            "dayQueryStats": {
                "totalNights": len(all_day_results),
                "sampled": sampled_days,
                "interpolated": interpolated_days,
                "missing": missing_days,
                "validPriceCount": len(valid_prices),
            },
        },
    }


def _preferred_comp_id(listing_url: str) -> str:
    match = ROOM_ID_RE.search(listing_url)
    return match.group(1) if match else listing_url


def _build_url_mode_benchmark_info(
    all_day_results: List[Dict[str, Any]],
    preferred_comps: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    if not preferred_comps:
        return None

    primary_url = str(preferred_comps[0].get("listingUrl") or "").strip()
    if not primary_url:
        return None

    sampled_days = [r for r in all_day_results if r.get("is_sampled", False)]
    total_days = len(sampled_days)
    if total_days == 0:
        return None

    from worker.core.benchmark import BENCHMARK_MARKET_WEIGHT, BENCHMARK_MAX_ADJ

    primary_id = _preferred_comp_id(primary_url)
    benchmark_prices: List[float] = []
    market_prices: List[float] = []
    market_adjustments: List[float] = []
    outlier_days = 0
    search_hits = 0
    failed = 0

    secondary_urls = [
        str(pc.get("listingUrl") or "").strip()
        for pc in preferred_comps[1:]
        if isinstance(pc, dict) and str(pc.get("listingUrl") or "").strip()
    ]
    secondary_found: Dict[str, List[float]] = {url: [] for url in secondary_urls}

    for day in sampled_days:
        comp_prices = day.get("comp_prices") or {}
        primary_price = comp_prices.get(primary_id)
        if primary_price is None:
            for tc in day.get("top_comps", []) or []:
                tc_url = str(tc.get("url") or "").strip()
                if tc_url and comp_urls_match(tc_url, primary_url):
                    price = tc.get("nightlyPrice")
                    if isinstance(price, (int, float)) and price > 0:
                        primary_price = float(price)
                        break

        if primary_price is not None:
            benchmark_prices.append(round(float(primary_price), 2))
            search_hits += 1
            market_price = day.get("median_price")
            if isinstance(market_price, (int, float)) and market_price > 0:
                market_prices.append(round(float(market_price), 2))
                adj = ((float(market_price) - float(primary_price)) / float(primary_price)) * 100
                market_adjustments.append(adj)
                if abs(adj) >= 40:
                    outlier_days += 1
        else:
            failed += 1

        for sec_url in secondary_urls:
            sec_id = _preferred_comp_id(sec_url)
            sec_price = comp_prices.get(sec_id)
            if sec_price is None:
                for tc in day.get("top_comps", []) or []:
                    tc_url = str(tc.get("url") or "").strip()
                    if tc_url and comp_urls_match(tc_url, sec_url):
                        price = tc.get("nightlyPrice")
                        if isinstance(price, (int, float)) and price > 0:
                            sec_price = float(price)
                            break
            if isinstance(sec_price, (int, float)) and sec_price > 0:
                secondary_found[sec_url].append(round(float(sec_price), 2))

    benchmark_used = len(benchmark_prices) > 0
    avg_benchmark = round(statistics.mean(benchmark_prices), 2) if benchmark_prices else None
    avg_market = round(statistics.mean(market_prices), 2) if market_prices else None
    avg_adj = round(statistics.mean(market_adjustments), 1) if market_adjustments else None

    secondary_comps: List[Dict[str, Any]] = []
    for sec_url in secondary_urls:
        prices = secondary_found.get(sec_url, [])
        secondary_comps.append({
            "url": sec_url,
            "avgPrice": round(statistics.mean(prices), 2) if prices else None,
            "daysFound": len(prices),
            "totalDays": total_days,
        })

    consensus_signal: Optional[str] = None
    found_avgs = [row["avgPrice"] for row in secondary_comps if row["avgPrice"] is not None]
    if found_avgs and avg_benchmark is not None:
        secondary_mean = statistics.mean(found_avgs)
        pct_from_benchmark = abs(secondary_mean - avg_benchmark) / avg_benchmark
        if avg_market is not None and avg_market > 0:
            pct_from_market = abs(secondary_mean - avg_market) / avg_market
            if pct_from_benchmark <= 0.20:
                consensus_signal = "strong"
            elif pct_from_market <= 0.20 and pct_from_benchmark > 0.20:
                consensus_signal = "divergent"
            else:
                consensus_signal = "mixed"
        else:
            consensus_signal = "strong" if pct_from_benchmark <= 0.20 else "mixed"

    conflict_detected = bool(
        (total_days > 0 and outlier_days / total_days > 0.30)
        or consensus_signal == "divergent"
    )

    return {
        "benchmarkUsed": benchmark_used,
        "benchmarkUrl": primary_url,
        "benchmarkFetchStatus": "search_hit" if benchmark_used else "failed",
        "benchmarkFetchMethod": "search_hit" if benchmark_used else "failed",
        "avgBenchmarkPrice": avg_benchmark,
        "avgMarketPrice": avg_market,
        "marketAdjustmentPct": avg_adj,
        "appliedMarketWeight": BENCHMARK_MARKET_WEIGHT,
        "effectiveMarketWeight": BENCHMARK_MARKET_WEIGHT,
        "maxAdjCap": BENCHMARK_MAX_ADJ,
        "outlierDays": outlier_days,
        "conflictDetected": conflict_detected,
        "fallbackReason": None if benchmark_used else "benchmark_not_found_in_url_mode",
        "fetchStats": {
            "searchHits": search_hits,
            "directFetches": 0,
            "failed": failed,
            "totalDays": total_days,
            "highConfidenceDays": search_hits,
            "mediumConfidenceDays": 0,
            "lowConfidenceDays": 0,
        },
        "secondaryComps": secondary_comps or None,
        "consensusSignal": consensus_signal,
    }


# ---------------------------------------------------------------------------
# Target spec fallback
# ---------------------------------------------------------------------------


def _is_spec_degraded(target: ListingSpec) -> bool:
    """
    Heuristic: return True when the Airbnb page appears to have returned a
    degraded / stale render that is missing most structural fields.

    Two conditions trigger degraded detection:
      - Location is empty AND at least one of (bedrooms, accommodates, baths) is None, OR
      - Three or more of the five key fields are None / empty.

    These patterns typically indicate a bot-challenge page, a redirect, or a
    cached page fragment rather than real listing content.
    """
    missing_location = not bool(target.location and target.location.strip())
    missing_count = sum([
        missing_location,
        target.bedrooms is None,
        target.accommodates is None,
        target.baths is None,
        not bool(target.property_type and target.property_type.strip()),
    ])
    if missing_location and missing_count >= 2:
        return True
    if missing_count >= 3:
        return True
    return False


def _backfill_target_spec(
    target: ListingSpec,
    attrs: Dict[str, Any],
) -> tuple:
    """
    Fill missing target spec fields from saved listing input attributes.

    Called after extract_target_spec() when key fields are absent (None / "").
    Only fills fields that are missing — never overwrites a live-extracted value.

    Returns (updated_target, debug_meta) where debug_meta is a dict with:
      "fields_filled"       — list of field names backfilled from attrs
      "fields_still_missing"— list of key fields still None/empty after backfill
      "source"              — "saved_attributes" | "none"
      "is_partial"          — True if any key field remains missing after backfill
    """
    fields_filled: list = []

    if not target.property_type:
        v = attrs.get("propertyType")
        if v and isinstance(v, str):
            target.property_type = v
            fields_filled.append("property_type")

    if target.accommodates is None:
        v = attrs.get("maxGuests")
        if isinstance(v, int) and v > 0:
            target.accommodates = v
            fields_filled.append("accommodates")

    if target.bedrooms is None:
        v = attrs.get("bedrooms")
        if isinstance(v, int) and v >= 0:
            target.bedrooms = v
            fields_filled.append("bedrooms")

    if target.baths is None:
        v = attrs.get("bathrooms")
        if isinstance(v, (int, float)) and v > 0:
            target.baths = float(v)
            fields_filled.append("baths")

    # beds is not a standard user-input field but include for completeness
    if target.beds is None:
        v = attrs.get("beds")
        if isinstance(v, int) and v > 0:
            target.beds = v
            fields_filled.append("beds")

    fields_still_missing: list = []
    if not target.property_type:
        fields_still_missing.append("property_type")
    if target.accommodates is None:
        fields_still_missing.append("accommodates")
    if target.bedrooms is None:
        fields_still_missing.append("bedrooms")
    if target.baths is None:
        fields_still_missing.append("baths")

    return target, {
        "fields_filled": fields_filled,
        "fields_still_missing": fields_still_missing,
        "source": "saved_attributes" if fields_filled else "none",
        "is_partial": bool(fields_still_missing),
    }


# ---------------------------------------------------------------------------
# Main scrape pipeline — day-by-day (connects to local Chrome via CDP)
# ---------------------------------------------------------------------------


def run_scrape(
    listing_url: str,
    checkin: str,
    checkout: str,
    cdp_url: str = "http://127.0.0.1:9222",
    adults: int = 2,
    top_k: int = 10,
    max_scroll_rounds: int = DAY_SCROLL_ROUNDS,
    max_cards: int = DAY_MAX_CARDS,
    max_runtime_seconds: int = 180,
    rate_limit_seconds: float = 1.0,
    cdp_connect_timeout_ms: int = 15000,
    preferred_comps: Optional[List[Dict[str, Any]]] = None,
    target_lat: Optional[float] = None,
    target_lng: Optional[float] = None,
    max_radius_km: Optional[float] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    nightly_plan: Optional[Any] = None,
    fallback_attributes: Optional[Dict[str, Any]] = None,
    fallback_address: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Full scrape pipeline using day-by-day 1-night queries.

    Returns (daily_results, transparent_result).
    daily_results is a list of dicts, one per night in [checkin, checkout).
    Each dict contains: date, median_price, comps_collected, comps_used,
    filter_stage, flags, is_sampled, is_weekend, price_distribution, error.

    fallback_attributes: saved listing input attributes (bedrooms, bathrooms,
    maxGuests, propertyType, ...) used to backfill any target spec fields that
    extract_target_spec() could not recover from the Airbnb page. Only fills
    missing fields — never overwrites live-extracted values.

    Raises ValueError if the date range exceeds MAX_NIGHTS.
    """
    from playwright.sync_api import sync_playwright

    listing_url = normalize_airbnb_url(listing_url)

    start_time = time.time()
    timings: Dict[str, int] = {}
    extraction_warnings: List[str] = []
    base_origin = safe_domain_base(listing_url)

    # Parse and validate dates
    d_start = dt.strptime(checkin, "%Y-%m-%d").date()
    d_end = dt.strptime(checkout, "%Y-%m-%d").date()
    total_nights = (d_end - d_start).days
    if total_nights < 1:
        return [], _empty_transparent("scrape", "Invalid date range: checkout must be after checkin")
    if total_nights > MAX_NIGHTS:
        raise ValueError(
            f"Date range of {total_nights} nights exceeds maximum of {MAX_NIGHTS}. "
            f"Please select a shorter range."
        )

    all_nights = daterange_nights(d_start, d_end)

    # Determine which nights to actually query.
    # Nightly jobs use the plan's tiered observe set; interactive uses the
    # standard even-sampling strategy.
    if nightly_plan is not None:
        sample_indices = nightly_plan.observe_indices
        _eff_scroll_rounds = nightly_plan.scroll_rounds
        _eff_max_cards = nightly_plan.max_cards
        _early_stop_threshold: Optional[int] = nightly_plan.early_stop_threshold
        logger.info(
            f"Day-by-day pipeline (nightly): {total_nights} nights, "
            f"observing {len(sample_indices)} / inferring {len(nightly_plan.infer_indices)} "
            f"(scroll_rounds={_eff_scroll_rounds}, max_cards={_eff_max_cards})"
        )
    else:
        if total_nights <= SAMPLE_THRESHOLD:
            sample_indices = list(range(total_nights))
        else:
            sample_indices = compute_sample_dates(total_nights, MAX_SAMPLE_QUERIES)
        _eff_scroll_rounds = max_scroll_rounds
        _eff_max_cards = max_cards
        _early_stop_threshold = None
        logger.info(
            f"Day-by-day pipeline: {total_nights} nights, "
            f"querying {len(sample_indices)} days (sampling={'yes' if len(sample_indices) < total_nights else 'no'})"
        )

    # CDP check
    cdp_ok, cdp_reason = check_cdp_endpoint(cdp_url)
    if not cdp_ok:
        return [], _empty_transparent("scrape", f"CDP unavailable: {cdp_reason}")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            cdp_url,
            timeout=cdp_connect_timeout_ms,
        )
        context = browser.new_context(
            locale="en-US",
            timezone_id="America/Los_Angeles",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = context.new_page()

        try:
            # Step 1: Extract target listing spec (with one retry on degraded pages)
            logger.info(f"Extracting target: {listing_url}")
            extract_start = time.time()
            target, warnings = extract_target_spec(page, listing_url)
            extraction_warnings.extend(warnings)
            timings["extract_ms"] = round((time.time() - extract_start) * 1000)
            logger.info(
                f"[run_scrape] Target spec (raw): type={target.property_type!r} "
                f"bedrooms={target.bedrooms} accommodates={target.accommodates} "
                f"baths={target.baths} beds={target.beds} "
                f"location={target.location!r}"
            )

            # Degraded-page detection + one retry.
            # If the page returned suspiciously incomplete fields (e.g. bot-challenge
            # redirect or cached fragment), wait 2 s and extract once more before
            # falling back to saved attributes.  The retry is lightweight — it only
            # re-navigates to the same URL and re-parses; it does not re-open a new
            # browser context.
            _spec_retry_attempted = False
            _spec_retry_improved = False
            _spec_degraded_page_suspected = _is_spec_degraded(target)
            if _spec_degraded_page_suspected:
                logger.warning(
                    f"[run_scrape] Degraded page suspected "
                    f"(location={target.location!r}, bedrooms={target.bedrooms}, "
                    f"accommodates={target.accommodates}, baths={target.baths}, "
                    f"type={target.property_type!r}) — retrying extraction in 2s"
                )
                extraction_warnings.append("Degraded page suspected; retrying spec extraction")
                time.sleep(2)
                _spec_retry_attempted = True
                _retry_start = time.time()
                target_retry, retry_warnings = extract_target_spec(page, listing_url)
                timings["extract_retry_ms"] = round((time.time() - _retry_start) * 1000)
                extraction_warnings.extend(retry_warnings)
                if not _is_spec_degraded(target_retry):
                    target = target_retry
                    _spec_retry_improved = True
                    logger.info("[run_scrape] Retry resolved degraded spec")
                else:
                    # Prefer the retry result if it has more fields even if still degraded
                    _orig_missing = sum([
                        not target.location, target.bedrooms is None,
                        target.accommodates is None, target.baths is None,
                        not target.property_type,
                    ])
                    _retry_missing = sum([
                        not target_retry.location, target_retry.bedrooms is None,
                        target_retry.accommodates is None, target_retry.baths is None,
                        not target_retry.property_type,
                    ])
                    if _retry_missing < _orig_missing:
                        target = target_retry
                        _spec_retry_improved = True
                        logger.info(
                            f"[run_scrape] Retry partially improved spec "
                            f"(missing fields: {_orig_missing} → {_retry_missing})"
                        )
                    else:
                        logger.warning("[run_scrape] Retry did not improve degraded spec")

            # Step 1a: Backfill missing target spec fields from saved listing attributes.
            # Resolves the failure mode where Airbnb returns a degraded page that omits
            # spec fields, causing all comps to score 0.35 (below the 0.40 floor) and
            # the wrong effective_adults for the market search (None → default 2).
            _spec_backfill_meta: Optional[Dict[str, Any]] = None
            if fallback_attributes:
                target, _spec_backfill_meta = _backfill_target_spec(target, fallback_attributes)
                if _spec_backfill_meta["fields_filled"]:
                    extraction_warnings.append(
                        f"Target spec backfilled from saved attributes: "
                        f"filled={_spec_backfill_meta['fields_filled']}, "
                        f"still_missing={_spec_backfill_meta['fields_still_missing']}"
                    )
                    logger.info(
                        f"[run_scrape] Target spec after backfill: type={target.property_type!r} "
                        f"bedrooms={target.bedrooms} accommodates={target.accommodates} "
                        f"baths={target.baths} beds={target.beds} "
                        f"(filled: {_spec_backfill_meta['fields_filled']})"
                    )
                elif _spec_backfill_meta["is_partial"]:
                    logger.warning(
                        f"[run_scrape] Target spec incomplete after extraction + fallback: "
                        f"still_missing={_spec_backfill_meta['fields_still_missing']}"
                    )

            # Build spec extraction telemetry — recorded in transparent result debug section.
            _spec_still_partial = bool(
                not target.property_type or target.accommodates is None
                or target.bedrooms is None or target.baths is None
            )
            _spec_location_source = "page"
            _spec_confidence: str
            missing_after = sum([
                not bool(target.location and target.location.strip()),
                not target.property_type,
                target.accommodates is None,
                target.bedrooms is None,
                target.baths is None,
            ])
            if missing_after == 0:
                _spec_confidence = "high"
            elif missing_after <= 1:
                _spec_confidence = "medium"
            else:
                _spec_confidence = "low"
            _spec_extraction_meta: Dict[str, Any] = {
                "retryAttempted": _spec_retry_attempted,
                "retryImproved": _spec_retry_improved,
                "degradedPageSuspected": _spec_degraded_page_suspected,
                "locationSource": _spec_location_source,
                "specConfidence": _spec_confidence,
                "stillPartial": _spec_still_partial,
            }

            # Step 1b: Capture date-aware target price using a SHORT window (non-fatal).
            # Root cause of prior failures: the full 30-day checkout was passed here,
            # causing Airbnb to render a long-stay/monthly widget that never shows
            # a standard "/night" label — all three extraction layers returned None.
            # Fix: always use a 1-night window (checkin → checkin+1) which gives the
            # standard per-night booking widget.  Fall back to 2-night if 1-night fails
            # (handles listings with a 2-night minimum stay).
            _target_price_confidence: Optional[str] = None
            try:
                from worker.scraper.target_extractor import extract_nightly_price_from_listing_page
                _checkin_dt = dt.strptime(checkin, "%Y-%m-%d")
                _checkout_1n = (_checkin_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                _checkout_2n = (_checkin_dt + timedelta(days=2)).strftime("%Y-%m-%d")

                logger.info(
                    f"[run_scrape] Capturing target price: 1-night window "
                    f"{checkin} → {_checkout_1n}"
                )
                _tp, _tp_conf = extract_nightly_price_from_listing_page(
                    page, listing_url, checkin, _checkout_1n
                )

                # If 1-night returns nothing, try 2-night (min-stay=2 listings hide
                # the booking widget for 1-night requests).
                if _tp is None and _tp_conf != "scrape_failed":
                    logger.info(
                        f"[run_scrape] 1-night target price None "
                        f"(confidence={_tp_conf}), retrying with 2-night window"
                    )
                    _tp, _tp_conf = extract_nightly_price_from_listing_page(
                        page, listing_url, checkin, _checkout_2n
                    )

                if _tp is not None:
                    target.nightly_price = _tp
                    _target_price_confidence = _tp_conf
                    logger.info(
                        f"[run_scrape] Target price captured: ${_tp} "
                        f"(confidence={_tp_conf})"
                    )
                else:
                    logger.warning(
                        f"[run_scrape] Target price capture returned None after 1+2 night "
                        f"attempts (final confidence={_tp_conf})"
                    )
                    _target_price_confidence = _tp_conf
            except Exception as _tp_exc:
                logger.warning(f"[run_scrape] Target price capture failed (non-fatal): {_tp_exc}")

            # Phase 3B: coordinate priority — page-extracted > geocoded > none.
            # extract_target_spec() may populate target.lat/lng from JSON-LD geo.
            # Only fall back to geocoded coords when the page gave us nothing.
            if target.lat is None or target.lng is None:
                if target_lat is not None and target_lng is not None:
                    target.lat = target_lat
                    target.lng = target_lng
                    logger.debug("[run_scrape] Using geocoded target coords (page gave none)")
            else:
                logger.info(
                    f"[run_scrape] Using page-extracted target coords "
                    f"({target.lat:.5f}, {target.lng:.5f})"
                )

            # Resolve adaptive radius — use caller-supplied value or fall back to default
            _effective_radius = max_radius_km if max_radius_km is not None else DEFAULT_MAX_RADIUS_KM

            if not target.location:
                # Try "... in City, State" pattern first
                loc_m = re.search(
                    r"\bin\s+([A-Z][a-zA-Z\s,]+(?:,\s*[A-Z][a-zA-Z\s]+)?)",
                    target.title,
                )
                if loc_m:
                    target.location = loc_m.group(1).strip().rstrip(",.")
                    _spec_location_source = "title"
                else:
                    # Fallback: last meaningful token from title delimiters
                    tokens = [
                        t.strip()
                        for t in re.split(r"[-|•·]", target.title)
                        if t.strip() and len(t.strip()) >= 3
                    ]
                    target.location = tokens[-1] if tokens else ""
                    if target.location:
                        _spec_location_source = "title"
                extraction_warnings.append(f"Location fallback from title: '{target.location}'")
                logger.warning(f"Location fallback from title: '{target.location}'")

            # Last-resort: use the saved property address when title fallback also failed.
            if not target.location and fallback_address:
                loc_from_addr, _addr_conf = _extract_search_location(fallback_address)
                if loc_from_addr:
                    target.location = loc_from_addr
                    _spec_location_source = "saved_address"
                    extraction_warnings.append(
                        f"Location fallback from saved address: '{target.location}' "
                        f"(confidence={_addr_conf})"
                    )
                    logger.warning(
                        f"[run_scrape] Location fallback from saved address: "
                        f"'{target.location}' (confidence={_addr_conf})"
                    )

            # Finalize telemetry after all location fallbacks have run.
            # specConfidence and stillPartial are recomputed here so they reflect
            # the final post-fallback state (title or saved-address recovery raises
            # confidence from "low" to "medium"/"high" when location is now resolved).
            _spec_extraction_meta["locationSource"] = _spec_location_source
            _final_missing = sum([
                not bool(target.location and target.location.strip()),
                not target.property_type,
                target.accommodates is None,
                target.bedrooms is None,
                target.baths is None,
            ])
            _spec_extraction_meta["specConfidence"] = (
                "high" if _final_missing == 0
                else "medium" if _final_missing <= 1
                else "low"
            )
            _spec_extraction_meta["stillPartial"] = bool(
                not target.property_type or target.accommodates is None
                or target.bedrooms is None or target.baths is None
            )

            if not target.location:
                return [], {
                    "targetSpec": {
                        "title": target.title,
                        "location": "",
                        "city": target.city or "",
                        "state": target.state or "",
                        "postalCode": target.postal_code or "",
                        "country": target.country or "",
                        "countryCode": target.country_code or "",
                        "lat": target.lat,
                        "lng": target.lng,
                        "propertyType": target.property_type,
                        "accommodates": target.accommodates,
                        "bedrooms": target.bedrooms,
                        "beds": target.beds,
                        "baths": target.baths,
                        "amenities": target.amenities,
                        "rating": target.rating,
                        "reviews": target.reviews,
                    },
                    "queryCriteria": None,
                    "compsSummary": None,
                    "priceDistribution": None,
                    "recommendedPrice": None,
                    "comparableListings": None,
                    "debug": {
                        "source": "scrape",
                        "error": "Cannot determine location from listing page.",
                        "extractionWarnings": extraction_warnings,
                        "timingsMs": timings,
                        "specExtractionMeta": _spec_extraction_meta,
                    },
                }

            # Use target listing capacity for search alignment
            effective_adults = adults
            if target.accommodates and target.accommodates > 0:
                effective_adults = min(int(target.accommodates), 16)

            query_criteria = {
                "locationBasis": target.location,
                "searchAdults": effective_adults,
                "checkin": checkin,
                "checkout": checkout,
                "totalNights": total_nights,
                "sampledNights": len(sample_indices),
                "queryMode": "day_by_day",
                "propertyTypeFilter": target.property_type or None,
            }

            # Step 2: Day-by-day 1-night queries
            from worker.scraper.day_query import DayResult

            sampled_results: List[DayResult] = []
            _queried_night_indices: List[int] = []
            day_loop_start = time.time()
            _consecutive_empty = 0
            _consecutive_empty_peak = 0
            _early_stop_triggered = False

            for idx_pos, night_idx in enumerate(sample_indices):
                # Check global timeout
                elapsed = time.time() - start_time
                remaining = max_runtime_seconds - elapsed
                if remaining < 15:
                    logger.warning(
                        f"Global timeout approaching ({remaining:.0f}s left), "
                        f"stopping after {idx_pos}/{len(sample_indices)} day-queries"
                    )
                    break

                date_i = all_nights[night_idx]

                # Retry logic
                result: Optional[DayResult] = None
                for attempt in range(1, PER_DAY_MAX_RETRIES + 1):
                    time.sleep(rate_limit_seconds)
                    result = estimate_base_price_for_date(
                        page,
                        target,
                        base_origin,
                        date_i,
                        effective_adults,
                        max_scroll_rounds=_eff_scroll_rounds,
                        max_cards=_eff_max_cards,
                        rate_limit_seconds=rate_limit_seconds,
                        top_k=top_k,
                        preferred_comps=preferred_comps,
                        max_radius_km=_effective_radius,
                    )
                    if result.median_price is not None:
                        break
                    if attempt < PER_DAY_MAX_RETRIES:
                        logger.info(
                            f"[day_query] {date_i.isoformat()}: retry {attempt+1}/{PER_DAY_MAX_RETRIES}"
                        )

                if result is not None:
                    sampled_results.append(result)
                    _queried_night_indices.append(night_idx)
                    if result.median_price is None:
                        _consecutive_empty += 1
                        _consecutive_empty_peak = max(_consecutive_empty_peak, _consecutive_empty)
                    else:
                        _consecutive_empty = 0

                # Circuit-breaker: nightly only — stop if too many consecutive empties.
                # Preserves already-collected observations; unqueried dates are interpolated.
                if (
                    _early_stop_threshold is not None
                    and _consecutive_empty >= _early_stop_threshold
                ):
                    logger.warning(
                        f"[nightly] Circuit-breaker: {_consecutive_empty} consecutive empty results "
                        f"at day {idx_pos + 1}/{len(sample_indices)} — stopping deeper crawl"
                    )
                    _early_stop_triggered = True
                    break

                if progress_callback is not None:
                    try:
                        progress_callback(idx_pos + 1, len(sample_indices))
                    except Exception:
                        pass

            timings["day_queries_ms"] = round((time.time() - day_loop_start) * 1000)
            # Record nightly crawl metadata for debug visibility.
            if nightly_plan is not None:
                # Compute actual observed/inferred from execution tracking, not from the
                # original plan.  If early-stop fired, unqueried planned dates must not
                # be counted as observed or as the plan's original infer set.
                _actual_observed = [
                    _queried_night_indices[i]
                    for i, r in enumerate(sampled_results)
                    if r.median_price is not None
                ]
                _actual_inferred = sorted(
                    set(range(nightly_plan.total_nights)) - set(_actual_observed)
                )
                timings["nightly_crawl_debug"] = {
                    "total_nights": nightly_plan.total_nights,
                    "observed_count": len(_actual_observed),
                    "queried_count": len(_queried_night_indices),
                    "infer_count": len(_actual_inferred),
                    "early_stop_triggered": _early_stop_triggered,
                    "consecutive_empty_peak": _consecutive_empty_peak,
                    "tiers": nightly_plan.tier_debug,
                    "planned_observe_indices": nightly_plan.observe_indices,
                    "actual_queried_indices": _queried_night_indices,
                    "actual_observed_indices": _actual_observed,
                    "actual_inferred_indices": _actual_inferred,
                    "scroll_rounds": nightly_plan.scroll_rounds,
                    "max_cards": nightly_plan.max_cards,
                }

            # Step 3: Interpolate unsampled/failed days
            interp_start = time.time()
            all_day_results_obj = interpolate_missing_days(sampled_results, all_nights)
            timings["interpolation_ms"] = round((time.time() - interp_start) * 1000)

            # Convert DayResult objects to dicts
            all_day_results: List[Dict[str, Any]] = []
            for dr in all_day_results_obj:
                all_day_results.append({
                    "date": dr.date,
                    "median_price": dr.median_price,
                    "comps_collected": dr.comps_collected,
                    "comps_used": dr.comps_used,
                    "below_similarity_floor": dr.below_similarity_floor,
                    "price_outliers_excluded": dr.price_outliers_excluded,
                    "price_outliers_downweighted": dr.price_outliers_downweighted,
                    "geo_excluded": dr.geo_excluded,
                    "price_band_excluded": dr.price_band_excluded,
                    "filter_stage": dr.filter_stage,
                    "flags": dr.flags,
                    "is_sampled": dr.is_sampled,
                    "is_weekend": dr.is_weekend,
                    "price_distribution": dr.price_distribution,
                    "top_comps": dr.top_comps,
                    "comp_prices": dr.comp_prices,
                    "error": dr.error,
                    "selection_mode": dr.selection_mode,
                    "pricing_confidence": dr.pricing_confidence,
                })

            # Step 4: Discount evidence (debug only, if time permits)
            discount_evidence = None
            elapsed = time.time() - start_time
            if elapsed < max_runtime_seconds - 20 and total_nights > 1:
                try:
                    discount_evidence = detect_discount_evidence(
                        page, base_origin, target, checkin, checkout,
                        effective_adults, rate_limit_seconds=rate_limit_seconds,
                    )
                except Exception as exc:
                    logger.warning(f"Discount evidence query failed: {exc}")

            timings["total_ms"] = round((time.time() - start_time) * 1000)

            transparent = _build_daily_transparent_result(
                target=target,
                query_criteria=query_criteria,
                all_day_results=all_day_results,
                timings_ms=timings,
                source="scrape",
                extraction_warnings=extraction_warnings,
                discount_evidence=discount_evidence,
                benchmark_info=_build_url_mode_benchmark_info(
                    all_day_results,
                    preferred_comps,
                ),
                target_price_confidence=_target_price_confidence,
                spec_backfill=_spec_backfill_meta,
                spec_extraction_meta=_spec_extraction_meta,
            )
            # Phase 3B: surface page-extracted coords so main.py can write them back to DB
            if target.lat is not None and target.lng is not None:
                _coord_source = (
                    "page" if (target.lat != target_lat or target.lng != target_lng
                               or target_lat is None)
                    else "geocoded"
                )
                transparent["pageExtractedCoords"] = {
                    "lat": target.lat,
                    "lng": target.lng,
                    "source": _coord_source,
                }
            _repair_incomplete_comparable_specs(page, transparent, extraction_warnings)
            _repair_suspicious_comparable_titles(page, transparent, extraction_warnings)

            logger.info(
                f"Day-by-day pipeline complete: {len(sample_indices)} queries, "
                f"{sum(1 for r in all_day_results if r['median_price'] is not None)} valid prices, "
                f"{timings['total_ms']}ms total"
            )

            return all_day_results, transparent

        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Benchmark-first scrape pipeline
# ---------------------------------------------------------------------------


def run_benchmark_scrape(
    benchmark_url: str,
    checkin: str,
    checkout: str,
    cdp_url: str = "http://127.0.0.1:9222",
    adults: int = 2,
    max_scroll_rounds: int = 12,
    max_cards: int = 80,
    max_runtime_seconds: int = 180,
    rate_limit_seconds: float = 1.0,
    cdp_connect_timeout_ms: int = 15000,
    target_spec_override: Optional[ListingSpec] = None,
    secondary_benchmark_urls: Optional[List[str]] = None,
    user_attributes: Optional[Dict[str, Any]] = None,
    target_lat: Optional[float] = None,
    target_lng: Optional[float] = None,
    max_radius_km: Optional[float] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    nightly_plan: Optional[Any] = None,
    fallback_address: Optional[str] = None,
) -> tuple:
    """
    Benchmark-first pipeline.

    Uses the pinned comp (benchmark_url) as the primary pricing anchor.
    Market comps from search are used only for a capped adjustment.

    Returns (daily_results, transparent_result).
    Fallback: if benchmark price fetch fails entirely, raises ValueError
    so the caller can fall back to the standard run_scrape pipeline.

    nightly_plan: when provided (nightly jobs only), overrides sample indices
    with the tiered nightly plan and applies reduced per-query limits and
    circuit-breaker early-stop.
    """
    from playwright.sync_api import sync_playwright
    from worker.core.benchmark import (
        BENCHMARK_MAX_SAMPLE_QUERIES,
        BenchmarkDayResult,
        aggregate_benchmark_transparency,
        benchmark_day_result_to_dict,
        probe_benchmark_discounts,  # 記得 import 新函式
        estimate_benchmark_price_for_date,
    )

    benchmark_url = normalize_airbnb_url(benchmark_url)
    if secondary_benchmark_urls:
        secondary_benchmark_urls = [normalize_airbnb_url(u) for u in secondary_benchmark_urls]

    start_time = time.time()
    timings: Dict[str, int] = {}
    extraction_warnings: List[str] = []
    base_origin = safe_domain_base(benchmark_url)

    d_start = dt.strptime(checkin, "%Y-%m-%d").date()
    d_end = dt.strptime(checkout, "%Y-%m-%d").date()
    total_nights = (d_end - d_start).days
    if total_nights < 1:
        return [], _empty_transparent("benchmark", "Invalid date range")
    if total_nights > MAX_NIGHTS:
        raise ValueError(
            f"Date range of {total_nights} nights exceeds maximum of {MAX_NIGHTS}."
        )

    all_nights = daterange_nights(d_start, d_end)

    # Nightly jobs use the tiered plan; benchmark interactive uses standard sampling.
    if nightly_plan is not None:
        sample_indices = nightly_plan.observe_indices
        _bm_eff_scroll_rounds = nightly_plan.scroll_rounds
        _bm_eff_max_cards = nightly_plan.max_cards
        _bm_early_stop_threshold: Optional[int] = nightly_plan.early_stop_threshold
        logger.info(
            f"Benchmark pipeline (nightly): {benchmark_url} | {total_nights} nights, "
            f"observing {len(sample_indices)} / inferring {len(nightly_plan.infer_indices)}"
        )
    else:
        if total_nights <= SAMPLE_THRESHOLD:
            sample_indices = list(range(total_nights))
        else:
            sample_indices = compute_sample_dates(total_nights, BENCHMARK_MAX_SAMPLE_QUERIES)
        from worker.core.benchmark import BENCHMARK_SCROLL_ROUNDS as _bm_eff_scroll_rounds, BENCHMARK_MAX_CARDS as _bm_eff_max_cards  # noqa: E501
        _bm_early_stop_threshold = None
        logger.info(
            f"Benchmark pipeline: {benchmark_url} | {total_nights} nights, "
            f"querying {len(sample_indices)} days"
        )

    cdp_ok, cdp_reason = check_cdp_endpoint(cdp_url)
    if not cdp_ok:
        return [], _empty_transparent("benchmark", f"CDP unavailable: {cdp_reason}")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url, timeout=cdp_connect_timeout_ms)
        context = browser.new_context(
            locale="en-US",
            timezone_id="America/Los_Angeles",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = context.new_page()

        # ── Step 0: Probe Discounts (Strategy B) ────────────────────────
        # 在開始逐日抓取前，先對 Benchmark 進行「定價策略探測」
        # 這會額外花費約 3-5 秒，但能大幅提升準確度
        discount_info = {}
        try:
            discount_info = probe_benchmark_discounts(page, benchmark_url, base_origin, d_start)
        except Exception as e:
            logger.warning(f"[benchmark] Discount probe failed: {e}")
        # ────────────────────────────────────────────────────────────────

        try:
            # Step 1: Extract benchmark listing spec (location, capacity, etc.)
            extract_start = time.time()
            _bm_spec_extraction_meta: Optional[Dict[str, Any]] = None
            _bm_backfill_meta: Optional[Dict[str, Any]] = None
            if target_spec_override is not None:
                target = target_spec_override
            else:
                logger.info(f"[benchmark] Extracting spec from: {benchmark_url}")
                target, warnings = extract_target_spec(page, benchmark_url)
                extraction_warnings.extend(warnings)
                logger.info(
                    f"[benchmark] Target spec (raw): type={target.property_type!r} "
                    f"bedrooms={target.bedrooms} accommodates={target.accommodates} "
                    f"baths={target.baths} location={target.location!r}"
                )

                # Degraded-page detection + one retry (mirrors run_scrape).
                _bm_retry_attempted = False
                _bm_retry_improved = False
                _bm_degraded_suspected = _is_spec_degraded(target)
                if _bm_degraded_suspected:
                    logger.warning(
                        f"[benchmark] Degraded page suspected — retrying spec extraction in 2s"
                    )
                    extraction_warnings.append(
                        "[benchmark] Degraded page suspected; retrying spec extraction"
                    )
                    time.sleep(2)
                    _bm_retry_attempted = True
                    target_bm_retry, bm_retry_warnings = extract_target_spec(page, benchmark_url)
                    extraction_warnings.extend(bm_retry_warnings)
                    if not _is_spec_degraded(target_bm_retry):
                        target = target_bm_retry
                        _bm_retry_improved = True
                        logger.info("[benchmark] Retry resolved degraded spec")
                    else:
                        _bm_orig_missing = sum([
                            not target.location, target.bedrooms is None,
                            target.accommodates is None, target.baths is None,
                            not target.property_type,
                        ])
                        _bm_retry_missing = sum([
                            not target_bm_retry.location, target_bm_retry.bedrooms is None,
                            target_bm_retry.accommodates is None, target_bm_retry.baths is None,
                            not target_bm_retry.property_type,
                        ])
                        if _bm_retry_missing < _bm_orig_missing:
                            target = target_bm_retry
                            _bm_retry_improved = True
                            logger.info(
                                f"[benchmark] Retry partially improved spec "
                                f"(missing: {_bm_orig_missing} → {_bm_retry_missing})"
                            )
                        else:
                            logger.warning("[benchmark] Retry did not improve degraded spec")

                # Backfill from user_attributes when structural fields are still missing.
                _bm_backfill_meta: Optional[Dict[str, Any]] = None
                if user_attributes:
                    target, _bm_backfill_meta = _backfill_target_spec(target, user_attributes)
                    if _bm_backfill_meta["fields_filled"]:
                        extraction_warnings.append(
                            f"[benchmark] Target spec backfilled from user attributes: "
                            f"filled={_bm_backfill_meta['fields_filled']}, "
                            f"still_missing={_bm_backfill_meta['fields_still_missing']}"
                        )
                        logger.info(
                            f"[benchmark] Target spec after backfill: type={target.property_type!r} "
                            f"bedrooms={target.bedrooms} accommodates={target.accommodates} "
                            f"(filled: {_bm_backfill_meta['fields_filled']})"
                        )

                # Location fallback (mirrors run_scrape)
                _bm_location_source = "page"
                if not target.location:
                    loc_m = re.search(
                        r"\bin\s+([A-Z][a-zA-Z\s,]+(?:,\s*[A-Z][a-zA-Z\s]+)?)",
                        target.title,
                    )
                    if loc_m:
                        target.location = loc_m.group(1).strip().rstrip(",.")
                        _bm_location_source = "title"
                    else:
                        tokens = [
                            t.strip()
                            for t in re.split(r"[-|•·]", target.title)
                            if t.strip() and len(t.strip()) >= 3
                        ]
                        target.location = tokens[-1] if tokens else ""
                        if target.location:
                            _bm_location_source = "title"
                    extraction_warnings.append(
                        f"[benchmark] Location fallback from title: '{target.location}'"
                    )

                # Last-resort: saved property address (mirrors run_scrape).
                if not target.location and fallback_address:
                    loc_from_addr, _bm_addr_conf = _extract_search_location(fallback_address)
                    if loc_from_addr:
                        target.location = loc_from_addr
                        _bm_location_source = "saved_address"
                        extraction_warnings.append(
                            f"[benchmark] Location fallback from saved address: "
                            f"'{target.location}' (confidence={_bm_addr_conf})"
                        )
                        logger.warning(
                            f"[benchmark] Location fallback from saved address: "
                            f"'{target.location}' (confidence={_bm_addr_conf})"
                        )

                # Build benchmark spec extraction telemetry after all fallbacks so
                # specConfidence / stillPartial / locationSource reflect final state.
                _bm_missing_after = sum([
                    not bool(target.location and target.location.strip()),
                    not target.property_type,
                    target.accommodates is None,
                    target.bedrooms is None,
                    target.baths is None,
                ])
                _bm_spec_extraction_meta: Dict[str, Any] = {
                    "retryAttempted": _bm_retry_attempted,
                    "retryImproved": _bm_retry_improved,
                    "degradedPageSuspected": _bm_degraded_suspected,
                    "locationSource": _bm_location_source,
                    "specConfidence": (
                        "high" if _bm_missing_after == 0
                        else "medium" if _bm_missing_after <= 1
                        else "low"
                    ),
                    "stillPartial": bool(
                        not target.property_type or target.accommodates is None
                        or target.bedrooms is None or target.baths is None
                    ),
                }

                if not target.location:
                    return [], _empty_transparent(
                        "benchmark", "Cannot determine location from benchmark listing page."
                    )

            timings["extract_ms"] = round((time.time() - extract_start) * 1000)

            # Phase 3B: coordinate priority — page-extracted > geocoded > none.
            if target.lat is None or target.lng is None:
                if target_lat is not None and target_lng is not None:
                    target.lat = target_lat
                    target.lng = target_lng

            # Resolve adaptive radius
            _effective_radius = max_radius_km if max_radius_km is not None else DEFAULT_MAX_RADIUS_KM

            # ── Benchmark-to-target similarity (computed once per job) ────────
            # Compare the benchmark listing's extracted spec against the user's
            # stated property attributes.  The resulting similarity score is
            # passed to each day query so it can reduce effective_weight when
            # the benchmark is a poor structural match for the target property.
            from worker.core.benchmark import (
                _BM_SIMILARITY_HIGH_MATCH,
                _BM_SIMILARITY_STRONG_MISMATCH,
            )
            from worker.core.similarity import similarity_score as _similarity_score

            bm_target_similarity: float = 1.0   # default: no penalty
            bm_mismatch_level: str = "unknown"

            if user_attributes:
                user_spec = ListingSpec(
                    url="",
                    bedrooms=user_attributes.get("bedrooms"),
                    baths=user_attributes.get("bathrooms"),
                    accommodates=user_attributes.get("maxGuests"),
                    property_type=user_attributes.get("propertyType", ""),
                )
                bm_target_similarity = round(_similarity_score(user_spec, target), 3)
                if bm_target_similarity >= _BM_SIMILARITY_HIGH_MATCH:
                    bm_mismatch_level = "high_match"
                elif bm_target_similarity >= _BM_SIMILARITY_STRONG_MISMATCH:
                    bm_mismatch_level = "moderate_mismatch"
                else:
                    bm_mismatch_level = "strong_mismatch"

                if bm_mismatch_level != "high_match":
                    logger.warning(
                        f"[benchmark] Benchmark-to-target similarity={bm_target_similarity:.3f} "
                        f"({bm_mismatch_level}) — benchmark may be a poor anchor for this property"
                    )
                else:
                    logger.info(
                        f"[benchmark] Benchmark-to-target similarity={bm_target_similarity:.3f} "
                        f"({bm_mismatch_level})"
                    )

            effective_adults = adults
            if target.accommodates and target.accommodates > 0:
                effective_adults = min(int(target.accommodates), 16)

            query_criteria = {
                "locationBasis": target.location,
                "searchAdults": effective_adults,
                "checkin": checkin,
                "checkout": checkout,
                "totalNights": total_nights,
                "sampledNights": len(sample_indices),
                "queryMode": "benchmark_first",
                "benchmarkUrl": benchmark_url,
            }

            # Step 2: Benchmark day-by-day queries
            from worker.core.benchmark import BENCHMARK_TOP_K
            sampled_results: List[BenchmarkDayResult] = []
            _bm_queried_night_indices: List[int] = []
            day_loop_start = time.time()
            _bm_consecutive_empty = 0
            _bm_consecutive_empty_peak = 0
            _bm_early_stop_triggered = False

            for idx_pos, night_idx in enumerate(sample_indices):
                elapsed = time.time() - start_time
                remaining = max_runtime_seconds - elapsed
                if remaining < 15:
                    logger.warning(
                        f"[benchmark] Timeout approaching, stopping after "
                        f"{idx_pos}/{len(sample_indices)} queries"
                    )
                    break

                date_i = all_nights[night_idx]
                time.sleep(rate_limit_seconds)
                result = estimate_benchmark_price_for_date(
                    page,
                    target,
                    benchmark_url,
                    base_origin,
                    date_i,
                    effective_adults,
                    secondary_benchmark_urls=secondary_benchmark_urls or [],
                    benchmark_target_similarity=bm_target_similarity,
                    max_scroll_rounds=_bm_eff_scroll_rounds,
                    max_cards=_bm_eff_max_cards,
                    rate_limit_seconds=rate_limit_seconds,
                    top_k=BENCHMARK_TOP_K,
                    max_radius_km=_effective_radius,
                )
                sampled_results.append(result)
                _bm_queried_night_indices.append(night_idx)

                if result.median_price is None:
                    _bm_consecutive_empty += 1
                    _bm_consecutive_empty_peak = max(_bm_consecutive_empty_peak, _bm_consecutive_empty)
                else:
                    _bm_consecutive_empty = 0

                if (
                    _bm_early_stop_threshold is not None
                    and _bm_consecutive_empty >= _bm_early_stop_threshold
                ):
                    logger.warning(
                        f"[nightly/benchmark] Circuit-breaker: {_bm_consecutive_empty} consecutive "
                        f"empty results at day {idx_pos + 1}/{len(sample_indices)} — stopping"
                    )
                    _bm_early_stop_triggered = True
                    break

                if progress_callback is not None:
                    try:
                        progress_callback(idx_pos + 1, len(sample_indices))
                    except Exception:
                        pass

            timings["day_queries_ms"] = round((time.time() - day_loop_start) * 1000)
            if nightly_plan is not None:
                _bm_actual_observed = [
                    _bm_queried_night_indices[i]
                    for i, r in enumerate(sampled_results)
                    if r.median_price is not None
                ]
                _bm_actual_inferred = sorted(
                    set(range(nightly_plan.total_nights)) - set(_bm_actual_observed)
                )
                timings["nightly_crawl_debug"] = {
                    "total_nights": nightly_plan.total_nights,
                    "observed_count": len(_bm_actual_observed),
                    "queried_count": len(_bm_queried_night_indices),
                    "infer_count": len(_bm_actual_inferred),
                    "early_stop_triggered": _bm_early_stop_triggered,
                    "consecutive_empty_peak": _bm_consecutive_empty_peak,
                    "tiers": nightly_plan.tier_debug,
                    "planned_observe_indices": nightly_plan.observe_indices,
                    "actual_queried_indices": _bm_queried_night_indices,
                    "actual_observed_indices": _bm_actual_observed,
                    "actual_inferred_indices": _bm_actual_inferred,
                    "scroll_rounds": nightly_plan.scroll_rounds,
                    "max_cards": nightly_plan.max_cards,
                }

            # Step 3: Interpolate
            # Reuse standard interpolation — BenchmarkDayResult.median_price is the blended price
            from worker.scraper.day_query import DayResult, interpolate_missing_days

            def _to_day_result(r: BenchmarkDayResult) -> DayResult:
                return DayResult(
                    date=r.date,
                    median_price=r.median_price,
                    comps_collected=r.comps_collected,
                    comps_used=r.comps_used,
                    filter_stage=r.filter_stage,
                    flags=r.flags,
                    is_sampled=r.is_sampled,
                    is_weekend=r.is_weekend,
                    price_distribution=r.price_distribution,
                    top_comps=r.top_comps,
                    comp_prices=r.comp_prices,
                    error=r.error,
                )

            interp_start = time.time()
            all_day_objs = interpolate_missing_days(
                [_to_day_result(r) for r in sampled_results], all_nights
            )
            timings["interpolation_ms"] = round((time.time() - interp_start) * 1000)

            # Convert to dicts for the standard pipeline
            all_day_results: List[Dict[str, Any]] = []
            for dr in all_day_objs:
                all_day_results.append({
                    "date": dr.date,
                    "median_price": dr.median_price,
                    "comps_collected": dr.comps_collected,
                    "comps_used": dr.comps_used,
                    "below_similarity_floor": dr.below_similarity_floor,
                    "price_outliers_excluded": dr.price_outliers_excluded,
                    "price_outliers_downweighted": dr.price_outliers_downweighted,
                    "geo_excluded": dr.geo_excluded,
                    "price_band_excluded": dr.price_band_excluded,
                    "filter_stage": dr.filter_stage,
                    "flags": dr.flags,
                    "is_sampled": dr.is_sampled,
                    "is_weekend": dr.is_weekend,
                    "price_distribution": dr.price_distribution,
                    "top_comps": dr.top_comps,
                    "comp_prices": dr.comp_prices,
                    "error": dr.error,
                    "selection_mode": dr.selection_mode,
                    "pricing_confidence": dr.pricing_confidence,
                })

            timings["total_ms"] = round((time.time() - start_time) * 1000)

            # ── Guarantee secondary benchmarks appear in comparableListings ────
            # Secondary benchmark listings may never surface in Airbnb search
            # results (different area, unavailable, outside search radius).
            # For each secondary URL that never appeared in ANY day's top_comps,
            # inject a synthetic entry into the first sampled day using the
            # average price collected via secondary_prices across all days.
            if secondary_benchmark_urls and all_day_results:
                from worker.core.benchmark import _ROOM_ID_RE as _BM_ROOM_ID_RE
                from worker.core.similarity import comp_urls_match as _cmu

                # Collect IDs of secondary comps already seen in top_comps
                seen_sec_ids: set = set()
                for day_dict in all_day_results:
                    for tc in (day_dict.get("top_comps") or []):
                        if tc.get("isPinnedBenchmark") and tc.get("url"):
                            m = _BM_ROOM_ID_RE.search(tc["url"])
                            if m:
                                seen_sec_ids.add(m.group(1))

                for sec_url in secondary_benchmark_urls:
                    sec_m = _BM_ROOM_ID_RE.search(sec_url)
                    sec_id = sec_m.group(1) if sec_m else sec_url
                    if sec_id in seen_sec_ids:
                        continue  # already present in at least one day

                    # Collect avg price from secondary_comp_prices across all sampled days
                    sec_prices_found = [
                        r.secondary_comp_prices.get(sec_url)
                        for r in sampled_results
                        if r.secondary_comp_prices.get(sec_url) is not None
                    ]
                    avg_sec_price = (
                        round(sum(sec_prices_found) / len(sec_prices_found), 2)
                        if sec_prices_found else None
                    )

                    if avg_sec_price is None:
                        # Truly zero data — skip; we have nothing to show
                        logger.info(
                            f"[benchmark] Secondary {sec_url}: never found in search — omitting from comps"
                        )
                        continue

                    # Inject into the first non-interpolated day
                    inject_day = next(
                        (d for d in all_day_results if d.get("is_sampled")),
                        all_day_results[0],
                    )
                    inject_day.setdefault("top_comps", [])
                    inject_day.setdefault("comp_prices", {})
                    inject_day["top_comps"].append({
                        "id": sec_id,
                        "title": "Secondary benchmark listing",
                        "propertyType": target.property_type or "entire_home",
                        "accommodates": None,
                        "bedrooms": None,
                        "baths": None,
                        "nightlyPrice": avg_sec_price,
                        "currency": "USD",
                        "similarity": 0.95,
                        "rating": None,
                        "reviews": None,
                        "location": None,
                        "url": sec_url,
                        "isPinnedBenchmark": True,
                    })
                    inject_day["comp_prices"][sec_id] = avg_sec_price
                    logger.info(
                        f"[benchmark] Secondary {sec_url}: injected as synthetic comp "
                        f"(avg_price=${avg_sec_price}, n={len(sec_prices_found)} days)"
                    )

            # Aggregate benchmark transparency
            benchmark_info = aggregate_benchmark_transparency(benchmark_url, sampled_results)
            # Attach benchmark-to-target similarity (computed once above)
            if discount_info:
                benchmark_info["detectedDiscounts"] = discount_info

            if user_attributes:
                benchmark_info["benchmarkTargetSimilarity"] = bm_target_similarity
                benchmark_info["benchmarkMismatchLevel"] = bm_mismatch_level

            transparent = _build_daily_transparent_result(
                target=target,
                query_criteria=query_criteria,
                all_day_results=all_day_results,
                timings_ms=timings,
                source="benchmark",
                extraction_warnings=extraction_warnings,
                benchmark_info=benchmark_info,
                spec_backfill=_bm_backfill_meta,
                spec_extraction_meta=_bm_spec_extraction_meta,
            )
            _repair_incomplete_comparable_specs(page, transparent, extraction_warnings)
            _repair_suspicious_comparable_titles(page, transparent, extraction_warnings)

            logger.info(
                f"[benchmark] Pipeline complete: {len(sampled_results)} queries, "
                f"benchmarkUsed={benchmark_info['benchmarkUsed']}, "
                f"avg_adj={benchmark_info['marketAdjustmentPct']}%, "
                f"{timings['total_ms']}ms total"
            )

            return all_day_results, transparent

        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Address preprocessing helper
# ---------------------------------------------------------------------------


def _build_structured_search_location(
    city: Optional[str],
    state: Optional[str],
    postal_code: Optional[str],
) -> tuple:
    """
    Build a search-friendly Airbnb search string from structured fields.

    NOTE: This function is intentionally NOT called when a postalCode is
    present.  run_criteria_search() geocodes the ZIP to a canonical
    city/state first (via _geocode_postal_to_canonical), then uses that
    canonical location as the Airbnb search string.  This function is the
    fallback for the no-postal path.

    Priority:
      1. city + state  → "City, ST"   (state disambiguates city name)
      2. anything else → ""           (caller falls back to address parser)

    A city name alone is not returned here — it may be geographically
    ambiguous (e.g. "Belmont" in CA vs NC vs Long Beach).

    Returns:
        (location: str, confidence: str)
        Returns ("", "") when no unambiguous location can be built.
    """
    city = (city or "").strip() or None
    state = (state or "").strip() or None
    postal_code = (postal_code or "").strip() or None

    if city and state:
        return f"{city}, {state}", "high"
    # postal_code alone: caller should geocode it; don't return raw ZIP here
    return "", ""


def _is_us_zip(postal_code: str) -> bool:
    """Return True if postal_code looks like a US ZIP (5-digit or ZIP+4)."""
    return bool(re.match(r"^\d{5}(?:-\d{4})?$", postal_code.strip()))


def _abbrev_state_for_search(state: str) -> str:
    """
    Return the 2-letter US state code suitable for Airbnb search strings.

    Converts full US state names to their abbreviation so the Airbnb search
    URL uses the compact form (``"Belmont, CA"`` rather than
    ``"Belmont, California"``).

    * ``"California"`` → ``"CA"``
    * ``"CA"``         → ``"CA"``   (already abbreviated — unchanged)
    * ``"Queensland"`` → ``"Queensland"``  (non-US state — preserved as-is)
    * ``"台灣"``        → ``"台灣"``         (non-ASCII state — preserved as-is)
    * ``""``           → ``""``

    The geocode_result metadata is **not** touched — only the string returned
    here is used for building the Airbnb search query.
    """
    if not state:
        return state
    from worker.core.anchor_location import normalize_state
    norm = normalize_state(state)
    # normalize_state returns a 2-letter uppercase ASCII code for US states.
    # Chinese chars / non-ASCII pass .isalpha() but not .isupper(); ASCII
    # 2-letter non-US codes (e.g. "BC" for British Columbia) would pass, but
    # those are already short and acceptable.
    if len(norm) == 2 and norm.isupper() and norm.isascii():
        return norm
    return state  # non-US or unrecognised — preserve original


def _geocode_postal_to_canonical(
    postal_code: str,
    hint_city: Optional[str] = None,
    timeout: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    Resolve a postal code to a canonical city / state / coords via Nominatim.

    For US ZIPs (5-digit or ZIP+4):
      - Appends ", United States" to the query so Nominatim restricts search
        to the US, preventing global misrouting (e.g. "94002" → Mexico).
      - Passes countrycodes="us" as an additional Nominatim filter.
      - Primary query:  "Belmont 94002, United States"  (with hint_city)
      - Fallback query: "94002, United States"           (bare ZIP)

    For non-US postal codes the query is sent without country restriction.

    Returns None on any failure — geocoding is always best-effort.  Callers
    must handle None and fall back gracefully.
    """
    try:
        from worker.core.geocode_details import geocode_address_details
    except ImportError:
        logger.warning("[criteria] geocode_details not available; skipping ZIP geocoding")
        return None

    us_zip = _is_us_zip(postal_code)
    country_suffix = ", United States" if us_zip else ""
    countrycodes = "us" if us_zip else None

    if hint_city:
        query = f"{hint_city} {postal_code}{country_suffix}"
    else:
        query = f"{postal_code}{country_suffix}"

    logger.debug(f"[criteria] Geocode query={query!r} countrycodes={countrycodes!r}")
    result = geocode_address_details(query, timeout=timeout, countrycodes=countrycodes)

    # If the city-hinted query fails, retry with just the ZIP (+ country context)
    if not result and hint_city:
        retry_query = f"{postal_code}{country_suffix}"
        logger.debug(f"[criteria] Geocode retry query={retry_query!r}")
        result = geocode_address_details(retry_query, timeout=timeout, countrycodes=countrycodes)

    return result


def _extract_search_location(address: str) -> tuple:
    """
    Extract the most useful location token from a free-text address string.

    This is the FALLBACK used only when no structured location fields (city,
    state, postalCode) are available.  run_criteria_search() will geocode
    any ZIP code returned here before using it as an Airbnb search query —
    so it is safe to return a bare ZIP; callers handle the geocoding step.

    Rules (in priority order):
      1. Bare ZIP (digits only, 3–6 chars)   → return ZIP  (caller geocodes)
      2. Taiwanese address                   → extract city+district
      3. Comma-separated with trailing ZIP   → return ZIP  (caller geocodes)
      4. Comma-separated, city + state       → "City, ST"
      5. Anything else                       → as-is, medium confidence

    Returns:
        (search_location: str, confidence: str)  — "high" | "medium" | "low"
    """
    addr = address.strip()

    # 1. Bare ZIP / postal code
    if re.match(r"^\d{3,6}$", addr):
        return addr, "high"

    # 2. Taiwanese address: e.g. "台北市信義區松山路123號" → "台北市信義區"
    tw_match = re.search(r"([^\s,]+?(?:市|縣)(?:[^\s,]+?(?:區|鄉|鎮|市))?)", addr)
    if tw_match:
        return tw_match.group(1), "high"

    # 3 & 4. Comma-separated address
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) >= 2:
        # Skip leading street component (starts with a digit)
        start = 1 if re.match(r"^\d", parts[0]) else 0
        city_parts = parts[start:]
        # Trailing bare ZIP wins — caller will geocode it
        if city_parts and re.match(r"^\d{3,6}$", city_parts[-1]):
            return city_parts[-1], "high"
        if city_parts:
            return ", ".join(city_parts[:2]), "high"

    # 5. Single token or unrecognised structure
    return addr, "medium"


# ---------------------------------------------------------------------------
# Anchor selection helper (criteria pass 1)
# ---------------------------------------------------------------------------

# Geo-filter radii used when selecting the anchor listing in criteria pass 1.
# Tighter than the default comp radius (30 km) because the anchor must be in
# the same neighbourhood as the target property.
_ANCHOR_RADIUS_TIGHT_KM: float = 20.0
_ANCHOR_RADIUS_FALLBACK_KM: float = 40.0
# City-centre proxy radius: smaller than the listing-level tight radius because
# geocoded city centres are less precise than per-listing map-pin coordinates.
# 15 km is chosen to keep nearby suburbs (Redwood City ~5 km, San Mateo ~6.5 km)
# while excluding more distant cities (San Francisco ~30 km, Sonoma ~87 km).
_ANCHOR_RADIUS_CITY_PROXY_KM: float = 15.0
# Minimum candidates that must survive the tight filter before we declare it
# "good enough" and skip the fallback.
_ANCHOR_MIN_GEO_CANDIDATES: int = 2


def _select_anchor_candidate(
    candidates: List[ListingSpec],
    user_spec: ListingSpec,
    target_lat: Optional[float],
    target_lng: Optional[float],
    *,
    target_city: Optional[str] = None,
    target_state: Optional[str] = None,
    n_listing_coords: int = 0,
    addr_confidence: str = "low",
) -> Tuple[ListingSpec, float, Dict[str, Any]]:
    """
    Choose the best anchor candidate for criteria pass 2.

    Four-phase pipeline
    -------------------

    Phase 1 — Geo pool (Paths A & B)
        Path A (``n_listing_coords > 0``): tight (20 km) then fallback (40 km)
        geo filter on page-embedded listing coordinates.

        Path B (``n_listing_coords == 0``, target has coords): geocode unique
        candidate location strings to city-centre proxies, apply 15 km filter.

        If neither produces a pool, fall through to Phase 2 as "text_bucket".

    Phase 2 — Location bucket classification
        Classify every candidate in the current pool into five buckets using
        the metro-cluster mapping:
          local_match       — same city as target
          nearby_market     — same metro cluster (approved nearby market)
          regional_mismatch — same state, different cluster
          far_mismatch      — different state
          unknown           — unparseable location (always pass-through)

        This classification is applied to ALL paths so bucket counts are always
        available for reporting, regardless of which path found the geo pool.

    Phase 3 — Controlled nearby-market expansion
        Priority 1: if ``local_match`` candidates exist → pool = local + unknown
        Priority 2: if no local but ``nearby_market`` exists → pool = nearby + unknown
                    (nearby_expansion_used = True)
        Priority 3: fallback based on ``addr_confidence``
          "high"   → fail-safe (use regional/far as last resort, flag degraded)
          "medium"/"low" → also allow regional_mismatch; far remains last resort

        For Paths A/B, this phase re-ranks the geo-filtered pool by bucket
        priority without removing candidates that are geo-confirmed nearby.
        For Path C (text-only), this phase IS the pool selection.

    Phase 4 — Structural similarity ranking
        ``filter_similar_candidates`` then ``similarity_score`` pick the best
        structural match within the location-constrained pool.

    Parameters
    ----------
    candidates       : pool of ListingSpec objects from the Airbnb search page
    user_spec        : synthetic target spec built from user criteria
    target_lat/lng   : geocoded target coordinates (may be None)
    target_city      : target city name (normalised internally)
    target_state     : target state, any form (normalised internally)
    n_listing_coords : how many candidates already have listing-level coords
    addr_confidence  : location confidence ("high" | "medium" | "low")

    Returns
    -------
    (best_match, best_score, anchor_debug)
    """
    from worker.core.anchor_location import (
        classify_candidate_location,
        geocode_candidate_cities,
        get_city_cluster,
        get_nearby_cities,
        normalize_city,
        normalize_location_text,
        normalize_state,
        parse_location_city_state,
    )

    # Pre-normalise target location once
    norm_city = normalize_city(target_city) if target_city else ""
    norm_state = normalize_state(target_state) if target_state else ""

    n_before_geo = len(candidates)
    geo_radius_used: Optional[float] = None
    geo_fallback = False
    geo_skipped = False
    n_after_geo = n_before_geo
    selection_mode = "no_geo"

    location_bucket_counts: Dict[str, int] = {
        "local_match": 0, "nearby_market": 0, "regional_mismatch": 0,
        "far_mismatch": 0, "unknown": 0,
    }
    fail_safe_triggered = False
    nearby_expansion_used = False
    allowed_nearby_cities: List[str] = []
    n_proxy_coords = 0

    pool = candidates  # default: all candidates

    # ── Phase 1A: listing-level coords ────────────────────────────────────────
    if target_lat is not None and target_lng is not None and n_listing_coords > 0:
        selection_mode = "listing_coords"

        tight_pool, tight_excluded = apply_geo_filter(
            candidates, target_lat, target_lng,
            max_radius_km=_ANCHOR_RADIUS_TIGHT_KM,
        )

        if len(tight_pool) >= _ANCHOR_MIN_GEO_CANDIDATES or tight_excluded == 0:
            pool = tight_pool
            geo_radius_used = _ANCHOR_RADIUS_TIGHT_KM
            n_after_geo = len(tight_pool)
            logger.info(
                f"[criteria/anchor] Path A: geo {_ANCHOR_RADIUS_TIGHT_KM}km: "
                f"{n_before_geo} → {n_after_geo} candidates"
            )
        else:
            geo_fallback = True
            geo_radius_used = _ANCHOR_RADIUS_FALLBACK_KM
            fallback_pool, _ = apply_geo_filter(
                candidates, target_lat, target_lng,
                max_radius_km=_ANCHOR_RADIUS_FALLBACK_KM,
            )
            logger.info(
                f"[criteria/anchor] Path A: tight radius sparse "
                f"({len(tight_pool)}); fallback {_ANCHOR_RADIUS_FALLBACK_KM}km"
                f" → {len(fallback_pool)} candidates"
            )
            if fallback_pool:
                pool = fallback_pool
                n_after_geo = len(fallback_pool)
            else:
                geo_skipped = True
                geo_radius_used = None
                selection_mode = "text_bucket"
                logger.warning(
                    "[criteria/anchor] Path A: nothing within fallback radius; "
                    "falling through to text-bucket filter"
                )

    # ── Phase 1B: city-proxy geocoding ────────────────────────────────────────
    elif target_lat is not None and target_lng is not None and n_listing_coords == 0:
        logger.info(
            "[criteria/anchor] Path B: no listing coords; "
            "attempting city-proxy geocoding"
        )
        n_proxy_coords = geocode_candidate_cities(
            candidates, max_unique_cities=10, timeout_per_city=2,
        )
        if n_proxy_coords > 0:
            proxy_pool, proxy_excluded = apply_geo_filter(
                candidates, target_lat, target_lng,
                max_radius_km=_ANCHOR_RADIUS_CITY_PROXY_KM,
            )
            if len(proxy_pool) >= _ANCHOR_MIN_GEO_CANDIDATES or proxy_excluded == 0:
                pool = proxy_pool
                geo_radius_used = _ANCHOR_RADIUS_CITY_PROXY_KM
                n_after_geo = len(proxy_pool)
                selection_mode = "city_proxy"
                logger.info(
                    f"[criteria/anchor] Path B: city-proxy {_ANCHOR_RADIUS_CITY_PROXY_KM}km: "
                    f"{n_before_geo} → {n_after_geo} candidates"
                )
            else:
                selection_mode = "text_bucket"
                logger.info(
                    f"[criteria/anchor] Path B: proxy pool sparse "
                    f"({len(proxy_pool)}); falling through to text-bucket"
                )
        else:
            selection_mode = "text_bucket"
            logger.info(
                "[criteria/anchor] Path B: city-proxy geocoding yielded no coords; "
                "falling through to text-bucket"
            )

    # ── Phase 2: Classify pool members by location bucket (all paths) ─────────
    # Always computed — gives audit trail regardless of which path was used.
    if norm_city and norm_state:
        allowed_nearby_cities = get_nearby_cities(norm_state, norm_city)

        bucketed: Dict[str, List[ListingSpec]] = {
            "local_match": [], "nearby_market": [], "regional_mismatch": [],
            "far_mismatch": [], "unknown": [],
        }
        for cand in pool:
            bkt = classify_candidate_location(
                getattr(cand, "location", "") or "", norm_city, norm_state,
            )
            bucketed[bkt].append(cand)

        for k in location_bucket_counts:
            location_bucket_counts[k] = len(bucketed[k])

        # ── Phase 3: Controlled nearby-market expansion ───────────────────────
        # For text-based paths (C / no_geo): this phase determines the pool.
        # For geo-based paths (A / B): pool is already geo-constrained; this
        # phase applies priority ordering within the geo pool.

        if selection_mode in ("text_bucket", "no_geo"):
            # Text path: staged expansion is the only location filter.
            selection_mode = "text_bucket"

            if bucketed["local_match"]:
                # Priority 1: target-city candidates found — no expansion needed
                pool = bucketed["local_match"] + bucketed["unknown"]
                nearby_expansion_used = False

            elif bucketed["nearby_market"]:
                # Priority 2: expand to approved nearby market
                pool = bucketed["nearby_market"] + bucketed["unknown"]
                nearby_expansion_used = True

            else:
                # Priority 3: no local or nearby → confidence-gated fallback
                nearby_expansion_used = True  # expansion attempted, nothing found

                if addr_confidence == "high":
                    # High confidence: refuse regional/far; fail-safe only
                    fail_safe_triggered = True
                    fallback = bucketed["regional_mismatch"] + bucketed["far_mismatch"]
                else:
                    # Medium/low: accept regional as degraded fallback
                    fallback = bucketed["regional_mismatch"] + bucketed["unknown"]
                    if not fallback:
                        fail_safe_triggered = True
                        fallback = bucketed["far_mismatch"]

                if fallback:
                    pool = fallback

            n_after_geo = len(pool)

            logger.info(
                f"[criteria/anchor] Phase 3 ({addr_confidence} conf, "
                f"expansion={'yes' if nearby_expansion_used else 'no'}): "
                f"local={location_bucket_counts['local_match']} "
                f"nearby={location_bucket_counts['nearby_market']} "
                f"regional={location_bucket_counts['regional_mismatch']} "
                f"far={location_bucket_counts['far_mismatch']} "
                f"unknown={location_bucket_counts['unknown']} "
                f"→ pool={len(pool)}"
                + (" FAIL-SAFE" if fail_safe_triggered else "")
            )

        else:
            # Geo path (A or B): pool already geo-constrained.
            # Priority-order within the pool: local > nearby > regional > far.
            # Prefer local if available; only use full pool if no local candidates.
            if bucketed["local_match"]:
                # Local candidates exist in geo pool — restrict to them
                refined = bucketed["local_match"] + bucketed["unknown"]
                pool = refined
                n_after_geo = len(pool)
                nearby_expansion_used = False
            elif bucketed["nearby_market"] or bucketed["unknown"]:
                # No local in geo pool; geo-confirmed nearby candidates compete
                nearby_expansion_used = True
                # pool stays as geo-filtered (don't narrow further — trust geo)
            else:
                # Geo pool has only regional/far — unusual given geo filter
                nearby_expansion_used = True
                # Still trust the geo filter result; just flag expansion

            logger.info(
                f"[criteria/anchor] Phase 3 geo-path "
                f"({'expansion' if nearby_expansion_used else 'local-only'}): "
                f"local={location_bucket_counts['local_match']} "
                f"nearby={location_bucket_counts['nearby_market']} "
                f"pool={len(pool)}"
            )

    else:
        logger.debug(
            "[criteria/anchor] No target city/state — skipping bucket "
            "classification and expansion"
        )

    # ── Phase 4: Structural similarity ranking ────────────────────────────────
    filtered, _filter_debug = filter_similar_candidates(user_spec, pool)
    if not filtered:
        logger.warning(
            f"[criteria/anchor] Similarity filter emptied pool of {len(pool)}; "
            "falling back to unfiltered pool"
        )
        filtered = pool

    scored = [(c, similarity_score(user_spec, c)) for c in filtered]
    scored.sort(key=lambda x: x[1], reverse=True)

    best_match, best_score = scored[0]
    anchor_dist = getattr(best_match, "distance_to_target_km", None)

    # Classify the selected anchor's bucket for reporting
    anchor_bucket: Optional[str] = None
    if norm_city and norm_state:
        anchor_bucket = classify_candidate_location(
            getattr(best_match, "location", "") or "", norm_city, norm_state,
        )
        # For geo paths: finalise nearby_expansion_used from the selected anchor
        if selection_mode in ("listing_coords", "city_proxy"):
            nearby_expansion_used = anchor_bucket not in (
                None, "local_match", "unknown"
            )

    # Compute location explainability fields for the selected anchor
    _anchor_raw_loc = (getattr(best_match, "location", "") or "").strip()
    _anchor_norm_loc, _anchor_norm_notes = normalize_location_text(_anchor_raw_loc)
    _anchor_city, _anchor_state_code = parse_location_city_state(
        _anchor_norm_loc if _anchor_norm_loc else _anchor_raw_loc
    )
    _anchor_cluster = (
        get_city_cluster(_anchor_state_code, _anchor_city)
        if _anchor_state_code and _anchor_city
        else None
    )

    logger.info(
        f"[criteria/anchor] Selected: {best_match.url} "
        f"score={best_score:.3f} "
        + (f"dist={anchor_dist:.1f}km " if anchor_dist is not None else "dist=unknown ")
        + f"mode={selection_mode} bucket={anchor_bucket} "
        + f"expansion={nearby_expansion_used}"
        + (f" norm={_anchor_norm_notes!r}" if _anchor_norm_notes else "")
    )

    return best_match, best_score, {
        "anchorCandidatesBeforeGeo": n_before_geo,
        "anchorCandidatesAfterGeo": n_after_geo,
        "anchorGeoRadiusKm": geo_radius_used,
        "anchorGeoFallback": geo_fallback,
        "anchorGeoSkipped": geo_skipped,
        "anchorStructuralScore": round(best_score, 3),
        "anchorDistanceKm": round(anchor_dist, 2) if anchor_dist is not None else None,
        "anchorHasTargetCoords": (target_lat is not None and target_lng is not None),
        "anchorSelectionMode": selection_mode,
        "anchorProxyCoordsAssigned": n_proxy_coords,
        "anchorLocationBuckets": location_bucket_counts,
        "anchorLocationBucket": anchor_bucket,
        "anchorFailSafeTriggered": fail_safe_triggered,
        "anchorNearbyExpansionUsed": nearby_expansion_used,
        "anchorAllowedNearbyCities": allowed_nearby_cities,
        "anchorTargetCityOnlyCount": location_bucket_counts["local_match"],
        "anchorNearbyMarketCount": location_bucket_counts["nearby_market"],
        "targetLocationConfidence": addr_confidence,
        "targetCanonicalCity": norm_city or None,
        "targetCanonicalState": norm_state or None,
        "anchorRawLocation": _anchor_raw_loc or None,
        "anchorNormalizedLocation": _anchor_norm_loc or _anchor_raw_loc or None,
        "anchorNormalizationNotes": _anchor_norm_notes or None,
        "anchorClusterId": _anchor_cluster,
    }


# ---------------------------------------------------------------------------
# Canonical target resolution helper (used by run_criteria_search)
# ---------------------------------------------------------------------------


def _resolve_canonical_target(
    candidates,                      # List[ListingSpec]
    raw_city: Optional[str],
    raw_state: Optional[str],
    addr_confidence: str,
    target_lat: Optional[float] = None,
    target_lng: Optional[float] = None,
) -> Tuple[Optional[str], Optional[str], str]:
    """
    Decide which city/state to use as the canonical target for anchor selection.

    High confidence → trust the structured address fields directly.
    Low / medium confidence → try to infer from the Airbnb first-page
    candidates (distance-first, then vote fallback).

    Returns ``(anchor_city, anchor_state, source)`` — safe to pass straight
    into ``_select_anchor_candidate`` as ``target_city`` / ``target_state``.
    ``source`` mirrors the ``targetCanonicalCitySource`` debug key.
    """
    if addr_confidence == "high":
        return raw_city, raw_state, "address"

    from worker.core.anchor_location import infer_canonical_target_from_candidates
    inf_city, inf_state, source = infer_canonical_target_from_candidates(
        candidates,
        fallback_city=raw_city,
        fallback_state=raw_state,
        target_lat=target_lat,
        target_lng=target_lng,
    )
    # Ensure we always return something, even when inference falls back
    return (inf_city or raw_city), (inf_state or raw_state), source


# ---------------------------------------------------------------------------
# Criteria-based search (Mode B)
# ---------------------------------------------------------------------------


def run_criteria_search(
    address: str,
    attributes: Dict[str, Any],
    checkin: str,
    checkout: str,
    cdp_url: str = "http://127.0.0.1:9222",
    top_k: int = 10,
    max_scroll_rounds: int = DAY_SCROLL_ROUNDS,
    max_cards: int = DAY_MAX_CARDS,
    max_runtime_seconds: int = 180,
    rate_limit_seconds: float = 1.0,
    cdp_connect_timeout_ms: int = 15000,
    preferred_comps: Optional[List[Dict[str, Any]]] = None,
    target_lat: Optional[float] = None,
    target_lng: Optional[float] = None,
    max_radius_km: Optional[float] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    nightly_plan: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Criteria-based search: search Airbnb for listings matching the user's
    property criteria, find the most similar one, then use it as the anchor
    listing for a full day-by-day scrape via run_scrape().

    Two-pass pipeline:
      Pass 1 -- Search Airbnb by address, collect cards, rank by similarity
      Pass 2 -- Use best match's URL as anchor for run_scrape()

    Returns (daily_results, transparent_result).
    """
    from playwright.sync_api import sync_playwright

    start_time = time.time()
    timings: Dict[str, int] = {}
    base_origin = "https://www.airbnb.com"

    # ── Step 1: read structured location fields from attributes ──────────────
    _city = (attributes.get("city") or "").strip() or None
    _state = (attributes.get("state") or "").strip() or None
    _postal = (
        (attributes.get("postalCode") or attributes.get("postal_code") or "").strip() or None
    )

    # ── Step 2: resolve location to a canonical city/state for Airbnb search ─
    #
    # Strategy:
    #   A. postalCode present → geocode ZIP to canonical city/state/coords.
    #      Using raw ZIP as the Airbnb search query is unreliable — Airbnb
    #      may misroute bare ZIP codes to unrelated places (e.g. "94002" has
    #      matched San Carlos, Mexico in some sessions).  Geocoding the ZIP
    #      produces a canonical city/state that Airbnb resolves correctly.
    #   B. no postalCode, city + state → use "city, state" directly.
    #   C. no postalCode, city only   → address-string fallback (medium conf).
    #   D. nothing structured         → address-string fallback.
    #
    # In all cases where the fallback parser returns a bare ZIP, we also
    # geocode it so the Airbnb search is always city/state-based.

    geocode_result: Optional[Dict[str, Any]] = None
    city_zip_mismatch: Optional[str] = None
    geocode_query_used: Optional[str] = None  # logged in queryCriteria for debugging

    # Detect ZIP anywhere in the address string (for the all-fallback path D)
    _addr_zip_match = re.search(r"\b(\d{5})\b", address)
    _addr_zip = _addr_zip_match.group(1) if _addr_zip_match else None

    if _postal:
        # Path A: geocode the ZIP
        _postal_us = _is_us_zip(_postal)
        _postal_suffix = ", United States" if _postal_us else ""
        geocode_query_used = (
            f"{_city} {_postal}{_postal_suffix}" if _city else f"{_postal}{_postal_suffix}"
        )
        logger.info(f"[criteria] Geocoding postalCode={_postal!r} hint_city={_city!r}")
        geocode_result = _geocode_postal_to_canonical(_postal, hint_city=_city)

        if geocode_result:
            gc_city = geocode_result.get("city")
            gc_state = geocode_result.get("state")
            gc_lat = geocode_result.get("lat")
            gc_lng = geocode_result.get("lng")

            # Carry geocoded coords forward as target coords if not already set
            if target_lat is None and gc_lat is not None:
                target_lat = gc_lat
            if target_lng is None and gc_lng is not None:
                target_lng = gc_lng

            # Warn if user-supplied city disagrees with geocoded city
            if _city and gc_city and _city.lower() != gc_city.lower():
                city_zip_mismatch = (
                    f"User city {_city!r} ≠ geocoded city {gc_city!r} for ZIP {_postal!r}"
                )
                logger.warning(f"[criteria] {city_zip_mismatch}")

            # Build canonical search string from geocoded city + state.
            # Abbreviate full state names ("California" → "CA") so the Airbnb
            # search URL uses the compact form Airbnb resolves most reliably.
            if gc_city and gc_state:
                search_location = f"{gc_city}, {_abbrev_state_for_search(gc_state)}"
                addr_confidence = "high"
            elif gc_city:
                search_location = gc_city
                addr_confidence = "medium"
            else:
                # Geocode returned coords but no city — fall through to city+state
                search_location = ""
                addr_confidence = "low"
        else:
            logger.warning(
                f"[criteria] ZIP geocode failed for {_postal!r}; falling back to "
                "structured city/state or address parser"
            )
            geocode_result = None
            search_location = ""
            addr_confidence = "low"

        # Geocode failed or returned no city: try city+state, then address parser
        if not search_location:
            if _city and _state:
                search_location = f"{_city}, {_abbrev_state_for_search(_state)}"
                addr_confidence = "medium"
            elif _city:
                search_location = _city
                addr_confidence = "low"
            else:
                search_location, addr_confidence = _extract_search_location(address)

    elif _city and _state:
        # Path B: no ZIP, structured city + state
        search_location = f"{_city}, {_abbrev_state_for_search(_state)}"
        addr_confidence = "high"

    elif _city:
        # Path C: city only — low confidence, ambiguous
        search_location = _city
        addr_confidence = "low"

    else:
        # Path D: no structured fields — parse address string
        search_location, addr_confidence = _extract_search_location(address)

        # If the fallback parser returned a bare ZIP, geocode it too
        raw_is_zip = bool(re.match(r"^\d{3,6}$", search_location))
        if raw_is_zip:
            logger.info(
                f"[criteria] Address parser returned ZIP {search_location!r}; geocoding"
            )
            _raw_us = _is_us_zip(search_location)
            geocode_query_used = search_location + (", United States" if _raw_us else "")
            geocode_result = _geocode_postal_to_canonical(search_location)
            if geocode_result:
                gc_city = geocode_result.get("city")
                gc_state = geocode_result.get("state")
                if target_lat is None:
                    target_lat = geocode_result.get("lat")
                if target_lng is None:
                    target_lng = geocode_result.get("lng")
                if gc_city and gc_state:
                    search_location = f"{gc_city}, {_abbrev_state_for_search(gc_state)}"
                    addr_confidence = "high"
                elif gc_city:
                    search_location = gc_city
                    addr_confidence = "medium"
            # If geocode fails, search_location stays as the raw ZIP (best-effort)

    logger.info(
        f"[criteria] Final search_location={search_location!r} "
        f"confidence={addr_confidence} geocoded={geocode_result is not None}"
    )
    if addr_confidence == "low":
        logger.warning(
            f"[criteria] Low confidence location for address={address!r}. "
            "Results may be inaccurate."
        )

    is_zip = bool(re.match(r"^\d{3,6}$", search_location))
    search_mode = "zip" if is_zip else "city"

    # Extract preferred comps from attributes if not explicitly passed
    if preferred_comps is None:
        raw = attributes.get("preferredComps")
        preferred_comps = raw if isinstance(raw, list) else None

    # Build a synthetic target spec from user criteria
    user_spec = ListingSpec(
        url="",
        title="User property",
        location=search_location,
        accommodates=attributes.get("maxGuests"),
        bedrooms=attributes.get("bedrooms"),
        beds=attributes.get("bedrooms"),  # approximate beds = bedrooms
        baths=attributes.get("bathrooms"),
        property_type=attributes.get("propertyType", ""),
        lat=target_lat,
        lng=target_lng,
    )

    adults = min(attributes.get("maxGuests", 2), 16)

    total_nights = max(1, (dt.strptime(checkout, "%Y-%m-%d") - dt.strptime(checkin, "%Y-%m-%d")).days)

    query_criteria = {
        "locationBasis": search_location,
        "rawAddress": address,
        "searchMode": search_mode,
        "addressConfidence": addr_confidence,
        "structuredLocation": {
            "city": _city,
            "state": _state,
            "postalCode": _postal,
        },
        "geocodeResult": {
            "city": geocode_result.get("city") if geocode_result else None,
            "state": geocode_result.get("state") if geocode_result else None,
            "postalCode": geocode_result.get("postal_code") if geocode_result else None,
            "country": geocode_result.get("country") if geocode_result else None,
            "countryCode": geocode_result.get("country_code") if geocode_result else None,
            "lat": geocode_result.get("lat") if geocode_result else None,
            "lng": geocode_result.get("lng") if geocode_result else None,
        } if geocode_result else None,
        "geocodeQuery": geocode_query_used,
        "cityZipMismatch": city_zip_mismatch,
        "searchAdults": adults,
        "checkin": checkin,
        "checkout": checkout,
        "propertyTypeFilter": user_spec.property_type or None,
        "tolerances": {
            "accommodates": 3,
            "bedrooms": 2,
            "beds": 3,
            "baths": 1.5,
        },
    }

    cdp_ok, cdp_reason = check_cdp_endpoint(cdp_url)
    if not cdp_ok:
        return [], {
            "targetSpec": {
                "title": "User property",
                "location": address,
                "city": "",
                "state": "",
                "postalCode": "",
                "country": "",
                "countryCode": "",
                "lat": target_lat,
                "lng": target_lng,
                "propertyType": user_spec.property_type,
                "accommodates": user_spec.accommodates,
                "bedrooms": user_spec.bedrooms,
                "beds": user_spec.beds,
                "baths": user_spec.baths,
                "amenities": [],
                "rating": None,
                "reviews": None,
            },
            "queryCriteria": query_criteria,
            "compsSummary": None,
            "priceDistribution": None,
            "recommendedPrice": None,
            "comparableListings": None,
            "debug": {
                "source": "criteria",
                "error": f"CDP unavailable: {cdp_reason}",
                "cdp_url": cdp_url,
                "extractionWarnings": [],
                "timingsMs": {},
            },
        }

    # Initialised before the playwright block so they're in scope for
    # the debug metadata section that runs after pass 2.
    coord_map: Dict[str, tuple] = {}
    n_coords_assigned = 0
    anchor_debug: Dict[str, Any] = {}

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            cdp_url,
            timeout=cdp_connect_timeout_ms,
        )
        context = browser.new_context(
            locale="en-US",
            timezone_id="America/Los_Angeles",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = context.new_page()

        try:
            # Pass 1: Search Airbnb for the area to find an anchor listing
            search_url = build_search_url(
                base_origin, search_location, checkin, checkout, adults
            )
            logger.info(f"[criteria] Search URL: {search_url}")

            time.sleep(rate_limit_seconds)
            page.goto(search_url, wait_until="domcontentloaded")
            wait_for_cards(page)

            # Dismiss modals
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > max_runtime_seconds - 10:
                return [], _empty_transparent("criteria", "Timeout before scroll")

            # Nightly Pass 1: apply reduced scroll/card limits to match the per-query
            # budget of the crawl plan.  Interactive jobs use caller-supplied defaults.
            _p1_scroll = nightly_plan.scroll_rounds if nightly_plan is not None else max_scroll_rounds
            _p1_max_cards = nightly_plan.max_cards if nightly_plan is not None else max_cards

            scroll_start = time.time()
            raw_cards = scroll_and_collect(
                page,
                max_rounds=_p1_scroll,
                max_cards=_p1_max_cards,
                pause_ms=900,
                rate_limit_seconds=rate_limit_seconds,
                stay_nights=total_nights,
            )
            timings["scroll_ms"] = round((time.time() - scroll_start) * 1000)

            candidates = [parse_card_to_spec(c) for c in raw_cards]
            candidates = [c for c in candidates if c.url and c.nightly_price]

            # Enrich candidates with page-embedded coordinates (map pin data).
            # extract_comp_coords() scans __NEXT_DATA__ for room_id → (lat, lng).
            # Candidates that lack a room-id match keep lat=None (pass-through in geo filter).
            coord_map = extract_comp_coords(page)
            if coord_map:
                for spec in candidates:
                    m = ROOM_ID_RE.search(spec.url or "")
                    if m:
                        pair = coord_map.get(m.group(1))
                        if pair:
                            spec.lat, spec.lng = pair[0], pair[1]
                            n_coords_assigned += 1
            logger.info(
                f"[criteria] Coord map: {len(coord_map)} entries, "
                f"coords assigned to {n_coords_assigned}/{len(candidates)} candidates"
            )

            if not candidates:
                no_results_hint = (
                    f"No listings found for ZIP code '{search_location}'. "
                    "Try using the city name instead."
                    if is_zip else
                    f"No listings found in search results for '{search_location}'."
                )
                return [], {
                    "targetSpec": None,
                    "queryCriteria": query_criteria,
                    "compsSummary": {"collected": 0, "afterFiltering": 0, "usedForPricing": 0, "filterStage": "empty", "topSimilarity": None, "avgSimilarity": None},
                    "priceDistribution": None,
                    "recommendedPrice": None,
                    "comparableListings": None,
                    "debug": {
                        "source": "criteria",
                        "error": no_results_hint,
                        "searchMode": search_mode,
                        "extractionWarnings": [],
                        "timingsMs": timings,
                    },
                }

            # Canonical target inference: when address confidence is low/medium,
            # vote on the most common city/state from first-page Airbnb candidates
            # rather than trusting the raw user-supplied _city/_state.  High
            # confidence (ZIP geocoded, or city+state both provided) always uses
            # the structured address fields directly.
            _target_raw_city  = _city
            _target_raw_state = _state
            _anchor_target_city, _anchor_target_state, _target_canonical_city_source = \
                _resolve_canonical_target(
                    candidates, _city, _state, addr_confidence,
                    target_lat=target_lat,
                    target_lng=target_lng,
                )

            # Anchor selection: geo-constrained then structural similarity.
            # _select_anchor_candidate() tries three paths in order:
            #   A. listing-level coords (from coord_map)   → 20 km / 40 km filter
            #   B. city-proxy geocoding (if coord_map = 0) → 15 km filter
            #   C. metro-cluster text-bucket filter        → confidence-gated
            best_match, best_score, anchor_debug = _select_anchor_candidate(
                candidates, user_spec, target_lat, target_lng,
                target_city=_anchor_target_city,
                target_state=_anchor_target_state,
                n_listing_coords=n_coords_assigned,
                addr_confidence=addr_confidence,
            )
            anchor_debug["targetRawCity"]              = _target_raw_city or None
            anchor_debug["targetRawState"]             = _target_raw_state or None
            anchor_debug["targetCanonicalCitySource"]  = _target_canonical_city_source

            logger.info(
                f"[criteria] Anchor selected: {best_match.url} "
                f"(score={best_score:.3f}, "
                f"dist={anchor_debug.get('anchorDistanceKm')!r} km, "
                f"bedrooms={best_match.bedrooms}, "
                f"price=${best_match.nightly_price})"
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

    # Pass 2: Use best match as anchor for full day-by-day scrape
    elapsed = time.time() - start_time
    remaining = max_runtime_seconds - elapsed

    if remaining < 30:
        # Not enough time for day-by-day scrape; return empty daily_results
        # (process_job will fail the job with a user-facing error)
        logger.info(f"[criteria] Low time ({remaining:.0f}s), returning empty daily_results")
        return [], _empty_transparent(
            "criteria_direct",
            f"Insufficient time for day-by-day queries ({remaining:.0f}s remaining)",
        )

    # Full second pass via run_scrape (day-by-day)
    logger.info(
        f"[criteria] Running day-by-day scrape on anchor: {best_match.url} "
        f"({remaining:.0f}s remaining)"
    )
    daily_results, scrape_transparent = run_scrape(
        listing_url=best_match.url,
        checkin=checkin,
        checkout=checkout,
        cdp_url=cdp_url,
        adults=adults,
        top_k=top_k,
        max_scroll_rounds=max_scroll_rounds,
        max_cards=max_cards,
        max_runtime_seconds=int(remaining),
        rate_limit_seconds=rate_limit_seconds,
        cdp_connect_timeout_ms=cdp_connect_timeout_ms,
        preferred_comps=preferred_comps,
        target_lat=target_lat,
        target_lng=target_lng,
        max_radius_km=max_radius_km,
        progress_callback=progress_callback,
        nightly_plan=nightly_plan,
    )

    # Merge criteria-specific info into the transparent result
    scrape_transparent["debug"]["source"] = "criteria"
    scrape_transparent["debug"]["criteria_search_ms"] = round(elapsed * 1000)
    scrape_transparent["debug"]["anchor_url"] = best_match.url
    scrape_transparent["debug"]["anchor_score"] = round(best_score, 3)
    scrape_transparent["debug"]["initial_candidates"] = len(candidates)
    # Anchor geo-selection metadata (filled by _select_anchor_candidate)
    scrape_transparent["debug"]["anchorCoordMapSize"] = len(coord_map)
    scrape_transparent["debug"]["anchorCoordsAssigned"] = n_coords_assigned
    scrape_transparent["debug"].update(anchor_debug)

    return daily_results, scrape_transparent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_transparent(source: str, error: str) -> Dict[str, Any]:
    """Return a minimal transparent_result dict for error/empty cases."""
    return {
        "targetSpec": None,
        "queryCriteria": None,
        "compsSummary": None,
        "priceDistribution": None,
        "recommendedPrice": None,
        "comparableListings": None,
        "debug": {
            "source": source,
            "error": error,
            "extractionWarnings": [],
            "timingsMs": {},
        },
    }
