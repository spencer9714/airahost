/**
 * Airbnb price apply executor.
 *
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │  STUB — Airbnb write-back is NOT yet implemented.                   │
 * │  All nights are returned as `simulated_success`.                    │
 * │  No prices are changed on the Airbnb platform.                      │
 * └─────────────────────────────────────────────────────────────────────┘
 *
 * Interface contract (must remain stable for the live phase):
 *   Input:  ManualApplyPlan (validated, ownership-checked by the API route)
 *   Output: ApplyExecutorResult (per-night status + run summary)
 *
 * To enable real execution in the next phase:
 *   1. Replace the body of `executeNightPrice` with a real Airbnb API call.
 *   2. Update `executionMode` to "live" and `executionModeNote` accordingly.
 *   3. Do NOT change the ApplyExecutorResult shape — the UI and audit layer
 *      depend on it being stable.
 */

import type { ManualApplyPlan, NightExecutionPlan } from "./autoApplyPlan";

// ── Result types ───────────────────────────────────────────────────────────

export interface NightApplyResult {
  date: string;
  /** Status after execution attempt. */
  applyStatus: "simulated_success" | "simulated_failure" | "skipped";
  /** Price that was (or would have been) written. null when skipped. */
  finalAppliedPrice: number | null;
  /** Error detail if applyStatus === "simulated_failure". */
  errorMessage: string | null;
}

export interface ApplyExecutorResult {
  runId: string;
  listingId: string;
  /**
   * "stub" = simulated; "live" = real Airbnb write-back.
   * Always "stub" in the current phase.
   */
  executionMode: "stub" | "live";
  /**
   * Human-readable note surfaced in the UI and audit log.
   * Explains the execution mode so users are never misled.
   */
  executionModeNote: string;
  nightsSimulatedSuccess: number;
  nightsSimulatedFailed: number;
  nightsSkipped: number;
  nightsTotal: number;
  completedAt: string;
  nights: NightApplyResult[];
}

// ── Per-night stub ─────────────────────────────────────────────────────────

/**
 * Attempt to apply a single night's price.
 *
 * STUB: returns simulated_success without calling Airbnb.
 *
 * TODO (live phase): replace the stub body with:
 *   const result = await airbnbPricingClient.setNightlyPrice(
 *     listingId,
 *     night.date,
 *     night.finalAppliedPrice
 *   );
 *   if (!result.ok) {
 *     return { date: night.date, applyStatus: "failed", finalAppliedPrice: null,
 *              errorMessage: result.errorMessage };
 *   }
 *   return { date: night.date, applyStatus: "success", finalAppliedPrice: night.finalAppliedPrice,
 *            errorMessage: null };
 *
 * Signature must remain:
 *   (night: NightExecutionPlan, listingId: string) => Promise<NightApplyResult>
 */
async function executeNightPrice(
  night: NightExecutionPlan,
  _listingId: string
): Promise<NightApplyResult> {
  // Skipped nights are never sent to the executor — short-circuit here
  // so the result is always consistent with the plan.
  if (!night.included || night.finalAppliedPrice == null) {
    return {
      date: night.date,
      applyStatus: "skipped",
      finalAppliedPrice: null,
      errorMessage: null,
    };
  }

  // STUB: simulate success without Airbnb write.
  // TODO: replace with real call (see docstring above).
  return {
    date: night.date,
    applyStatus: "simulated_success",
    finalAppliedPrice: night.finalAppliedPrice,
    errorMessage: null,
  };
}

// ── Plan executor ──────────────────────────────────────────────────────────

/**
 * Execute a ManualApplyPlan against Airbnb.
 *
 * Currently returns simulated results only. No prices are changed.
 *
 * Nights are processed sequentially to avoid rate-limit issues
 * when real execution is enabled.
 */
export async function executeManualApplyPlan(
  plan: ManualApplyPlan
): Promise<ApplyExecutorResult> {
  const nightResults: NightApplyResult[] = [];

  for (const night of plan.nights) {
    const result = await executeNightPrice(night, plan.listingId);
    nightResults.push(result);
  }

  const simulatedSuccess = nightResults.filter(
    (n) => n.applyStatus === "simulated_success"
  ).length;
  const simulatedFailed = nightResults.filter(
    (n) => n.applyStatus === "simulated_failure"
  ).length;
  const skipped = nightResults.filter((n) => n.applyStatus === "skipped").length;

  return {
    runId: plan.planId,
    listingId: plan.listingId,
    executionMode: "stub",
    executionModeNote:
      "Airbnb sync is not yet enabled. This is a preview-only run — " +
      "the prices shown are what would have been applied. " +
      "No changes were made to your listing.",
    nightsSimulatedSuccess: simulatedSuccess,
    nightsSimulatedFailed: simulatedFailed,
    nightsSkipped: skipped,
    nightsTotal: plan.nights.length,
    completedAt: new Date().toISOString(),
    nights: nightResults,
  };
}
