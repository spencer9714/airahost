/**
 * Auto-Apply preview computation.
 *
 * Derives a per-night preview of what Auto-Apply would do over a given window,
 * applying all user guardrails (floor, caps, notice window) without touching
 * the canonical recommendedDailyPrice.
 *
 * Core invariant:
 *   finalAutoApplyPrice = max(recommendedDailyPrice, minPriceFloor)
 *                         then clamped by increase/decrease caps
 *
 * recommendedDailyPrice is NEVER modified. Both values are always shown
 * side-by-side so users understand the distinction.
 *
 * This module is pure — no I/O, no side effects. Safe to call in any context.
 */

import type { CalendarDay } from "@/lib/schemas";
import type { AutoApplySettings } from "@/components/dashboard/AutoApplyDrawer";

// ── Types ──────────────────────────────────────────────────────────────────

export type AdjustmentReason =
  | "none"               // finalPrice == rec; no guardrail applied
  | "floored"            // rec < floor → floor applied
  | "capped_increase"    // floor or other factor pushed price above cap → capped down
  | "capped_decrease"    // price fell below decrease cap → capped up (future use)
  | "floored_and_capped"; // floor applied, then increase cap reduced it

export type SkipReason =
  | "notice_window"  // check-in within minNoticeDays of today
  | "no_data";       // no calendar entry or no recommendation for this date

export interface AutoApplyNightPreview {
  /** YYYY-MM-DD */
  date: string;
  dayOfWeek: string;
  isWeekend: boolean;
  /**
   * Canonical recommendation (recommendedDailyPrice ?? basePrice).
   * NEVER modified by guardrails — always the original recommendation.
   */
  recommendedPrice: number | null;
  /**
   * The price Auto-Apply would use for this night.
   * = max(recommendedPrice, minPriceFloor), clamped by increase/decrease caps.
   * null when the night is skipped.
   */
  finalAutoApplyPrice: number | null;
  adjustmentReason: AdjustmentReason;
  skipped: boolean;
  skipReason: SkipReason | null;
}

export interface AutoApplyPreviewResult {
  // ── Range metadata ─────────────────────────────────────────────────
  /** Today's date (ISO) — window start. */
  rangeStart: string;
  /** rangeStart + windowEndDays (ISO) — window end, exclusive. */
  rangeEnd: string;
  /** When this preview was generated. */
  generatedAt: string;
  /** Date range covered by the source report, or null if no calendar data. */
  reportDateRange: { start: string; end: string } | null;

  // ── Night counts ───────────────────────────────────────────────────
  totalWindowNights: number;
  nightsWithData: number;
  nightsSkipped: number;     // notice window + no data
  nightsIncluded: number;    // nights that would receive a price
  nightsFloored: number;     // rec < floor — floor was applied
  nightsCappedIncrease: number;
  nightsCappedDecrease: number;

  // ── Price ranges (over included nights only) ───────────────────────
  recommendedPriceRange: { min: number; max: number } | null;
  finalApplyPriceRange: { min: number; max: number } | null;

  // ── Per-night detail ───────────────────────────────────────────────
  nights: AutoApplyNightPreview[];

  // ── Contiguity ─────────────────────────────────────────────────────
  /** True when all included nights form an unbroken consecutive run. */
  includedDatesContiguous: boolean;
  /** Sorted YYYY-MM-DD list of nights that would receive a price. */
  includedDates: string[];
  /** Sorted YYYY-MM-DD list of nights that are skipped or missing. */
  excludedDates: string[];

  // ── Scope note ─────────────────────────────────────────────────────
  /**
   * When applyScope = "actionable": explains that runtime will further
   * filter to only nights where live price is meaningfully mispriced.
   * null for "all_sellable" scope.
   */
  scopeNote: string | null;

  // ── Future-ready execution snapshot ───────────────────────────────
  /**
   * Frozen copy of the settings used to produce this preview.
   * A future execution layer can consume this directly to reproduce
   * the same result without re-reading user settings at apply time.
   */
  settingsSnapshot: {
    windowEndDays: number;
    applyScope: "actionable" | "all_sellable";
    minPriceFloor: number | null;
    minNoticeDays: number;
    maxIncreasePct: number | null;
    maxDecreasePct: number | null;
    skipUnavailableNights: boolean;
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────

const DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function utcDayOfWeek(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00Z");
  return DOW_NAMES[d.getUTCDay()];
}

function utcIsWeekend(dateStr: string): boolean {
  const dow = new Date(dateStr + "T00:00:00Z").getUTCDay();
  return dow === 0 || dow === 5 || dow === 6;
}

function addDays(dateStr: string, n: number): string {
  const ms = new Date(dateStr + "T00:00:00Z").getTime() + n * 86_400_000;
  return new Date(ms).toISOString().split("T")[0];
}

function isConsecutive(dates: string[]): boolean {
  for (let i = 1; i < dates.length; i++) {
    if (addDays(dates[i - 1], 1) !== dates[i]) return false;
  }
  return true;
}

// ── Core computation ───────────────────────────────────────────────────────

/**
 * Compute an Auto-Apply preview for the given calendar and settings.
 *
 * @param calendar  CalendarDay array from the latest pricing report.
 * @param settings  Persisted AutoApplySettings (windowEndDays, floor, caps, etc.).
 * @param today     Override today's date (YYYY-MM-DD). Defaults to actual UTC today.
 */
export function computeAutoApplyPreview(
  calendar: CalendarDay[],
  settings: AutoApplySettings,
  today?: string
): AutoApplyPreviewResult {
  const todayStr =
    today ?? new Date().toISOString().split("T")[0];

  const rangeEnd = addDays(todayStr, settings.windowEndDays);

  // Build O(1) lookup by date
  const calIndex = new Map<string, CalendarDay>();
  for (const day of calendar) {
    if (day.date) calIndex.set(day.date, day);
  }

  // Report date range
  const calDates = Array.from(calIndex.keys()).sort();
  const reportDateRange =
    calDates.length > 0
      ? { start: calDates[0], end: calDates[calDates.length - 1] }
      : null;

  const nights: AutoApplyNightPreview[] = [];
  let nightsWithData = 0;
  let nightsSkipped = 0;
  let nightsIncluded = 0;
  let nightsFloored = 0;
  let nightsCappedIncrease = 0;
  let nightsCappedDecrease = 0;

  for (let i = 0; i < settings.windowEndDays; i++) {
    const dateStr = addDays(todayStr, i);

    // ── Notice window check ────────────────────────────────────────
    if (settings.minNoticeDays > 0 && i < settings.minNoticeDays) {
      const calDay = calIndex.get(dateStr);
      nights.push({
        date: dateStr,
        dayOfWeek: calDay?.dayOfWeek ?? utcDayOfWeek(dateStr),
        isWeekend: calDay?.isWeekend ?? utcIsWeekend(dateStr),
        recommendedPrice:
          calDay != null
            ? (calDay.recommendedDailyPrice ?? calDay.basePrice ?? null)
            : null,
        finalAutoApplyPrice: null,
        adjustmentReason: "none",
        skipped: true,
        skipReason: "notice_window",
      });
      nightsSkipped++;
      continue;
    }

    // ── No calendar data ───────────────────────────────────────────
    const calDay = calIndex.get(dateStr);
    if (!calDay) {
      nights.push({
        date: dateStr,
        dayOfWeek: utcDayOfWeek(dateStr),
        isWeekend: utcIsWeekend(dateStr),
        recommendedPrice: null,
        finalAutoApplyPrice: null,
        adjustmentReason: "none",
        skipped: true,
        skipReason: "no_data",
      });
      nightsSkipped++;
      continue;
    }

    // ── Resolve recommendation ─────────────────────────────────────
    const rec: number | null =
      calDay.recommendedDailyPrice ?? calDay.basePrice ?? null;

    if (rec == null) {
      nights.push({
        date: dateStr,
        dayOfWeek: calDay.dayOfWeek,
        isWeekend: calDay.isWeekend,
        recommendedPrice: null,
        finalAutoApplyPrice: null,
        adjustmentReason: "none",
        skipped: true,
        skipReason: "no_data",
      });
      nightsSkipped++;
      continue;
    }

    nightsWithData++;

    // ── Apply guardrails (rec is never mutated) ────────────────────
    let finalPrice = rec;
    let reason: AdjustmentReason = "none";

    // 1. Floor: finalPrice = max(rec, floor)
    if (settings.minPriceFloor != null && rec < settings.minPriceFloor) {
      finalPrice = settings.minPriceFloor;
      reason = "floored";
      nightsFloored++;
    }

    // 2. Increase cap: clamp finalPrice ≤ rec × (1 + cap%)
    //    (applies when floor pushed price above the cap)
    if (settings.maxIncreasePct != null) {
      const cap = rec * (1 + settings.maxIncreasePct / 100);
      if (finalPrice > cap) {
        finalPrice = Math.round(cap);
        reason = reason === "floored" ? "floored_and_capped" : "capped_increase";
        nightsCappedIncrease++;
      }
    }

    // 3. Decrease cap: clamp finalPrice ≥ rec × (1 − cap%)
    //    (future-ready: fires when downward adjustments are added)
    if (settings.maxDecreasePct != null) {
      const floor = rec * (1 - settings.maxDecreasePct / 100);
      if (finalPrice < floor) {
        finalPrice = Math.round(floor);
        reason = "capped_decrease";
        nightsCappedDecrease++;
      }
    }

    nights.push({
      date: dateStr,
      dayOfWeek: calDay.dayOfWeek,
      isWeekend: calDay.isWeekend,
      recommendedPrice: rec,
      finalAutoApplyPrice: Math.round(finalPrice),
      adjustmentReason: reason,
      skipped: false,
      skipReason: null,
    });
    nightsIncluded++;
  }

  // ── Aggregates ─────────────────────────────────────────────────────
  const included = nights.filter(
    (n) => !n.skipped && n.recommendedPrice != null && n.finalAutoApplyPrice != null
  );

  const recommendedPriceRange =
    included.length > 0
      ? {
          min: Math.min(...included.map((n) => n.recommendedPrice!)),
          max: Math.max(...included.map((n) => n.recommendedPrice!)),
        }
      : null;

  const finalApplyPriceRange =
    included.length > 0
      ? {
          min: Math.min(...included.map((n) => n.finalAutoApplyPrice!)),
          max: Math.max(...included.map((n) => n.finalAutoApplyPrice!)),
        }
      : null;

  const includedDates = included.map((n) => n.date).sort();
  const excludedDates = nights
    .filter((n) => n.skipped)
    .map((n) => n.date)
    .sort();

  const includedDatesContiguous =
    includedDates.length <= 1 || isConsecutive(includedDates);

  const scopeNote =
    settings.applyScope === "actionable"
      ? "Actionable scope: at runtime, only nights where your live price is meaningfully above or below the recommendation will be included. This preview shows all calendar nights — the final applied count may be lower."
      : null;

  return {
    rangeStart: todayStr,
    rangeEnd,
    generatedAt: new Date().toISOString(),
    reportDateRange,
    totalWindowNights: settings.windowEndDays,
    nightsWithData,
    nightsSkipped,
    nightsIncluded,
    nightsFloored,
    nightsCappedIncrease,
    nightsCappedDecrease,
    recommendedPriceRange,
    finalApplyPriceRange,
    nights,
    includedDatesContiguous,
    includedDates,
    excludedDates,
    scopeNote,
    settingsSnapshot: {
      windowEndDays: settings.windowEndDays,
      applyScope: settings.applyScope,
      minPriceFloor: settings.minPriceFloor,
      minNoticeDays: settings.minNoticeDays,
      maxIncreasePct: settings.maxIncreasePct,
      maxDecreasePct: settings.maxDecreasePct,
      skipUnavailableNights: settings.skipUnavailableNights,
    },
  };
}
