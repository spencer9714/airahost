"""
Benchmark-first pricing engine.

When a user provides a preferred comparable (pinned comp) the system uses
it as the primary benchmark rather than as a similarity-score boost.

Two-stage model
---------------
Stage 1 — Benchmark anchor
  Fetch the benchmark listing's own nightly price for each sample day.
  Primary strategy: look for it in 1-night search results.
  Fallback strategy: navigate to its listing page directly with dates.

Stage 2 — Market validation / adjustment
  Collect a smaller set of other market comps from the same search.
  Compute the raw market offset vs the benchmark price, cap it at
  ±BENCHMARK_MAX_ADJ, then apply only BENCHMARK_MARKET_WEIGHT of the
  offset so the benchmark price stays dominant.

  final_price = benchmark_price × (1 + capped_adj × MARKET_WEIGHT)

Fast-path settings
------------------
Benchmark mode requests fewer scroll rounds, fewer cards, and fewer
sample days than the standard pipeline, making it faster overall.
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from worker.core.similarity import (
    comp_urls_match,
    filter_similar_candidates,
    similarity_score,
)
from worker.scraper.comparable_collector import (
    build_search_url,
    parse_card_to_spec,
    scroll_and_collect,
)
from worker.scraper.target_extractor import (
    ListingSpec,
    extract_nightly_price_from_listing_page,
)

logger = logging.getLogger("worker.core.benchmark")

# ── Tuning constants ─────────────────────────────────────────────────────────

# Fast-path scraping limits (less than standard day-query defaults)
BENCHMARK_SCROLL_ROUNDS: int = 1     # standard: DAY_SCROLL_ROUNDS = 2
BENCHMARK_MAX_CARDS: int = 15        # standard: DAY_MAX_CARDS = 30
BENCHMARK_TOP_K: int = 5             # standard: top_k = 10
BENCHMARK_MAX_SAMPLE_QUERIES: int = 10  # standard: MAX_SAMPLE_QUERIES = 20

# Pricing formula weights
BENCHMARK_MARKET_WEIGHT: float = 0.30   # 30 % weight to market adjustment
BENCHMARK_MAX_ADJ: float = 0.25         # cap raw market offset at ±25 %

# Fetch status codes
FETCH_STATUS_SEARCH_HIT = "search_hit"       # benchmark appeared in search results
FETCH_STATUS_DIRECT_PAGE = "direct_page"     # obtained via listing-page scrape
FETCH_STATUS_FAILED = "failed"               # price unavailable for this day

import re
_ROOM_ID_RE = re.compile(r"/rooms/(\d+)")


# ── Per-day result ────────────────────────────────────────────────────────────

@dataclass
class BenchmarkDayResult:
    """Day result produced by the benchmark-first pipeline."""

    date: str                                        # "YYYY-MM-DD"
    median_price: Optional[float] = None             # final blended price
    benchmark_price: Optional[float] = None          # Stage-1 anchor price
    market_price: Optional[float] = None             # Stage-2 market median
    market_adj_pct: Optional[float] = None           # raw offset % (market vs benchmark)
    applied_adj_pct: Optional[float] = None          # actual % applied after cap+weight
    benchmark_fetch_status: str = FETCH_STATUS_FAILED
    comps_collected: int = 0
    comps_used: int = 0
    filter_stage: str = ""
    flags: List[str] = field(default_factory=list)
    is_sampled: bool = True
    is_weekend: bool = False
    price_distribution: Dict[str, Any] = field(default_factory=dict)
    top_comps: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


# ── Single-day benchmark query ────────────────────────────────────────────────

def estimate_benchmark_price_for_date(
    page,
    target: ListingSpec,
    benchmark_url: str,
    base_origin: str,
    date_i: date,
    adults: int,
    *,
    max_scroll_rounds: int = BENCHMARK_SCROLL_ROUNDS,
    max_cards: int = BENCHMARK_MAX_CARDS,
    rate_limit_seconds: float = 1.0,
    top_k: int = BENCHMARK_TOP_K,
) -> BenchmarkDayResult:
    """
    Execute a 1-night benchmark-first query for *date_i*.

    1. Run a 1-night Airbnb search (fast-path: fewer rounds/cards).
    2. If the benchmark listing appears in results → use its price as anchor.
    3. If not → navigate to the benchmark listing page directly with dates
       and extract the nightly price from the booking widget.
    4. Remaining market comps → compute market adjustment.
    5. Return blended final price.
    """

    def _safe_num(v: Any, fallback: Optional[float]) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(fallback, (int, float)):
            return float(fallback)
        return 0.0

    def _to_comp_payload(spec: ListingSpec, score: float) -> Dict[str, Any]:
        room_match = _ROOM_ID_RE.search(spec.url or "")
        comp_id = (
            room_match.group(1) if room_match
            else (spec.url or f"comp-{int(time.time() * 1000)}")
        )
        return {
            "id": comp_id,
            "title": spec.title or "Comparable listing",
            "propertyType": spec.property_type or target.property_type or "entire_home",
            "accommodates": int(_safe_num(spec.accommodates, target.accommodates or 1)),
            "bedrooms": int(_safe_num(spec.bedrooms, target.bedrooms or 1)),
            "baths": round(_safe_num(spec.baths, target.baths or 1), 1),
            "nightlyPrice": round(_safe_num(spec.nightly_price, None), 2),
            "currency": spec.currency or "USD",
            "similarity": round(float(score), 3),
            "rating": (
                round(float(spec.rating), 2)
                if isinstance(spec.rating, (int, float)) else None
            ),
            "reviews": (
                int(spec.reviews)
                if isinstance(spec.reviews, (int, float)) else None
            ),
            "location": target.location or None,
            "url": spec.url or None,
            "isPinnedBenchmark": False,
        }

    checkin_str = date_i.isoformat()
    checkout_str = (date_i + timedelta(days=1)).isoformat()
    is_weekend = date_i.weekday() >= 4  # Fri=4, Sat=5

    search_url = build_search_url(
        base_origin, target.location, checkin_str, checkout_str, adults,
    )
    logger.info(f"[benchmark] {checkin_str}: 1-night benchmark search")

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(700)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        raw_cards = scroll_and_collect(
            page,
            max_rounds=max_scroll_rounds,
            max_cards=max_cards,
            pause_ms=600,
            rate_limit_seconds=rate_limit_seconds,
        )
        comps = [parse_card_to_spec(c) for c in raw_cards]
        comps = [c for c in comps if c.url and c.nightly_price and c.nightly_price > 0]
        comps_collected = len(comps)

        # ── Stage 1: locate benchmark in search results ───────────────────
        benchmark_comp = next(
            (c for c in comps if c.url and comp_urls_match(c.url, benchmark_url)),
            None,
        )
        benchmark_price: Optional[float] = None
        benchmark_fetch_status = FETCH_STATUS_FAILED

        if benchmark_comp and benchmark_comp.nightly_price:
            benchmark_price = benchmark_comp.nightly_price
            benchmark_fetch_status = FETCH_STATUS_SEARCH_HIT
            logger.info(
                f"[benchmark] {checkin_str}: benchmark found in search "
                f"(price=${benchmark_price:.2f})"
            )
        else:
            # Not in search results → try direct listing page
            logger.info(
                f"[benchmark] {checkin_str}: benchmark not in search results, "
                "trying direct page"
            )
            time.sleep(rate_limit_seconds)
            direct_price = extract_nightly_price_from_listing_page(
                page, benchmark_url, checkin_str, checkout_str
            )
            if direct_price:
                benchmark_price = direct_price
                benchmark_fetch_status = FETCH_STATUS_DIRECT_PAGE
                logger.info(
                    f"[benchmark] {checkin_str}: direct page price=${benchmark_price:.2f}"
                )
                # Re-navigate to search to ensure market comps are loaded
                time.sleep(rate_limit_seconds)
                page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(700)

        # ── Stage 2: market comps (exclude benchmark itself) ─────────────
        market_comps = [
            c for c in comps
            if not (c.url and comp_urls_match(c.url, benchmark_url))
        ]

        if comps_collected == 0:
            return BenchmarkDayResult(
                date=checkin_str,
                benchmark_price=benchmark_price,
                benchmark_fetch_status=benchmark_fetch_status,
                flags=["missing_data"],
                is_sampled=True,
                is_weekend=is_weekend,
                error="No comps found",
            )

        # Filter and score market comps
        filtered_market, filter_debug = filter_similar_candidates(target, market_comps)
        market_scored: List[Tuple[ListingSpec, float]] = [
            (c, similarity_score(target, c)) for c in filtered_market
        ]
        market_scored.sort(key=lambda x: x[1], reverse=True)

        market_prices = [c.nightly_price for c, _ in market_scored if c.nightly_price]
        market_median = (
            round(statistics.median(market_prices), 2) if market_prices else None
        )

        # ── Blend: benchmark anchor + market adjustment ───────────────────
        final_price: Optional[float] = None
        market_adj_pct: Optional[float] = None
        applied_adj_pct: Optional[float] = None

        if benchmark_price is not None:
            if market_median is not None:
                raw_adj = (market_median - benchmark_price) / benchmark_price
                capped_adj = max(-BENCHMARK_MAX_ADJ, min(BENCHMARK_MAX_ADJ, raw_adj))
                adj_factor = 1.0 + capped_adj * BENCHMARK_MARKET_WEIGHT
                final_price = round(benchmark_price * adj_factor, 2)
                market_adj_pct = round(raw_adj * 100, 1)
                applied_adj_pct = round((adj_factor - 1.0) * 100, 1)
            else:
                final_price = benchmark_price
        elif market_median is not None:
            # No benchmark price at all → fall back to pure market median
            final_price = market_median

        # Build top-comps payload (market comps only; benchmark shown separately)
        top_comps_scored = market_scored[: max(3, top_k)]
        top_comps = [_to_comp_payload(c, s) for c, s in top_comps_scored]

        # Flags
        flags: List[str] = []
        if benchmark_fetch_status == FETCH_STATUS_FAILED:
            flags.append("benchmark_fetch_failed")

        # Price distribution (include benchmark price as a data point)
        all_prices = market_prices.copy()
        if benchmark_price is not None:
            all_prices = [benchmark_price] + all_prices
        dist: Dict[str, Any] = {
            "min": round(min(all_prices), 2) if all_prices else None,
            "max": round(max(all_prices), 2) if all_prices else None,
            "median": round(statistics.median(all_prices), 2) if all_prices else None,
            "p25": None,
            "p75": None,
        }
        if len(all_prices) >= 4:
            q = statistics.quantiles(all_prices, n=4)
            dist["p25"] = round(q[0], 2)
            dist["p75"] = round(q[2], 2)

        logger.info(
            f"[benchmark] {checkin_str}: anchor=${benchmark_price} "
            f"market_median=${market_median} adj={applied_adj_pct}% "
            f"final=${final_price} (status={benchmark_fetch_status})"
        )

        return BenchmarkDayResult(
            date=checkin_str,
            median_price=final_price,
            benchmark_price=benchmark_price,
            market_price=market_median,
            market_adj_pct=market_adj_pct,
            applied_adj_pct=applied_adj_pct,
            benchmark_fetch_status=benchmark_fetch_status,
            comps_collected=comps_collected,
            comps_used=len(top_comps_scored),
            filter_stage=filter_debug.get("stage", "unknown"),
            flags=flags,
            is_sampled=True,
            is_weekend=is_weekend,
            price_distribution=dist,
            top_comps=top_comps,
        )

    except Exception as exc:
        logger.warning(f"[benchmark] {checkin_str}: error: {exc}")
        return BenchmarkDayResult(
            date=checkin_str,
            benchmark_fetch_status=FETCH_STATUS_FAILED,
            flags=["missing_data"],
            is_sampled=True,
            is_weekend=is_weekend,
            error=str(exc)[:200],
        )


# ── Aggregate transparency stats ─────────────────────────────────────────────

def aggregate_benchmark_transparency(
    benchmark_url: str,
    day_results: List[BenchmarkDayResult],
) -> Dict[str, Any]:
    """
    Aggregate per-day benchmark stats into the transparency block
    surfaced to the frontend.
    """
    total = len(day_results)
    search_hits = sum(
        1 for r in day_results if r.benchmark_fetch_status == FETCH_STATUS_SEARCH_HIT
    )
    direct_fetches = sum(
        1 for r in day_results if r.benchmark_fetch_status == FETCH_STATUS_DIRECT_PAGE
    )
    failed = sum(
        1 for r in day_results if r.benchmark_fetch_status == FETCH_STATUS_FAILED
    )

    benchmark_prices = [r.benchmark_price for r in day_results if r.benchmark_price is not None]
    market_prices = [r.market_price for r in day_results if r.market_price is not None]
    adj_pcts = [r.market_adj_pct for r in day_results if r.market_adj_pct is not None]

    avg_benchmark = round(statistics.mean(benchmark_prices), 2) if benchmark_prices else None
    avg_market = round(statistics.mean(market_prices), 2) if market_prices else None
    avg_adj = round(statistics.mean(adj_pcts), 1) if adj_pcts else None

    benchmark_used = len(benchmark_prices) > 0
    fallback_reason: Optional[str] = None
    if not benchmark_used:
        fallback_reason = "benchmark_fetch_failed"

    # Determine primary fetch method used
    if search_hits >= direct_fetches and search_hits > 0:
        primary_method = FETCH_STATUS_SEARCH_HIT
    elif direct_fetches > 0:
        primary_method = FETCH_STATUS_DIRECT_PAGE
    else:
        primary_method = FETCH_STATUS_FAILED

    return {
        "benchmarkUsed": benchmark_used,
        "benchmarkUrl": benchmark_url,
        "benchmarkFetchStatus": primary_method,
        "benchmarkFetchMethod": primary_method,
        "avgBenchmarkPrice": avg_benchmark,
        "avgMarketPrice": avg_market,
        "marketAdjustmentPct": avg_adj,
        "appliedMarketWeight": BENCHMARK_MARKET_WEIGHT,
        "maxAdjCap": BENCHMARK_MAX_ADJ,
        "fallbackReason": fallback_reason,
        "fetchStats": {
            "searchHits": search_hits,
            "directFetches": direct_fetches,
            "failed": failed,
            "totalDays": total,
        },
    }


# ── Convert BenchmarkDayResult → plain dict for pipeline compatibility ────────

def benchmark_day_result_to_dict(r: BenchmarkDayResult) -> Dict[str, Any]:
    return {
        "date": r.date,
        "median_price": r.median_price,
        "benchmark_price": r.benchmark_price,
        "market_price": r.market_price,
        "market_adj_pct": r.market_adj_pct,
        "applied_adj_pct": r.applied_adj_pct,
        "benchmark_fetch_status": r.benchmark_fetch_status,
        "comps_collected": r.comps_collected,
        "comps_used": r.comps_used,
        "filter_stage": r.filter_stage,
        "flags": r.flags,
        "is_sampled": r.is_sampled,
        "is_weekend": r.is_weekend,
        "price_distribution": r.price_distribution,
        "top_comps": r.top_comps,
        "error": r.error,
    }
