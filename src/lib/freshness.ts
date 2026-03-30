/**
 * Forecast freshness model
 *
 * All freshness logic lives here so thresholds are applied consistently
 * across the dashboard, listing detail page, and any future components.
 *
 * Source-of-truth field: pricing_reports.market_captured_at
 *   – set by the worker (complete_job) for fresh scrapes
 *   – set to cache entry created_at for cache-hit reports
 *   – set to source report's market_captured_at for forecast_snapshot reports
 *
 * Fallback: when market_captured_at is null (older reports pre-migration),
 * callers should fall back to listing_reports.created_at and treat the
 * result as approximate.
 */

// ── Thresholds ────────────────────────────────────────────────────────────────

export const FRESHNESS_THRESHOLDS = {
  /** Up to this many days old → fresh */
  FRESH_MAX_DAYS: 3,
  /** Up to this many days old → aging (above FRESH_MAX_DAYS) */
  AGING_MAX_DAYS: 7,
  // > AGING_MAX_DAYS → stale
} as const;

// ── Status type ───────────────────────────────────────────────────────────────

export type FreshnessStatus = "fresh" | "aging" | "stale" | "missing";

export interface FreshnessInfo {
  status: FreshnessStatus;
  /** Whole days since market_captured_at, null when missing */
  daysAgo: number | null;
  /** Short human label: "Today", "Yesterday", "3d ago", etc. */
  label: string;
  /** Tailwind bg-* class for the status dot */
  dotClass: string;
  /** Optional advisory hint shown below the market basis row */
  hint: string | null;
}

// ── Core function ─────────────────────────────────────────────────────────────

/**
 * Compute freshness from a market_captured_at ISO timestamp (or null).
 *
 * @param marketCapturedAt  pricing_reports.market_captured_at, or a fallback
 *                          timestamp when the field is not yet populated.
 */
export function computeFreshness(
  marketCapturedAt: string | null | undefined
): FreshnessInfo {
  if (!marketCapturedAt) {
    return {
      status: "missing",
      daysAgo: null,
      label: "No market data",
      dotClass: "bg-gray-300",
      hint: null,
    };
  }

  const days = Math.floor(
    (Date.now() - new Date(marketCapturedAt).getTime()) / 86_400_000
  );

  if (days <= FRESHNESS_THRESHOLDS.FRESH_MAX_DAYS) {
    return {
      status: "fresh",
      daysAgo: days,
      label:
        days === 0 ? "Today"
        : days === 1 ? "Yesterday"
        : `${days}d ago`,
      dotClass: "bg-emerald-400",
      hint: null,
    };
  }

  if (days <= FRESHNESS_THRESHOLDS.AGING_MAX_DAYS) {
    return {
      status: "aging",
      daysAgo: days,
      label: `${days}d ago`,
      dotClass: "bg-amber-400",
      hint: "Consider refreshing for more accurate pricing",
    };
  }

  return {
    status: "stale",
    daysAgo: days,
    label: `${days}d ago`,
    dotClass: "bg-rose-400",
    hint: "Market data is stale — run a fresh analysis for accurate pricing",
  };
}

// ── Derived helpers ───────────────────────────────────────────────────────────

/** Returns the best available market-capture timestamp for freshness. */
export function resolveMarketCapturedAt(
  report: {
    market_captured_at?: string | null;
    completed_at?: string | null;
    created_at?: string | null;
  } | null | undefined,
  /** Fallback: listing_reports.created_at (pre-migration approximation) */
  linkedAt?: string | null
): string | null {
  return (
    report?.market_captured_at ??
    report?.completed_at ??
    linkedAt ??
    report?.created_at ??
    null
  );
}

/**
 * For forecast_snapshot reports, inherit market_captured_at from the
 * source live_analysis report rather than using the forecast generation time.
 *
 * Contract:
 *   A forecast_snapshot derives its pricing from a prior live_analysis.
 *   The market data age (freshness) should reflect when that live_analysis
 *   captured Airbnb data — not when the forecast was computed.
 *
 * Usage (forecast generation API or worker):
 *   const sourceMCT = resolveSnapshotMarketCapturedAt(sourceLiveReport);
 *   // write sourceMCT as the forecast_snapshot report's market_captured_at
 *   // (pass as source_market_captured_at to complete_job() in db.py)
 *
 * Falls back through completed_at → linkedAt → created_at, so pre-migration
 * source reports still produce a reasonable approximate timestamp.
 */
export function resolveSnapshotMarketCapturedAt(
  sourceLiveAnalysisReport: {
    market_captured_at?: string | null;
    completed_at?: string | null;
    created_at?: string | null;
  } | null | undefined,
  /** Optional: listing_reports.created_at for the source report's link row */
  sourceLinkedAt?: string | null
): string | null {
  return resolveMarketCapturedAt(sourceLiveAnalysisReport, sourceLinkedAt);
}
