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

from worker.core.geo_filter import DEFAULT_MAX_RADIUS_KM
from worker.core.similarity import (
    SIMILARITY_FLOOR,
    comp_urls_match,
    filter_similar_candidates,
    similarity_score,
)
from worker.scraper.comparable_collector import (
    build_search_url,
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
        url = str(item.get("url") or "").strip()
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

        url = str(item.get("url") or "").strip()
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
        # Only surface comps whose average similarity passes the floor.
        # top_comps already excludes below-floor comps, so this is a safety guard
        # against boosted display scores that may have been stored in older entries.
        if avg_score < SIMILARITY_FLOOR:
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
            "propertyType": target.property_type or "",
            "accommodates": target.accommodates,
            "bedrooms": target.bedrooms,
            "beds": target.beds,
            "baths": target.baths,
            "amenities": target.amenities or [],
            "rating": target.rating,
            "reviews": target.reviews,
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
            "lowCompConfidenceDays": low_comp_confidence_days,
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
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Full scrape pipeline using day-by-day 1-night queries.

    Returns (daily_results, transparent_result).
    daily_results is a list of dicts, one per night in [checkin, checkout).
    Each dict contains: date, median_price, comps_collected, comps_used,
    filter_stage, flags, is_sampled, is_weekend, price_distribution, error.

    Raises ValueError if the date range exceeds MAX_NIGHTS.
    """
    from playwright.sync_api import sync_playwright

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

    # Determine which nights to actually query
    if total_nights <= SAMPLE_THRESHOLD:
        sample_indices = list(range(total_nights))
    else:
        sample_indices = compute_sample_dates(total_nights, MAX_SAMPLE_QUERIES)

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
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        try:
            # Step 1: Extract target listing spec
            logger.info(f"Extracting target: {listing_url}")
            extract_start = time.time()
            target, warnings = extract_target_spec(page, listing_url)
            extraction_warnings.extend(warnings)
            timings["extract_ms"] = round((time.time() - extract_start) * 1000)

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
                else:
                    # Fallback: last meaningful token from title delimiters
                    tokens = [
                        t.strip()
                        for t in re.split(r"[-|•·]", target.title)
                        if t.strip() and len(t.strip()) >= 3
                    ]
                    target.location = tokens[-1] if tokens else ""
                extraction_warnings.append(f"Location fallback from title: '{target.location}'")
                logger.warning(f"Location fallback from title: '{target.location}'")

            if not target.location:
                return [], {
                    "targetSpec": {
                        "title": target.title,
                        "location": "",
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
            day_loop_start = time.time()

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
                        max_scroll_rounds=max_scroll_rounds,
                        max_cards=max_cards,
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
                if progress_callback is not None:
                    try:
                        progress_callback(idx_pos + 1, len(sample_indices))
                    except Exception:
                        pass

            timings["day_queries_ms"] = round((time.time() - day_loop_start) * 1000)

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
) -> tuple:
    """
    Benchmark-first pipeline.

    Uses the pinned comp (benchmark_url) as the primary pricing anchor.
    Market comps from search are used only for a capped adjustment.

    Returns (daily_results, transparent_result).
    Fallback: if benchmark price fetch fails entirely, raises ValueError
    so the caller can fall back to the standard run_scrape pipeline.
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

    # Benchmark mode uses fewer sample queries
    if total_nights <= SAMPLE_THRESHOLD:
        sample_indices = list(range(total_nights))
    else:
        sample_indices = compute_sample_dates(total_nights, BENCHMARK_MAX_SAMPLE_QUERIES)

    logger.info(
        f"Benchmark pipeline: {benchmark_url} | {total_nights} nights, "
        f"querying {len(sample_indices)} days"
    )

    cdp_ok, cdp_reason = check_cdp_endpoint(cdp_url)
    if not cdp_ok:
        return [], _empty_transparent("benchmark", f"CDP unavailable: {cdp_reason}")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url, timeout=cdp_connect_timeout_ms)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
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
            if target_spec_override is not None:
                target = target_spec_override
            else:
                logger.info(f"[benchmark] Extracting spec from: {benchmark_url}")
                target, warnings = extract_target_spec(page, benchmark_url)
                extraction_warnings.extend(warnings)

                # Location fallback (mirrors run_scrape)
                if not target.location:
                    loc_m = re.search(
                        r"\bin\s+([A-Z][a-zA-Z\s,]+(?:,\s*[A-Z][a-zA-Z\s]+)?)",
                        target.title,
                    )
                    if loc_m:
                        target.location = loc_m.group(1).strip().rstrip(",.")
                    else:
                        tokens = [
                            t.strip()
                            for t in re.split(r"[-|•·]", target.title)
                            if t.strip() and len(t.strip()) >= 3
                        ]
                        target.location = tokens[-1] if tokens else ""
                    extraction_warnings.append(
                        f"[benchmark] Location fallback from title: '{target.location}'"
                    )

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
            from worker.core.benchmark import BENCHMARK_SCROLL_ROUNDS, BENCHMARK_MAX_CARDS, BENCHMARK_TOP_K
            sampled_results: List[BenchmarkDayResult] = []
            day_loop_start = time.time()

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
                    max_scroll_rounds=BENCHMARK_SCROLL_ROUNDS,
                    max_cards=BENCHMARK_MAX_CARDS,
                    rate_limit_seconds=rate_limit_seconds,
                    top_k=BENCHMARK_TOP_K,
                    max_radius_km=_effective_radius,
                )
                sampled_results.append(result)
                if progress_callback is not None:
                    try:
                        progress_callback(idx_pos + 1, len(sample_indices))
                    except Exception:
                        pass

            timings["day_queries_ms"] = round((time.time() - day_loop_start) * 1000)

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


# ---------------------------------------------------------------------------
# Address preprocessing helper
# ---------------------------------------------------------------------------


def _extract_search_location(address: str) -> tuple:
    """
    Extract a search-friendly location string from a full property address.

    Airbnb search works best with city/neighborhood names rather than full
    street addresses.  This function strips the street-level detail and
    returns the most useful search token, plus a confidence indicator.

    Returns:
        (search_location: str, confidence: str)  — confidence is "high" | "medium" | "low"
    """
    addr = address.strip()

    # ZIP / postal code (digits only, 3–6 chars): use directly
    if re.match(r"^\d{3,6}$", addr):
        return addr, "high"

    # Taiwanese address: extract city + district
    # e.g. "台北市信義區松山路123號" → "台北市信義區"
    tw_match = re.search(r"([^\s,]+?(?:市|縣)(?:[^\s,]+?(?:區|鄉|鎮|市))?)", addr)
    if tw_match:
        return tw_match.group(1), "high"

    # Comma-separated: "123 Main St, New York, NY 10001" → "New York, NY"
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) >= 2:
        # Skip leading street component (starts with a digit or looks like a house number)
        start = 1 if re.match(r"^\d", parts[0]) else 0
        city_parts = parts[start:]
        # Drop trailing bare ZIP/postal codes
        city_parts = [p for p in city_parts if not re.match(r"^\d{3,6}$", p.strip())]
        if city_parts:
            return ", ".join(city_parts[:2]), "high"

    # Single token or no recognisable structure: use as-is
    return addr, "medium"


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

    # Extract a search-friendly location from the full address
    search_location, addr_confidence = _extract_search_location(address)
    is_zip = bool(re.match(r"^\d{3,6}$", search_location))
    search_mode = "zip" if is_zip else "city"
    logger.info(
        f"[criteria] address={address!r} → search_location={search_location!r} "
        f"(mode={search_mode}, confidence={addr_confidence})"
    )
    if addr_confidence == "low":
        logger.warning(
            f"[criteria] Low confidence extracting search location from address={address!r}. "
            "Results may be inaccurate."
        )

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

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            cdp_url,
            timeout=cdp_connect_timeout_ms,
        )
        context = browser.contexts[0] if browser.contexts else browser.new_context()
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

            scroll_start = time.time()
            raw_cards = scroll_and_collect(
                page,
                max_rounds=max_scroll_rounds,
                max_cards=max_cards,
                pause_ms=900,
                rate_limit_seconds=rate_limit_seconds,
                stay_nights=total_nights,
            )
            timings["scroll_ms"] = round((time.time() - scroll_start) * 1000)

            candidates = [parse_card_to_spec(c) for c in raw_cards]
            candidates = [c for c in candidates if c.url and c.nightly_price]

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

            # Rank by similarity to user's spec
            filtered_candidates, _filter_debug = filter_similar_candidates(user_spec, candidates)
            scored = [(c, similarity_score(user_spec, c)) for c in filtered_candidates]
            scored.sort(key=lambda x: x[1], reverse=True)

            best_match = scored[0][0]
            best_score = scored[0][1]

            logger.info(
                f"[criteria] Best match: {best_match.url} "
                f"(score={best_score:.3f}, "
                f"bedrooms={best_match.bedrooms}, "
                f"price=${best_match.nightly_price})"
            )

        finally:
            try:
                page.close()
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
    )

    # Merge criteria-specific info into the transparent result
    scrape_transparent["debug"]["source"] = "criteria"
    scrape_transparent["debug"]["criteria_search_ms"] = round(elapsed * 1000)
    scrape_transparent["debug"]["anchor_url"] = best_match.url
    scrape_transparent["debug"]["anchor_score"] = round(best_score, 3)
    scrape_transparent["debug"]["initial_candidates"] = len(candidates)

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
