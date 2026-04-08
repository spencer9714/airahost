# Report Contract — Execution Policy & Source-of-Truth Rules

---

## Canonical Pricing Contract

### One Pricing Truth

All user-facing price displays (dashboard board, chart recommended series, report
price calendar, email/alert recommendation) must derive from one canonical daily
recommended price field:

```
CalendarDay.recommendedDailyPrice
```

**Definition:** The demand-adjusted recommended listing price for that specific
date. Computed as `perDayMarketMedian × demandAdjustment`, where `demandAdjustment`
accounts for weekend premium (+8% Fri/Sat, +4% Sun), peak/event flags (+15%),
low-demand signals (−15%), and market price tightness. Range: ~0.90–1.05× the
raw market median. Last-minute time discounts (dates within 30 days) are
**intentionally excluded** — this is a recommended list price, not a dynamic
revenue-strategy signal. Falls back to the window-level overall median for
interpolated or missing-data days.

**Top-level `recommendedPrice.nightly`:** Always equals `calendar[0].recommendedDailyPrice`
(the canonical recommended price for the report start date). This is the primary
"Recommended Price" shown in the dashboard banner and report hero.

### User-Facing Price Roles

| Field | Role | Where it appears |
|---|---|---|
| `CalendarDay.recommendedDailyPrice` | **Canonical daily recommendation** | Board tiles, chart recommended line, report calendar |
| `summary.recommendedPrice.nightly` | **Top-level recommended price** = day-0 `recommendedDailyPrice` | Dashboard banner, report hero, alert emails |
| `CalendarDay.baseDailyPrice` | **Market reference** — raw per-day market median, no adjustments | Chart market line, transparency/debug |
| `summary.nightlyMedian` | **Market proxy** — window-level midpoint derived from legacy `basePrice` (backward-compatible; may include time/demand adjustments on near-term dates) | Dashboard KPI, report market snapshot, alert comparison |
| `summary.observedListingPrice` | **Host's live Airbnb price** — scraped at report time | Dashboard banner hero, chart live marker |
| `summary.recommendedPrice.windowMedian` | Secondary context: 30-day similarity-weighted engine recommendation | Not shown in primary UI; available for transparency / future use |

### Market Reference vs Recommendation

These are **distinct values** and must not be conflated in UI:

- **`baseDailyPrice`** — raw observed market median for that date (what comparable
  listings actually charge on that day, unmodified)
- **`recommendedDailyPrice`** — what we suggest the host should charge (market
  median adjusted for demand signals; deliberately excludes last-minute discounts)

A typical weekend: `baseDailyPrice = $120`, `recommendedDailyPrice = $130` (1.08×).
A low-demand weekday: `baseDailyPrice = $90`, `recommendedDailyPrice = $81` (0.90×).

### Legacy / Internal Fields — Do Not Build New UI On These

The following fields remain in the payload for backward compatibility and internal
transparency. They must **not** drive primary user-facing price displays:

| Field | Role | Why not for primary UI |
|---|---|---|
| `CalendarDay.basePrice` | Legacy compat: equals `priceAfterTimeAdjustment` (or `overallMedian` for missing days) | Named ambiguously; pre-dates canonical field |
| `CalendarDay.refundablePrice` | Legacy: `priceAfterTimeAdjustment` with weekly/monthly discount applied | Discount stack removed from user-facing product |
| `CalendarDay.nonRefundablePrice` | Legacy: `refundablePrice` with non-refundable discount | Discount stack removed from user-facing product |
| `CalendarDay.priceAfterTimeAdjustment` | Internal: `baseDailyPrice × finalMultiplier` (time + demand) | Includes last-minute discount — not a recommended list price |
| `CalendarDay.effectiveDailyPriceRefundable` | Internal: full discount stack applied on top of `priceAfterTimeAdjustment` | Fully discounted — internal pipeline stage |
| `CalendarDay.effectiveDailyPriceNonRefundable` | Internal: same + non-refundable discount | Fully discounted — internal pipeline stage |
| `recommendedPrice.windowMedian` | Pricing engine's 30-day similarity-weighted recommendation | Single window value, not a per-day series; preserved as secondary context |

### Backward Compatibility

Old reports (pre-contract) do not have `recommendedDailyPrice`. UI components
must fall back to `basePrice` when `recommendedDailyPrice` is absent:

```typescript
// ✓ Correct — canonical with legacy fallback
const displayPrice = day.recommendedDailyPrice ?? day.basePrice;

// ✗ Wrong — basePrice is not the canonical recommendation name
const displayPrice = day.basePrice;

// ✗ Wrong — market reference is not the same as recommendation
const displayPrice = day.baseDailyPrice;
```

### Demo / Mock Reports

`src/core/pricingCore.ts` generates deterministic demo data. It uses `basePrice`
as its primary field (set to a demand-adjusted value with weekend boost already
baked in), and also emits `recommendedDailyPrice` equal to `basePrice` for new
canonical consumers. The market/recommendation distinction is not meaningful for
demo data — both fields carry the same simulated value.

---

## Overview

Reports created after Phase 1 of the report contract carry an explicit
`result_core_debug.execution_policy` field that encodes the **intended role** of
that report.  It is written at creation time by the originating API route and
is never mutated by the worker.

Older reports that predate Phase 1 do not have this field.  The worker resolves
the effective policy by falling back to `job_lane` when the explicit field is
absent (see [Execution Policy Resolution](#execution-policy-resolution)).

Consumers must not rely on JSON key order inside `result_core_debug`; read the
`execution_policy` value by key name.

---

## Report Types

### `nightly_board_refresh`

| Field | Value |
|---|---|
| `job_lane` | `nightly` |
| `trigger` (listing_reports) | `scheduled` |
| `report_type` | `live_analysis` |
| Origin | `POST /api/internal/nightly/schedule` (Railway cron, once/day) |

**Role:** Provides fresh market data for the dashboard board.  Once a report
with this policy transitions to `status=ready`, it becomes the
**dashboard source-of-truth** for that listing.

**Worker contract:** Performs a live scrape (`force_rerun: true`, no cache
served).  `completed_at` and `market_captured_at` are set by the worker, not
the API, because there is no cache-hit fast path for nightly reports.
Saved-listing inputs (address, attributes, discount policy) are live-reloaded
from the `saved_listings` row at execution time so that changes made after
queuing are reflected in the report.

---

### `interactive_live_report`

| Field | Value |
|---|---|
| `job_lane` | `interactive` |
| `trigger` (listing_reports) | `manual` or `rerun` |
| `report_type` | `live_analysis` |
| Origin | `POST /api/reports`, `POST /api/reports` (listing shorthand), `POST /api/listings/[id]/rerun` |

**Role:** User-initiated analysis with custom date ranges.  These reports
appear in the history panel but **never replace the board**.

**Worker contract:** May serve a cache hit (24 h TTL).  If `status=ready` and
`core_version=cache-hit`, the report was finalised by the API before enqueueing.
Inputs are not reloaded from `saved_listings`; the queued snapshot reflects the
user's deliberate choices at the time they initiated the analysis.

**Phase 6A — Observation-first reuse (criteria modes only):**  Before live-scraping,
the worker checks `target_price_observations` for fresh coverage of the requested
date window.  If all dates pass freshness thresholds, the report is assembled from
stored data without Airbnb scraping (`core_version` ends with `+obs_reuse`).  Falls
back silently to the live scrape path when coverage is missing, stale, or insufficient.
URL mode is excluded — it always scrapes to extract real listing specs.  See
`worker/core/observation_reuse.py` for freshness thresholds and the eligibility gate.

---

### `nightly_alert_training_refresh` *(reserved — not yet emitted)*

Intended for a future phase where a separate nightly pass feeds the alert-model
training pipeline.  The policy value is defined in `src/lib/reportPolicy.ts`
(TypeScript) and `worker/core/report_policy.py` (Python) so the type is
registered; no route emits it yet.

---

## Dashboard Source-of-Truth Rule

`GET /api/listings` enforces the following selection logic (see `route.ts`):

```
latestReport = most recent link where:
  trigger     = "scheduled"
  status      = "ready"
  report_type ≠ "forecast_snapshot"
```

Interactive reports (`interactive_live_report`) are **never** selected as
`latestReport`.  They surface only in `recentReports` (history panel).

The `execution_policy` field makes the intended routing machine-readable, but
the dashboard selection query continues to use `trigger + status + report_type`
from the DB columns directly (no schema change in this phase).

---

## Execution Policy Resolution

The worker resolves the effective policy in `worker/core/report_policy.py`
via `resolve_execution_policy(job)`:

1. **Explicit** — if `result_core_debug.execution_policy` is present and
   a recognised value, use it.  This applies to all reports created after
   Phase 1 of the report contract.
2. **Derived** — for legacy rows that predate the explicit field, derive from
   `job_lane`:
   - `job_lane = "nightly"` → `nightly_board_refresh`
   - `job_lane = "interactive"` → `interactive_live_report`

The `trigger` field lives in `listing_reports`, not in the claimed
`pricing_reports` row, so it is not available to the worker at execution
time without an extra query.  `job_lane` alone is the reliable discriminator.

---

## Worker Pipeline Dispatch

`process_job()` in `worker/main.py` is a thin dispatcher:

```
process_job(job, worker_token)
  ├── reject forecast_snapshot (deprecated)
  ├── resolve_execution_policy(job)          # explicit → derived fallback
  ├── log: job_id, job_lane, execution_policy, pipeline
  ├── nightly_board_refresh  → run_nightly_job()   → _execute_analysis(is_nightly=True)
  └── interactive_live_report → run_interactive_job() → _execute_analysis(is_nightly=False)
```

`run_nightly_job()` and `run_interactive_job()` are explicit pipeline entry
points.  Both delegate to the shared `_execute_analysis()` engine; they exist
as named hooks for nightly-only or interactive-only pre/post logic in future
phases.

---

## Metadata Stamped at Creation

All creation routes call `executionPolicyMeta(policy)` from
`src/lib/reportPolicy.ts` and spread the result into `result_core_debug`:

```typescript
result_core_debug: {
  ...executionPolicyMeta("nightly_board_refresh"),  // or "interactive_live_report"
  cache_hit: ...,
  request_source: ...,
  // other fields follow — no guaranteed key order
}
```

`result_core_debug` is a JSONB append-only audit log.  Key order within the
object is not part of the contract; always read `execution_policy` by key name.

---

## Impacted Routes (audit summary)

| Route | Policy stamped | Trigger written | job_lane |
|---|---|---|---|
| `POST /api/internal/nightly/schedule` | `nightly_board_refresh` | `scheduled` | `nightly` |
| `POST /api/reports` (full) | `interactive_live_report` | `manual` | `interactive` |
| `POST /api/reports` (listing shorthand) | `interactive_live_report` | `manual` | `interactive` |
| `POST /api/listings/[id]/rerun` | `interactive_live_report` | `rerun` | `interactive` |

`GET /api/listings` — read path only; no policy emitted.  Selection logic
unchanged; `execution_policy` is available for future filtering if needed.

`src/app/dashboard/page.tsx` — read-only frontend; no changes required.

---

## Nightly Crawl Strategy (Phase 3)

Nightly jobs are **budgeted collectors**, not full 30-day live analyses.  They
use a tiered date-selection plan built in `worker/core/nightly_strategy.py` to
minimise Airbnb request volume while preserving the two outcomes that matter:

1. **Fresh near-term data** for alert evaluation (D0–D3)
2. **Recurring market observations** at lower cadence for future ML training (D4+)

### Date Tiers (defaults, all tunable via env vars)

| Tier | Date range | Cadence | Env var (boundary) |
|---|---|---|---|
| `near_term` | D0 – D3 | Every night | `NIGHTLY_TIER1_END=4` |
| `medium` | D4 – D10 | Every 2 nights | `NIGHTLY_TIER2_END=11`, `NIGHTLY_TIER2_STEP=2` |
| `far` | D11 – D21 | Every 3 nights | `NIGHTLY_TIER3_END=22`, `NIGHTLY_TIER3_STEP=3` |
| `sparse` | D22 – D29 | First + last only | `NIGHTLY_TIER4_COUNT=2` |

**Hard cap**: `NIGHTLY_MAX_OBSERVE_DATES=15` — never observe more than this many
nights regardless of tier math.

For a standard 30-night window this produces **~14 observed nights** and **~16
interpolated nights**.  By comparison, the interactive path samples ~16 of 30
nights via `compute_sample_dates()` (`step = ceil(30 / MAX_SAMPLE_QUERIES) = 2`),
so nightly and interactive coverage are roughly equivalent for a 30-night window.
Unsampled nights are filled by the existing `interpolate_missing_days()` linear
interpolation — output shape is unchanged.

### Per-query limits

Nightly queries use reduced scroll depth and card limits to further cut request
volume per observed night.  Mode C (benchmark) uses a separate, tighter cap
that keeps its volume at parity with the pre-Phase-3 `BENCHMARK_MAX_SAMPLE_QUERIES=10`
interactive baseline:

| Setting | Interactive default | Nightly (A/B) | Nightly (C benchmark) | Env var |
|---|---|---|---|---|
| Scroll rounds | `DAY_QUERY_SCROLL_ROUNDS` (2) | 1 | 1 | `NIGHTLY_SCROLL_ROUNDS` / `BENCHMARK_NIGHTLY_SCROLL_ROUNDS` |
| Max cards | `DAY_QUERY_MAX_CARDS` (30) | 20 | 15 | `NIGHTLY_MAX_CARDS` / `BENCHMARK_NIGHTLY_MAX_CARDS` |
| Max observe dates | — | 15 | 10 | `NIGHTLY_MAX_OBSERVE_DATES` / `BENCHMARK_NIGHTLY_MAX_OBSERVE` |

`build_nightly_crawl_plan(total_nights, mode="benchmark")` is called for Mode C;
`mode="standard"` (default) is used for Modes A and B.  Criteria Mode B Pass 1
(`scroll_and_collect` anchor search) also uses `nightly_plan.scroll_rounds` /
`nightly_plan.max_cards` when a nightly plan is active, reducing its anchor-search
volume on nightly runs.

### Circuit-breaker (early-stop)

If the scrape loop sees `NIGHTLY_EARLY_STOP_THRESHOLD` (default: 3) consecutive
date queries with no price results — a signal of Airbnb challenge pages or
empty search results — the loop halts without querying the remaining planned
dates.  Already-collected observations are preserved and the rest are
interpolated.

The job still completes (does not fail) if there are enough valid prices to
build a calendar.  `result_core_debug.nightly_crawl.early_stop_triggered` is
set to `true` when this fires.

### Debug metadata

Every nightly fresh-scrape report (not cache-hit) includes a
`result_core_debug.nightly_crawl` block:

```json
{
  "total_nights": 30,
  "observed_count": 12,
  "queried_count": 13,
  "infer_count": 18,
  "early_stop_triggered": false,
  "consecutive_empty_peak": 1,
  "scroll_rounds": 1,
  "max_cards": 20,
  "tiers": {
    "near_term": { "range": "D0-D3",   "indices": [0,1,2,3],        "step": 1, "observed": 4 },
    "medium":    { "range": "D4-D10",  "indices": [4,6,8,10],       "step": 2, "observed": 4 },
    "far":       { "range": "D11-D21", "indices": [11,14,17,20],    "step": 3, "observed": 4 },
    "sparse":    { "range": "D22-D29", "indices": [22,29],                     "observed": 2 }
  },
  "planned_observe_indices":  [0,1,2,3,4,6,8,10,11,14,17,20,22,29],
  "actual_queried_indices":   [0,1,2,3,4,6,8,10,11,14,17,20,22],
  "actual_observed_indices":  [0,1,2,3,4,6,8,10,11,14,17,22],
  "actual_inferred_indices":  [5,7,9,12,13,15,16,18,19,21,23,24,25,26,27,28,29]
}
```

Field semantics:
- `planned_observe_indices` — nights the plan intended to query (before execution)
- `actual_queried_indices` — nights actually sent to Airbnb (may be shorter if early-stop or timeout fired)
- `actual_observed_indices` — queried nights that returned a non-null `median_price`
- `actual_inferred_indices` — all remaining nights filled by interpolation
- `observed_count` / `queried_count` / `infer_count` — lengths of the above lists

`tiers` indices are updated after the hard-cap is applied, reflecting the indices
actually included in `planned_observe_indices` rather than the pre-cap planned set.

Interactive reports do not include `nightly_crawl` — its absence indicates a
standard interactive run.

### Interactive jobs: unchanged

`run_interactive_job()` passes `nightly_plan=None` to all scrape functions.
When `nightly_plan` is `None`, every scrape function falls through to its
existing sampling logic (`compute_sample_dates()` / `BENCHMARK_MAX_SAMPLE_QUERIES`),
card limits, and scroll rounds.  No behavior change for interactive paths.

---

## Benchmark / Comparable Architecture (Phase 4)

### Shared utilities

Two modules centralise logic previously duplicated between the standard
(`day_query.py`) and benchmark (`benchmark.py`) pipelines:

| Module | Contents |
|---|---|
| `worker/core/comp_utils.py` | `build_comp_id`, `build_comp_prices_dict`, `compute_price_distribution`, `to_comparable_payload` |
| `worker/scraper/comp_collection.py` | `collect_search_comps` — 2-night-primary / 1-night-fallback search loop |

### `collect_search_comps`

Both pipelines now call a single search loop that:
- executes the 2-night-primary / 1-night-fallback Airbnb search
- extracts approximate lat/lng via `extract_comp_coords` and assigns coordinates
  to parsed specs — this now runs in both paths (previously only in standard).
  The coordinates feed the geo-distance filter (`apply_geo_filter`) so benchmark
  market comps are filtered correctly by distance.  They do **not** appear in the
  comparable listing payload for the benchmark path: `to_comparable_payload` is
  called without `include_geo=True` in `benchmark.py`, so `distanceKm`, `lat`,
  and `lng` are absent from benchmark comparable output (same as before Phase 4).
- applies optional self-URL exclusion (`exclude_url=target.url` for standard,
  `None` for benchmark so the benchmark listing stays in results for Stage-1 capture)
- returns `(priced_comps, query_nights_used)`

### `to_comparable_payload`

Unified comparable payload builder used by both pipelines.  The `isPinnedBenchmark`
field is **not** emitted by this function; callers in `benchmark.py` add it
explicitly so the standard path continues to omit the field (preserving output
backward compatibility).

### Benchmark-specific logic retained separately

The following benchmark concerns remain in `benchmark.py` and are not shared:
- Stage 1 direct-page price extraction (`_extract_benchmark_price_with_min_stay_fallback`)
- Benchmark vs market blend formula (confidence weighting, outlier guard, ±15% cap)
- Secondary comp consensus signal and aggregate transparency stats
- The pinned-benchmark payload block (uses `bm_spec` which may be `None`)

---

## Next Phases

1. **Emit `nightly_alert_training_refresh`** when the alert model training
   pipeline is wired to the scheduler, and add routing in `run_nightly_job()`
   to split the two nightly policy types.
2. **Add a DB column** (`execution_policy TEXT`) once the JSONB field has
   proven stable in production — migration can backfill from
   `deriveExecutionPolicy(job_lane, trigger)` (TypeScript) or
   `resolve_execution_policy(job)` (Python).
3. **Per-tier card/scroll tuning** — the nightly strategy module supports
   different limits per tier; default is uniform reduced settings for now.
