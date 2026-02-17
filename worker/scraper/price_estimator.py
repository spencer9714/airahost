"""
Playwright-based Airbnb price estimator — orchestrator.

Connects to a local Chrome instance via CDP (Chrome DevTools Protocol)
to scrape target listing specs and nearby comparable listings.

Uses day-by-day 1-night queries to get accurate nightly prices.  See
day_query.py for the rationale (Airbnb search cards display total trip
prices for multi-night stays, which inflates extracted nightly rates).

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
from datetime import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from worker.core.similarity import (
    filter_similar_candidates,
    similarity_score,
)
from worker.scraper.comparable_collector import (
    build_search_url,
    parse_card_to_spec,
    scroll_and_collect,
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
    extract_target_spec,
    safe_domain_base,
)

logger = logging.getLogger("worker.scraper")


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
    comparable_index: Dict[str, Dict[str, Any]] = {}
    for day_result in all_day_results:
        for comp in day_result.get("top_comps", []) or []:
            comp_id = str(comp.get("id") or comp.get("url") or "").strip()
            if not comp_id:
                continue
            score = float(comp.get("similarity") or 0.0)
            if comp_id not in comparable_index:
                comparable_index[comp_id] = {
                    "item": dict(comp),
                    "score_sum": score,
                    "count": 1,
                }
            else:
                comparable_index[comp_id]["score_sum"] += score
                comparable_index[comp_id]["count"] += 1

    comparable_listings: List[Dict[str, Any]] = []
    for state in comparable_index.values():
        item = dict(state["item"])
        avg_score = state["score_sum"] / max(1, state["count"])
        item["similarity"] = round(avg_score, 3)
        comparable_listings.append(item)
    comparable_listings.sort(
        key=lambda row: (
            -float(row.get("similarity") or 0.0),
            float(row.get("nightlyPrice") or 0.0),
        )
    )
    comparable_listings = comparable_listings[:20]

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
                    )
                    if result.median_price is not None:
                        break
                    if attempt < PER_DAY_MAX_RETRIES:
                        logger.info(
                            f"[day_query] {date_i.isoformat()}: retry {attempt+1}/{PER_DAY_MAX_RETRIES}"
                        )

                if result is not None:
                    sampled_results.append(result)

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
                    "filter_stage": dr.filter_stage,
                    "flags": dr.flags,
                    "is_sampled": dr.is_sampled,
                    "is_weekend": dr.is_weekend,
                    "price_distribution": dr.price_distribution,
                    "top_comps": dr.top_comps,
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
            )

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

    # Build a synthetic target spec from user criteria
    user_spec = ListingSpec(
        url="",
        title="User property",
        location=address,
        accommodates=attributes.get("maxGuests"),
        bedrooms=attributes.get("bedrooms"),
        beds=attributes.get("bedrooms"),  # approximate beds = bedrooms
        baths=attributes.get("bathrooms"),
        property_type=attributes.get("propertyType", ""),
    )

    adults = min(attributes.get("maxGuests", 2), 16)

    query_criteria = {
        "locationBasis": address,
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
                base_origin, address, checkin, checkout, adults
            )
            logger.info(f"[criteria] Search URL: {search_url}")

            time.sleep(rate_limit_seconds)
            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(900)

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
            )
            timings["scroll_ms"] = round((time.time() - scroll_start) * 1000)

            candidates = [parse_card_to_spec(c) for c in raw_cards]
            candidates = [c for c in candidates if c.url and c.nightly_price]

            if not candidates:
                return [], {
                    "targetSpec": None,
                    "queryCriteria": query_criteria,
                    "compsSummary": {"collected": 0, "afterFiltering": 0, "usedForPricing": 0, "filterStage": "empty", "topSimilarity": None, "avgSimilarity": None},
                    "priceDistribution": None,
                    "recommendedPrice": None,
                    "comparableListings": None,
                    "debug": {
                        "source": "criteria",
                        "error": "No listings found in search results",
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
