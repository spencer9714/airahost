"""
Report Execution Policy — Python mirror of src/lib/reportPolicy.ts

Resolves the effective execution policy for a claimed pricing_report row.
Used by the worker dispatcher to route jobs into the correct pipeline.

Policy values match the TypeScript ReportExecutionPolicy union type exactly
so that result_core_debug.execution_policy is interoperable between the
API layer (TypeScript) and the worker (Python).

Resolution order:
  1. Explicit: result_core_debug.execution_policy — set at creation time by
     the API route for all reports created after Phase 1 of the report contract.
  2. Derived: job_lane — used for legacy rows that predate the explicit field.

Note: The trigger field (manual/rerun/scheduled) lives in listing_reports,
not in the claimed pricing_reports row, so it is not available to the worker
at execution time without an extra query.  job_lane is the reliable
discriminator available from the claimed row alone.
"""

from __future__ import annotations

from typing import Any, Dict, Literal

# ---------------------------------------------------------------------------
# Policy type and constants
# ---------------------------------------------------------------------------

ReportExecutionPolicy = Literal[
    "nightly_board_refresh",
    "nightly_alert_training_refresh",
    "interactive_live_report",
]

POLICY_NIGHTLY_BOARD: ReportExecutionPolicy = "nightly_board_refresh"
POLICY_NIGHTLY_ALERT: ReportExecutionPolicy = "nightly_alert_training_refresh"  # reserved
POLICY_INTERACTIVE: ReportExecutionPolicy = "interactive_live_report"

# All valid policy values — used for safe membership checks on untrusted data.
_VALID_POLICIES: frozenset[str] = frozenset({
    POLICY_NIGHTLY_BOARD,
    POLICY_NIGHTLY_ALERT,
    POLICY_INTERACTIVE,
})

# Policies that route through the nightly pipeline.
# Both nightly variants share the same pipeline until nightly_alert_training_refresh
# is split into its own handler in a future phase.
NIGHTLY_POLICIES: frozenset[str] = frozenset({
    POLICY_NIGHTLY_BOARD,
    POLICY_NIGHTLY_ALERT,
})

# ---------------------------------------------------------------------------
# Resolution helper
# ---------------------------------------------------------------------------


def resolve_execution_policy(job: Dict[str, Any]) -> ReportExecutionPolicy:
    """
    Resolve the effective execution policy for a claimed job row.

    Prefers the explicit ``result_core_debug.execution_policy`` value when
    present and valid (reports created after Phase 1 of the report contract).
    Falls back to deriving from ``job_lane`` for legacy rows that predate the
    explicit field.

    - Does not assume any JSON key order inside result_core_debug.
    - Does not raise; always returns a valid policy string.
    - Does not perform any DB queries.

    Args:
        job: Full pricing_reports row dict as returned by ``claim_job()``.

    Returns:
        A ``ReportExecutionPolicy`` string.
    """
    debug = job.get("result_core_debug")
    if isinstance(debug, dict):
        explicit = debug.get("execution_policy")
        if isinstance(explicit, str) and explicit in _VALID_POLICIES:
            return explicit  # type: ignore[return-value]

    # Legacy fallback: derive from job_lane.
    job_lane = job.get("job_lane") or "interactive"
    if job_lane == "nightly":
        return POLICY_NIGHTLY_BOARD

    return POLICY_INTERACTIVE
