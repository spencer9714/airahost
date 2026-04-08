"""
Nightly Crawl Strategy — budgeted, tiered date-selection for nightly jobs.

Nightly jobs are not full live analyses.  They are budgeted collectors designed
to minimise Airbnb request volume while preserving the two outcomes that matter:

  1. Fresh near-term market data for alert evaluation (D0–D3)
  2. Recurring market observations at lower cadence for ML training (D4+)

A NightlyCrawlPlan partitions a report window into four tiers:

  Tier        Date range    Observation cadence
  ──────────  ──────────    ─────────────────────────────────────────────────
  near_term   D0–D3         every night (highest freshness — alert-critical)
  medium      D4–D10        every TIER2_STEP nights (medium freshness)
  far         D11–D21       every TIER3_STEP nights (lower frequency)
  sparse      D22–D30       TIER4_COUNT endpoints only (minimal observation)

Unsampled nights are filled by linear interpolation inside the existing
`interpolate_missing_days()` pipeline — output shape is unchanged.

Circuit-breaker (early-stop):
  If the scrape loop sees EARLY_STOP_THRESHOLD consecutive date queries
  with no price results (challenge or empty search page), it stops crawling
  further to avoid hammering Airbnb.  Already-collected good observations are
  preserved and the rest are interpolated.

All thresholds are tunable via environment variables without a code deploy.

Interactive jobs are NOT affected by this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Config — tunable via environment variables
# ---------------------------------------------------------------------------

# D0-D(TIER1_END-1): observe every night
NIGHTLY_TIER1_END: int = int(os.getenv("NIGHTLY_TIER1_END", "4"))

# D(TIER1_END)-D(TIER2_END-1): observe every TIER2_STEP nights
NIGHTLY_TIER2_END: int = int(os.getenv("NIGHTLY_TIER2_END", "11"))
NIGHTLY_TIER2_STEP: int = int(os.getenv("NIGHTLY_TIER2_STEP", "2"))

# D(TIER2_END)-D(TIER3_END-1): observe every TIER3_STEP nights
NIGHTLY_TIER3_END: int = int(os.getenv("NIGHTLY_TIER3_END", "22"))
NIGHTLY_TIER3_STEP: int = int(os.getenv("NIGHTLY_TIER3_STEP", "3"))

# D(TIER3_END)-D(total_nights-1): observe only the first and last night of this segment
# TIER4_COUNT is the maximum number of observations in the sparse tier.
NIGHTLY_TIER4_COUNT: int = int(os.getenv("NIGHTLY_TIER4_COUNT", "2"))

# Hard cap: never observe more than this many nights regardless of tier math
NIGHTLY_MAX_OBSERVE_DATES: int = int(os.getenv("NIGHTLY_MAX_OBSERVE_DATES", "15"))

# Consecutive no-price results before triggering early-stop
NIGHTLY_EARLY_STOP_THRESHOLD: int = int(os.getenv("NIGHTLY_EARLY_STOP_THRESHOLD", "3"))

# Per-query scroll rounds for nightly (reduced from interactive defaults)
NIGHTLY_SCROLL_ROUNDS: int = int(os.getenv("NIGHTLY_SCROLL_ROUNDS", "1"))

# Per-query max cards for nightly (reduced from interactive defaults)
NIGHTLY_MAX_CARDS: int = int(os.getenv("NIGHTLY_MAX_CARDS", "20"))

# ---------------------------------------------------------------------------
# Benchmark-mode overrides (Mode C only)
# ---------------------------------------------------------------------------

# Hard cap for benchmark nightly observe dates — keeps benchmark path at parity
# with the old BENCHMARK_MAX_SAMPLE_QUERIES=10 before Phase 3 was introduced.
BENCHMARK_NIGHTLY_MAX_OBSERVE: int = int(os.getenv("BENCHMARK_NIGHTLY_MAX_OBSERVE", "10"))

# Per-query scroll rounds for benchmark nightly (match old BENCHMARK_SCROLL_ROUNDS)
BENCHMARK_NIGHTLY_SCROLL_ROUNDS: int = int(os.getenv("BENCHMARK_NIGHTLY_SCROLL_ROUNDS", "1"))

# Per-query max cards for benchmark nightly (match old BENCHMARK_MAX_CARDS=15)
BENCHMARK_NIGHTLY_MAX_CARDS: int = int(os.getenv("BENCHMARK_NIGHTLY_MAX_CARDS", "15"))


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------

@dataclass
class NightlyCrawlPlan:
    """
    Crawl plan for a single nightly job.

    observe_indices and infer_indices are integer offsets into the all_nights
    list produced by daterange_nights(start, end).  They are disjoint and
    together cover all nights in the window.

    observe_indices  — nights to directly query Airbnb for
    infer_indices    — nights to fill by interpolation (not queried)
    tier_debug       — per-tier breakdown for debug metadata
    scroll_rounds    — per-query scroll depth (overrides interactive default)
    max_cards        — per-query card limit (overrides interactive default)
    early_stop_threshold — consecutive empty results before stopping the loop
    total_nights     — total nights in the window
    """

    observe_indices: List[int]
    infer_indices: List[int]
    tier_debug: Dict[str, Any]
    scroll_rounds: int
    max_cards: int
    early_stop_threshold: int
    total_nights: int


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

def build_nightly_crawl_plan(
    total_nights: int,
    mode: str = "standard",
) -> NightlyCrawlPlan:
    """
    Build a NightlyCrawlPlan for a window of total_nights.

    Tiers are computed from the module-level config constants (which are
    populated from environment variables).  All indices are 0-based offsets
    into the all_nights list.

    For windows shorter than the tier boundaries, tiers that extend beyond
    total_nights are gracefully truncated.

    Args:
        total_nights: Number of nights in the report window (>= 1).
        mode: "standard" (default) for Mode A/B criteria/URL scrapes, or
              "benchmark" for Mode C benchmark scrapes.  Benchmark mode caps
              observe count at BENCHMARK_NIGHTLY_MAX_OBSERVE and uses
              benchmark-specific per-query limits, keeping the benchmark path
              at parity with its pre-Phase-3 volume.

    Returns:
        NightlyCrawlPlan with observe/infer split and debug metadata.
    """
    if total_nights < 1:
        total_nights = 1

    observe: List[int] = []
    tier_debug: Dict[str, Any] = {}

    # ── Tier 1: near-term — every night ──────────────────────────────────────
    t1_end = min(NIGHTLY_TIER1_END, total_nights)
    t1_indices = list(range(0, t1_end))
    observe.extend(t1_indices)
    tier_debug["near_term"] = {
        "range": f"D0-D{t1_end - 1}" if t1_end > 1 else "D0",
        "indices": t1_indices,
        "step": 1,
        "observed": len(t1_indices),
    }

    # ── Tier 2: medium — every TIER2_STEP nights ─────────────────────────────
    t2_start = t1_end
    t2_end = min(NIGHTLY_TIER2_END, total_nights)
    if t2_start < t2_end:
        step2 = max(1, NIGHTLY_TIER2_STEP)
        t2_indices = list(range(t2_start, t2_end, step2))
        observe.extend(t2_indices)
        tier_debug["medium"] = {
            "range": f"D{t2_start}-D{t2_end - 1}",
            "indices": t2_indices,
            "step": step2,
            "observed": len(t2_indices),
        }

    # ── Tier 3: far — every TIER3_STEP nights ────────────────────────────────
    t3_start = t2_end
    t3_end = min(NIGHTLY_TIER3_END, total_nights)
    if t3_start < t3_end:
        step3 = max(1, NIGHTLY_TIER3_STEP)
        t3_indices = list(range(t3_start, t3_end, step3))
        observe.extend(t3_indices)
        tier_debug["far"] = {
            "range": f"D{t3_start}-D{t3_end - 1}",
            "indices": t3_indices,
            "step": step3,
            "observed": len(t3_indices),
        }

    # ── Tier 4: sparse — only endpoint samples ───────────────────────────────
    t4_start = t3_end
    t4_end = total_nights
    if t4_start < t4_end:
        count = max(1, NIGHTLY_TIER4_COUNT)
        if count >= (t4_end - t4_start):
            # Sparse tier is short enough to observe all of it
            t4_indices = list(range(t4_start, t4_end))
        else:
            # Sample: always include first; add equally-spaced endpoints up to count
            t4_indices = [t4_start]
            if count >= 2:
                t4_indices.append(t4_end - 1)
            if count >= 3:
                # Fill any extra count slots evenly between first and last
                step4 = max(1, (t4_end - 1 - t4_start) // (count - 1))
                mid = t4_start + step4
                while mid < t4_end - 1 and len(t4_indices) < count:
                    t4_indices.append(mid)
                    mid += step4
                t4_indices = sorted(set(t4_indices))
        observe.extend(t4_indices)
        tier_debug["sparse"] = {
            "range": f"D{t4_start}-D{t4_end - 1}",
            "indices": t4_indices,
            "observed": len(t4_indices),
        }

    # ── Cap at safety maximum ─────────────────────────────────────────────────
    observe = sorted(set(observe))

    # Apply mode-specific hard cap.
    if mode == "benchmark":
        hard_cap = BENCHMARK_NIGHTLY_MAX_OBSERVE
    else:
        hard_cap = NIGHTLY_MAX_OBSERVE_DATES

    if len(observe) > hard_cap:
        # Keep the first hard_cap observations (near-term priority).
        observe = observe[:hard_cap]

    # Rebuild tier_debug to reflect what was actually included after capping.
    # Without this, tier_debug would show pre-cap planned indices which misrepresent
    # actual crawl scope when the cap trims the far/sparse tiers.
    observe_set = set(observe)
    for tier_name, tier in tier_debug.items():
        included = [i for i in tier["indices"] if i in observe_set]
        tier["indices"] = included
        tier["observed"] = len(included)

    infer = [i for i in range(total_nights) if i not in observe_set]

    if mode == "benchmark":
        eff_scroll_rounds = BENCHMARK_NIGHTLY_SCROLL_ROUNDS
        eff_max_cards = BENCHMARK_NIGHTLY_MAX_CARDS
    else:
        eff_scroll_rounds = NIGHTLY_SCROLL_ROUNDS
        eff_max_cards = NIGHTLY_MAX_CARDS

    return NightlyCrawlPlan(
        observe_indices=observe,
        infer_indices=infer,
        tier_debug=tier_debug,
        scroll_rounds=eff_scroll_rounds,
        max_cards=eff_max_cards,
        early_stop_threshold=NIGHTLY_EARLY_STOP_THRESHOLD,
        total_nights=total_nights,
    )
