/**
 * Auto-Apply execution plan model.
 *
 * A ManualApplyPlan is a concrete, per-night description of what the system
 * intends to apply. It is derived from an AutoApplyPreviewResult so that the
 * preview and execution layers share the same guardrail logic without
 * duplicating it.
 *
 * Separation of concerns:
 *   AutoApplyPreviewResult — read-only dry-run view (UI layer)
 *   ManualApplyPlan        — executable intent (execution layer)
 *   ApplyExecutorResult    — outcome after execution (audit layer)
 *
 * The executor receives a ManualApplyPlan and returns an ApplyExecutorResult.
 * The UI uses both for the confirmation and result screens.
 */

import type {
  AutoApplyPreviewResult,
  AdjustmentReason,
  SkipReason,
} from "./autoApplyPreview";

// ── Night-level types ──────────────────────────────────────────────────────

export type NightApplyStatus =
  | "planned"            // included in plan, awaiting execution
  | "skipped"            // excluded (notice_window / no_data / unavailable)
  | "simulated_success"  // stub: execution simulated as successful
  | "simulated_failure"; // stub: execution simulated as failed (reserved for future)

export interface NightExecutionPlan {
  date: string;
  dayOfWeek: string;
  isWeekend: boolean;
  /**
   * Canonical recommendation — NEVER modified.
   * Displayed alongside finalAppliedPrice so the distinction is always clear.
   */
  recommendedPrice: number | null;
  /**
   * Price the executor will apply: max(recommendedPrice, floor) + caps.
   * null when night is skipped.
   */
  finalAppliedPrice: number | null;
  /**
   * Known live Airbnb price at plan-build time, if available.
   * Currently always null — populated in the live execution phase.
   */
  currentKnownPrice: number | null;
  included: boolean;
  skipReason: SkipReason | null;
  guardrailsApplied: AdjustmentReason;
  applyStatus: NightApplyStatus;
}

// ── Plan-level types ───────────────────────────────────────────────────────

export interface ManualApplyPlan {
  /** Unique run identifier, generated at plan-build time. */
  planId: string;
  listingId: string;
  reportId: string | null;
  rangeStart: string;
  rangeEnd: string;
  totalSelectedNights: number;
  nightsIncluded: number;
  nightsSkipped: number;
  nightsFloored: number;
  nightsCapped: number;
  generatedAt: string;
  initiatedByUser: boolean;
  nights: NightExecutionPlan[];
  /** Frozen settings snapshot — executor uses this, not live user settings. */
  settingsSnapshot: AutoApplyPreviewResult["settingsSnapshot"];
  /**
   * "stub" = current phase; no real Airbnb writes.
   * "live" = future phase; real write-back enabled.
   */
  executionMode: "stub" | "live";
}

// ── Builder ────────────────────────────────────────────────────────────────

/**
 * Build a ManualApplyPlan from an AutoApplyPreviewResult.
 *
 * This is the single bridge between preview and execution. All guardrail
 * logic lives in computeAutoApplyPreview — this function only maps the
 * result into the execution structure without re-applying any rules.
 */
export function buildManualApplyPlan(
  preview: AutoApplyPreviewResult,
  listingId: string,
  reportId: string | null
): ManualApplyPlan {
  const nights: NightExecutionPlan[] = preview.nights.map((n) => ({
    date: n.date,
    dayOfWeek: n.dayOfWeek,
    isWeekend: n.isWeekend,
    recommendedPrice: n.recommendedPrice,
    finalAppliedPrice: n.finalAutoApplyPrice,
    currentKnownPrice: null,
    included: !n.skipped,
    skipReason: n.skipReason,
    guardrailsApplied: n.adjustmentReason,
    applyStatus: n.skipped ? "skipped" : "planned",
  }));

  return {
    planId: crypto.randomUUID(),
    listingId,
    reportId,
    rangeStart: preview.rangeStart,
    rangeEnd: preview.rangeEnd,
    totalSelectedNights: preview.totalWindowNights,
    nightsIncluded: preview.nightsIncluded,
    nightsSkipped: preview.nightsSkipped,
    nightsFloored: preview.nightsFloored,
    nightsCapped: preview.nightsCappedIncrease + preview.nightsCappedDecrease,
    generatedAt: new Date().toISOString(),
    initiatedByUser: true,
    nights,
    settingsSnapshot: preview.settingsSnapshot,
    executionMode: "stub",
  };
}
