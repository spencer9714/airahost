/**
 * Co-host verification scaffold.
 *
 * This module is the single integration point for verifying that Airahost
 * has been added as a co-host on an Airbnb listing and holds the write
 * capability required for Auto-Apply execution.
 *
 * ── CURRENT STATE: Stub implementation only ───────────────────────────────
 *
 * The stub returns "verification_pending" without performing any real check.
 * This keeps the product honest: user confirmation does not equal verified.
 *
 * ── How to replace the stub ───────────────────────────────────────────────
 *
 * Replace `startCohostVerification` with a real implementation that does
 * one of the following:
 *
 *   a) Airbnb co-host API:
 *      GET /v2/listings/{listingId}/co-hosts — confirm the Airahost service
 *      account is present with calendar/pricing write scope.
 *
 *   b) Authenticated browser session:
 *      Navigate to the listing's co-host settings and scrape membership.
 *
 *   c) Test write:
 *      Attempt to set the listing price to its current value and immediately
 *      revert. A successful round-trip confirms write access.
 *
 * The contract below must be honoured regardless of implementation.
 */

// ── Types ──────────────────────────────────────────────────────────────────

export type CohostVerificationStatus =
  | "not_started"
  | "invite_opened"
  | "user_confirmed"
  | "verification_pending"
  | "verified"
  | "verification_failed";

export interface CohostVerificationResult {
  /** Resulting status after this verification attempt. */
  status: CohostVerificationStatus;
  /** ISO timestamp when verification succeeded; null otherwise. */
  verifiedAt: string | null;
  /** Human-readable error if status is "verification_failed"; null otherwise. */
  errorMessage: string | null;
  /**
   * How verification was performed.
   *   "stub"             — no real check (current phase)
   *   "airbnb_api"       — via Airbnb co-host API endpoint
   *   "browser_session"  — via authenticated browser session
   */
  method: "stub" | "airbnb_api" | "browser_session";
}

// ── Verification entry point ───────────────────────────────────────────────

/**
 * Attempt to verify that Airahost has co-host write access on the given
 * Airbnb listing.
 *
 * CONTRACT:
 *   - MUST NOT return status "verified" without a real confirmation signal.
 *   - MUST NOT silently succeed or self-attest.
 *   - SHOULD return "verification_failed" when a real check is run but fails.
 *   - Safe to call multiple times (idempotent from the caller's perspective).
 *
 * @param listingId       Airahost saved_listing UUID (for logging / context).
 * @param airbnbListingId Numeric Airbnb listing ID from the room URL, or null
 *                        if the listing has no URL set yet.
 */
export async function startCohostVerification(
  _listingId: string,
  _airbnbListingId: string | null
): Promise<CohostVerificationResult> {
  // ── Stub ─────────────────────────────────────────────────────────────────
  // No real verification is possible in this phase.
  // Leave status at verification_pending so the product stays honest.
  //
  // Replace this block with a real implementation (see module comment above).
  return {
    status: "verification_pending",
    verifiedAt: null,
    errorMessage: null,
    method: "stub",
  };
}
