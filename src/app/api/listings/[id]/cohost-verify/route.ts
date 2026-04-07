/**
 * POST /api/listings/[id]/cohost-verify
 *
 * Called when the user clicks "I've added Airahost as co-host".
 * Records the confirmation timestamp and immediately transitions to
 * verification_pending via the cohostVerification scaffold.
 *
 * Current (stub) flow — synchronous, single write:
 *   1. Auth + ownership check.
 *   2. Call startCohostVerification (stub → returns verification_pending).
 *   3. Persist verification_pending + confirmed_at in one write.
 *   4. Return the new co-host status fields.
 *
 * Note on user_confirmed:
 *   That state exists in the schema for a future async verification phase
 *   where the flow would be:
 *     a) immediately write user_confirmed + confirmed_at
 *     b) dispatch an async verification job
 *     c) job updates status → verification_pending → verified / failed
 *   In the stub phase there is no async job, so we skip straight to
 *   verification_pending in a single write to avoid an invisible flash state.
 *
 * This route does NOT touch auto_apply_enabled. Enabling Auto-Apply is a
 * separate user action that requires verified status.
 */

import { NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";
import { startCohostVerification } from "@/lib/cohostVerification";
import { extractAirbnbListingId } from "@/lib/airbnb-utils";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  // ── Auth ────────────────────────────────────────────────────────────────
  const supabase = await getSupabaseServer();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // ── Ownership check ──────────────────────────────────────────────────────
  const { data: listing, error: listingError } = await supabase
    .from("saved_listings")
    .select("id, input_attributes, auto_apply_cohost_status")
    .eq("id", id)
    .eq("user_id", user.id)
    .single();

  if (listingError || !listing) {
    return NextResponse.json({ error: "Listing not found" }, { status: 404 });
  }

  // Extract the Airbnb listing ID from the URL in input_attributes.
  const attrs = (listing.input_attributes as Record<string, unknown> | null) ?? {};
  const listingUrl = (attrs.listingUrl as string | null | undefined) ?? null;
  const airbnbListingId = extractAirbnbListingId(listingUrl);

  const admin = getSupabaseAdmin();
  const now = new Date().toISOString();

  // ── Attempt verification ──────────────────────────────────────────────────
  // The stub returns verification_pending synchronously.
  // A real implementation would either return verified/failed here or
  // dispatch an async job and return verification_pending as a handoff.
  let verificationResult;
  try {
    verificationResult = await startCohostVerification(id, airbnbListingId);
  } catch (err) {
    console.error("[cohost-verify] verification error:", err);
    verificationResult = {
      status: "verification_failed" as const,
      verifiedAt: null,
      errorMessage: "An unexpected error occurred during verification.",
      method: "stub" as const,
    };
  }

  // ── Persist result in one write ───────────────────────────────────────────
  // confirmed_at records when the user clicked "I've added Airahost".
  // In the stub phase we skip the user_confirmed intermediate state and
  // write verification_pending directly. When real async verification exists,
  // the flow becomes: user_confirmed (immediate) → job → verified/failed.
  const update: Record<string, unknown> = {
    auto_apply_cohost_status: verificationResult.status,
    auto_apply_cohost_confirmed_at: now,
    auto_apply_cohost_verification_method: verificationResult.method,
    // Clear any stale error on each new attempt.
    auto_apply_cohost_verification_error: verificationResult.errorMessage,
  };

  if (verificationResult.status === "verified" && verificationResult.verifiedAt) {
    update.auto_apply_cohost_verified_at = verificationResult.verifiedAt;
  }

  await admin
    .from("saved_listings")
    .update(update)
    .eq("id", id);

  // ── Response ─────────────────────────────────────────────────────────────
  return NextResponse.json({
    cohostStatus: verificationResult.status,
    cohostConfirmedAt: now,
    cohostVerifiedAt: verificationResult.verifiedAt,
    cohostVerificationError: verificationResult.errorMessage,
    cohostVerificationMethod: verificationResult.method,
  });
}
