/**
 * Report Execution Policy
 *
 * Formalizes the internal execution contract for pricing_reports so the
 * worker and future consumers can identify the intended role of each job
 * without re-deriving it from job_lane + trigger combinations.
 *
 * Stored as result_core_debug.execution_policy at report creation time.
 * Read-only after creation — never mutated by the worker.
 *
 * ┌──────────────────────────────────────┬─────────────┬───────────────┐
 * │ execution_policy                     │ job_lane    │ trigger       │
 * ├──────────────────────────────────────┼─────────────┼───────────────┤
 * │ nightly_board_refresh                │ nightly     │ scheduled     │
 * │ nightly_alert_training_refresh       │ nightly     │ scheduled     │  ← future
 * │ interactive_live_report              │ interactive │ manual/rerun  │
 * └──────────────────────────────────────┴─────────────┴───────────────┘
 *
 * Dashboard source-of-truth rule (enforced in /api/listings GET):
 *   Only reports with trigger="scheduled" + status="ready" + report_type!="forecast_snapshot"
 *   are selected as latestReport.  The execution_policy field makes this
 *   intent explicit and machine-readable without re-deriving it later.
 */

export type ReportExecutionPolicy =
  /** Nightly scheduled run. Becomes the dashboard board source-of-truth once ready. */
  | "nightly_board_refresh"
  /** Nightly scheduled run for alert-model training. Not yet emitted; reserved for phase 2. */
  | "nightly_alert_training_refresh"
  /** User-initiated report: dashboard shorthand rerun, /rerun endpoint, or anonymous analysis. */
  | "interactive_live_report";

/**
 * Returns the execution_policy field to spread into result_core_debug.
 *
 * @example
 * result_core_debug: {
 *   ...executionPolicyMeta("nightly_board_refresh"),
 *   cache_hit: false,
 *   nightly: true,
 *   ...
 * }
 */
export function executionPolicyMeta(policy: ReportExecutionPolicy): {
  execution_policy: ReportExecutionPolicy;
} {
  return { execution_policy: policy };
}

/**
 * Derives the execution policy from the stored job_lane + trigger values.
 * Use this when reading existing reports that may predate the explicit field.
 * Prefer calling executionPolicyMeta() with an explicit policy at creation sites.
 */
export function deriveExecutionPolicy(
  jobLane: string,
  trigger: string
): ReportExecutionPolicy {
  if (jobLane === "nightly" && trigger === "scheduled") {
    return "nightly_board_refresh";
  }
  return "interactive_live_report";
}
