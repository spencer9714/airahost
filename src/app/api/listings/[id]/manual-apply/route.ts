/**
 * POST /api/listings/[id]/manual-apply
 *
 * Initiates a manual apply run for the given listing.
 *
 * Flow:
 *   1. Validate auth + listing ownership.
 *   2. Load latest ready report → result_calendar.
 *   3. Build AutoApplySettings from persisted listing fields.
 *   4. computeAutoApplyPreview → buildManualApplyPlan.
 *   5. executeManualApplyPlan (stub — no real Airbnb writes).
 *   6. Write audit rows to auto_apply_runs + auto_apply_run_nights.
 *   7. Return structured ApplyExecutorResult.
 *
 * The executor is intentionally stubbed. To enable real Airbnb write-back,
 * replace the executor body in src/lib/airbnbApplyExecutor.ts without
 * changing this route.
 */

import { NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";
import { computeAutoApplyPreview } from "@/lib/autoApplyPreview";
import { buildManualApplyPlan } from "@/lib/autoApplyPlan";
import { executeManualApplyPlan } from "@/lib/airbnbApplyExecutor";
import type { CalendarDay } from "@/lib/schemas";
import type { AutoApplySettings } from "@/components/dashboard/AutoApplyDrawer";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;

  // Parse optional selectedDates from body (may be null or absent).
  let selectedDates: string[] | null = null;
  try {
    const body = await request.json().catch(() => ({}));
    if (Array.isArray((body as { selectedDates?: unknown }).selectedDates)) {
      selectedDates = (body as { selectedDates: string[] }).selectedDates;
    }
  } catch {
    // ignore parse errors — selectedDates stays null (all nights included)
  }

  // ── Auth ────────────────────────────────────────────────────────────────
  const supabase = await getSupabaseServer();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // ── Ownership + settings ─────────────────────────────────────────────────
  const { data: listing, error: listingError } = await supabase
    .from("saved_listings")
    .select(
      `id,
       auto_apply_enabled,
       auto_apply_window_end_days,
       auto_apply_scope,
       auto_apply_min_price_floor,
       auto_apply_min_notice_days,
       auto_apply_max_increase_pct,
       auto_apply_max_decrease_pct,
       auto_apply_skip_unavailable,
       auto_apply_last_updated_at,
       auto_apply_cohost_status`
    )
    .eq("id", id)
    .eq("user_id", user.id)
    .single();

  if (listingError || !listing) {
    return NextResponse.json({ error: "Listing not found" }, { status: 404 });
  }

  // Settings must be configured before running manual apply.
  if (!listing.auto_apply_last_updated_at) {
    return NextResponse.json(
      { error: "Auto-Apply is not configured for this listing." },
      { status: 400 }
    );
  }

  // Co-host access must be verified before execution.
  // "user_confirmed" and "verification_pending" are NOT sufficient —
  // only "verified" means the system has confirmed write capability.
  const cohostStatus = (listing.auto_apply_cohost_status as string | null) ?? "not_started";
  if (cohostStatus !== "verified") {
    const statusMessages: Record<string, string> = {
      not_started: "Co-host setup is required before applying prices. Add Airahost as a co-host on Airbnb.",
      invite_opened: "Co-host setup is not yet complete. Finish adding Airahost as a co-host, then confirm.",
      user_confirmed: "Co-host access is pending verification. Auto-Apply execution requires confirmed system access.",
      verification_pending: "Co-host verification is in progress. Execution will be available once access is confirmed.",
      verification_failed: "Co-host verification failed. Please re-confirm your co-host setup and try again.",
    };
    return NextResponse.json(
      {
        error: statusMessages[cohostStatus] ?? "Co-host access is not verified for this listing.",
        cohostStatus,
      },
      { status: 400 }
    );
  }

  // ── Latest report calendar ───────────────────────────────────────────────
  // pricing_reports is linked to saved_listings via the listing_reports junction table.
  const { data: latestLink } = await supabase
    .from("listing_reports")
    .select(
      "pricing_report_id, pricing_reports:pricing_report_id(id, status, result_calendar)"
    )
    .eq("saved_listing_id", id)
    .order("created_at", { ascending: false })
    .limit(10);

  // Find the most recent link whose report is ready and has calendar data.
  type ReportRow = { id: string; status: string; result_calendar: unknown } | null;
  const readyLink = (latestLink ?? []).find((row) => {
    const rpt = Array.isArray(row.pricing_reports)
      ? row.pricing_reports[0]
      : (row.pricing_reports as ReportRow);
    return rpt?.status === "ready";
  });

  const linkedReport = readyLink
    ? Array.isArray(readyLink.pricing_reports)
      ? (readyLink.pricing_reports[0] as ReportRow)
      : (readyLink.pricing_reports as ReportRow)
    : null;

  const calendar: CalendarDay[] = (linkedReport?.result_calendar as CalendarDay[]) ?? [];

  // ── Build execution plan ─────────────────────────────────────────────────
  const settings: AutoApplySettings = {
    enabled: listing.auto_apply_enabled ?? false,
    windowEndDays: listing.auto_apply_window_end_days ?? 30,
    applyScope: listing.auto_apply_scope ?? "actionable",
    minPriceFloor: listing.auto_apply_min_price_floor ?? null,
    minNoticeDays: listing.auto_apply_min_notice_days ?? 1,
    maxIncreasePct: listing.auto_apply_max_increase_pct ?? null,
    maxDecreasePct: listing.auto_apply_max_decrease_pct ?? null,
    skipUnavailableNights: listing.auto_apply_skip_unavailable ?? true,
    lastUpdatedAt: listing.auto_apply_last_updated_at ?? null,
  };

  const preview = computeAutoApplyPreview(calendar, settings);
  const plan = buildManualApplyPlan(preview, id, linkedReport?.id ?? null);

  // Filter plan nights to the caller-selected subset (if provided).
  if (selectedDates && selectedDates.length > 0) {
    const selectedSet = new Set(selectedDates);
    plan.nights = plan.nights.map((n) =>
      n.included && !selectedSet.has(n.date)
        ? { ...n, included: false, skipReason: "no_data" as const, applyStatus: "skipped" as const }
        : n
    );
    // Recount summary fields to reflect the filtered set.
    plan.nightsIncluded = plan.nights.filter((n) => n.included).length;
    plan.nightsSkipped = plan.nights.filter((n) => !n.included).length;
    plan.nightsFloored = plan.nights.filter(
      (n) => n.included && (n.guardrailsApplied === "floored" || n.guardrailsApplied === "floored_and_capped")
    ).length;
    plan.nightsCapped = plan.nights.filter(
      (n) => n.included && (n.guardrailsApplied === "capped_increase" || n.guardrailsApplied === "capped_decrease" || n.guardrailsApplied === "floored_and_capped")
    ).length;
  }

  // ── Execute (stub) ───────────────────────────────────────────────────────
  let executorResult;
  try {
    executorResult = await executeManualApplyPlan(plan);
  } catch (err) {
    console.error("[manual-apply] executor error:", err);
    return NextResponse.json(
      { error: "Execution failed unexpectedly." },
      { status: 500 }
    );
  }

  // ── Audit logging ────────────────────────────────────────────────────────
  // Uses admin client (service role) — user cannot write these tables directly.
  // Audit writes are best-effort; a failure here does not roll back the run.
  const adminClient = getSupabaseAdmin();

  try {
    const { data: runRow, error: runError } = await adminClient
      .from("auto_apply_runs")
      .insert({
        id: plan.planId,
        listing_id: id,
        report_id: plan.reportId,
        user_id: user.id,
        range_start: plan.rangeStart,
        range_end: plan.rangeEnd,
        total_nights: plan.totalSelectedNights,
        nights_included: plan.nightsIncluded,
        nights_skipped: plan.nightsSkipped,
        nights_floored: plan.nightsFloored,
        nights_capped: plan.nightsCapped,
        execution_mode: "stub",
        result_status: "simulated",
        settings_snapshot: plan.settingsSnapshot,
        initiated_at: plan.generatedAt,
      })
      .select("id")
      .single();

    if (runRow && !runError) {
      const nightRows = plan.nights.map((n) => {
        const nightResult = executorResult.nights.find((r) => r.date === n.date);
        return {
          run_id: runRow.id,
          listing_id: id,
          night_date: n.date,
          recommended_price: n.recommendedPrice,
          final_applied_price: n.finalAppliedPrice,
          current_known_price: n.currentKnownPrice,
          included: n.included,
          skip_reason: n.skipReason,
          guardrails_applied: n.guardrailsApplied,
          apply_status: nightResult?.applyStatus ?? (n.included ? "planned" : "skipped"),
          error_message: nightResult?.errorMessage ?? null,
        };
      });

      await adminClient.from("auto_apply_run_nights").insert(nightRows);
    }
  } catch (auditErr) {
    // Log but do not fail the request — the execution already completed.
    console.error("[manual-apply] audit write error:", auditErr);
  }

  // ── Response ─────────────────────────────────────────────────────────────
  return NextResponse.json({
    runId: executorResult.runId,
    executionMode: executorResult.executionMode,
    executionModeNote: executorResult.executionModeNote,
    nightsTotal: executorResult.nightsTotal,
    nightsSimulatedSuccess: executorResult.nightsSimulatedSuccess,
    nightsSimulatedFailed: executorResult.nightsSimulatedFailed,
    nightsSkipped: executorResult.nightsSkipped,
    nightsFloored: plan.nightsFloored,
    nightsCapped: plan.nightsCapped,
    rangeStart: plan.rangeStart,
    rangeEnd: plan.rangeEnd,
    completedAt: executorResult.completedAt,
    nights: executorResult.nights,
  });
}
