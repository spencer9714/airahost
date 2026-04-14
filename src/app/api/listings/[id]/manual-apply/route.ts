import { NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";
import { computeAutoApplyPreview } from "@/lib/autoApplyPreview";
import { buildManualApplyPlan } from "@/lib/autoApplyPlan";
import type { CalendarDay } from "@/lib/schemas";
import type { AutoApplySettings } from "@/components/dashboard/AutoApplyDrawer";

/**
 * POST /api/listings/[id]/manual-apply
 *
 * Builds a manual apply pricing plan and enqueues a worker job.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const todayStr = new Date().toISOString().split("T")[0];

  let selectedDates: string[] | null = null;
  let sourceReportId: string | null = null;
  try {
    const body = await request.json().catch(() => ({}));
    if (Array.isArray((body as { selectedDates?: unknown }).selectedDates)) {
      selectedDates = (body as { selectedDates: string[] }).selectedDates;
    }
    if (typeof (body as { sourceReportId?: unknown }).sourceReportId === "string") {
      sourceReportId = (body as { sourceReportId: string }).sourceReportId;
    }
  } catch {
    // Keep selectedDates as null when the request body is empty/invalid.
  }

  const supabase = await getSupabaseServer();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

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

  if (!listing.auto_apply_last_updated_at) {
    return NextResponse.json(
      { error: "Auto-Apply is not configured for this listing." },
      { status: 400 }
    );
  }

  const cohostStatus = (listing.auto_apply_cohost_status as string | null) ?? "not_started";
  if (cohostStatus !== "verified") {
    const statusMessages: Record<string, string> = {
      not_started:
        "Co-host setup is required before applying prices. Add Airahost as a co-host on Airbnb.",
      invite_opened:
        "Co-host setup is not yet complete. Finish adding Airahost as a co-host, then confirm.",
      user_confirmed:
        "Co-host access is pending verification. Auto-Apply execution requires confirmed system access.",
      verification_pending:
        "Co-host verification is in progress. Execution will be available once access is confirmed.",
      verification_failed:
        "Co-host verification failed. Please re-confirm your co-host setup and try again.",
    };

    return NextResponse.json(
      {
        error: statusMessages[cohostStatus] ?? "Co-host access is not verified for this listing.",
        cohostStatus,
      },
      { status: 400 }
    );
  }

  const adminClient = getSupabaseAdmin();
  let explicitSourceReport: ReportRow = null;
  if (sourceReportId) {
    const { data: requestedReport } = await adminClient
      .from("pricing_reports")
      .select("id, status, result_calendar")
      .eq("id", sourceReportId)
      .eq("listing_id", id)
      .maybeSingle();

    if (!requestedReport) {
      return NextResponse.json(
        { error: "Selected report was not found for this listing." },
        { status: 400 }
      );
    }
    if ((requestedReport as { status?: string }).status !== "ready") {
      return NextResponse.json(
        { error: "Selected report is not ready yet. Please wait and try again." },
        { status: 400 }
      );
    }
    explicitSourceReport = requestedReport as ReportRow;
  }

  const { data: latestLink } = await supabase
    .from("listing_reports")
    .select("pricing_report_id, pricing_reports:pricing_report_id(id, status, result_calendar)")
    .eq("saved_listing_id", id)
    .order("created_at", { ascending: false })
    .limit(10);

  type ReportRow = { id: string; status: string; result_calendar: unknown } | null;
  const readyLink = (latestLink ?? []).find((row) => {
    const report = Array.isArray(row.pricing_reports)
      ? row.pricing_reports[0]
      : (row.pricing_reports as ReportRow);
    return report?.status === "ready";
  });

  const linkedReport = readyLink
    ? Array.isArray(readyLink.pricing_reports)
      ? (readyLink.pricing_reports[0] as ReportRow)
      : (readyLink.pricing_reports as ReportRow)
    : null;
  let fallbackReport: ReportRow = null;
  if (!explicitSourceReport && !linkedReport) {
    const { data: latestReady } = await adminClient
      .from("pricing_reports")
      .select("id, status, result_calendar")
      .eq("listing_id", id)
      .eq("status", "ready")
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    fallbackReport = (latestReady as ReportRow) ?? null;
  }

  const sourceReport = explicitSourceReport ?? linkedReport ?? fallbackReport;
  const reportCalendar: CalendarDay[] = (sourceReport?.result_calendar as CalendarDay[]) ?? [];

  const normalizedWindowEndDays = Math.max(
    1,
    Math.min(30, listing.auto_apply_window_end_days ?? 30)
  );
  const normalizedMinNoticeDays = Math.max(0, listing.auto_apply_min_notice_days ?? 1);

  const settings: AutoApplySettings = {
    enabled: listing.auto_apply_enabled ?? false,
    windowEndDays: normalizedWindowEndDays,
    applyScope: listing.auto_apply_scope ?? "actionable",
    minPriceFloor: listing.auto_apply_min_price_floor ?? null,
    minNoticeDays: normalizedMinNoticeDays,
    maxIncreasePct: listing.auto_apply_max_increase_pct ?? null,
    maxDecreasePct: listing.auto_apply_max_decrease_pct ?? null,
    skipUnavailableNights: listing.auto_apply_skip_unavailable ?? true,
    lastUpdatedAt: listing.auto_apply_last_updated_at ?? null,
  };

  // Ensure selected dates from UI are representable in the preview window.
  // Without this, a tiny persisted window (e.g. 0/1 days) can exclude every selected date.
  let effectiveWindowEndDays = settings.windowEndDays;
  if (selectedDates && selectedDates.length > 0) {
    let maxOffset = -1;
    const startMs = new Date(todayStr + "T00:00:00Z").getTime();
    for (const d of selectedDates) {
      const ms = new Date(d + "T00:00:00Z").getTime();
      if (Number.isNaN(ms)) continue;
      const offset = Math.floor((ms - startMs) / 86_400_000);
      if (offset > maxOffset) maxOffset = offset;
    }
    if (maxOffset >= 0) {
      effectiveWindowEndDays = Math.min(30, Math.max(effectiveWindowEndDays, maxOffset + 1));
    }
  }

  const settingsForPreview: AutoApplySettings = {
    ...settings,
    windowEndDays: effectiveWindowEndDays,
  };

  const preview = computeAutoApplyPreview(reportCalendar, settingsForPreview);
  const plan = buildManualApplyPlan(preview, id, sourceReport?.id ?? null);

  if (selectedDates && selectedDates.length > 0) {
    const selectedSet = new Set(selectedDates);
    plan.nights = plan.nights.map((n) =>
      n.included && !selectedSet.has(n.date)
        ? {
            ...n,
            included: false,
            skipReason: "no_data" as const,
            applyStatus: "skipped" as const,
          }
        : n
    );
    plan.nightsIncluded = plan.nights.filter((n) => n.included).length;
    plan.nightsSkipped = plan.nights.filter((n) => !n.included).length;
    plan.nightsFloored = plan.nights.filter(
      (n) =>
        n.included &&
        (n.guardrailsApplied === "floored" || n.guardrailsApplied === "floored_and_capped")
    ).length;
    plan.nightsCapped = plan.nights.filter(
      (n) =>
        n.included &&
        (n.guardrailsApplied === "capped_increase" ||
          n.guardrailsApplied === "capped_decrease" ||
          n.guardrailsApplied === "floored_and_capped")
    ).length;
  }

  const calendarPayload: Record<string, number> = {};
  for (const night of plan.nights) {
    if (!night.included || night.finalAppliedPrice == null) continue;
    calendarPayload[night.date] = Math.round(night.finalAppliedPrice);
  }

  if (Object.keys(calendarPayload).length === 0) {
    const selectedSet = new Set(selectedDates ?? []);
    const consideredNights =
      selectedSet.size > 0
        ? plan.nights.filter((n) => selectedSet.has(n.date))
        : plan.nights;
    const skippedByNoticeWindow = consideredNights.filter(
      (n) => !n.included && n.skipReason === "notice_window"
    ).length;
    const skippedByNoData = consideredNights.filter(
      (n) => !n.included && n.skipReason === "no_data"
    ).length;

    let reason =
      "No eligible nights to apply for the selected range.";
    if (consideredNights.length === 0) {
      reason = "Selected nights are outside the Auto-Apply window.";
    } else if (skippedByNoticeWindow === consideredNights.length) {
      reason = `All selected nights were skipped by minimum notice days (${settings.minNoticeDays}).`;
    } else if (skippedByNoData === consideredNights.length) {
      reason = "No recommendation data was available for the selected nights.";
    }

    return NextResponse.json(
      {
        error: reason,
        debug: {
          reportId: sourceReport?.id ?? null,
          calendarDays: reportCalendar.length,
          nightsIncluded: plan.nightsIncluded,
          selectedDatesCount: selectedDates?.length ?? 0,
          minNoticeDays: settings.minNoticeDays,
          windowEndDays: settings.windowEndDays,
          effectiveWindowEndDays,
          skippedByNoticeWindow,
          skippedByNoData,
        },
      },
      { status: 400 }
    );
  }
  const { data: jobRow, error: jobError } = await adminClient
    .from("price_update_jobs")
    .insert({
      listing_id: id,
      user_id: user.id,
      source_report_id: plan.reportId,
      range_start: plan.rangeStart,
      range_end: plan.rangeEnd,
      calendar: calendarPayload,
      settings_snapshot: plan.settingsSnapshot,
      status: "queued",
    })
    .select("id, status, created_at")
    .single();

  if (jobError || !jobRow) {
    console.error("[manual-apply] queue insert error:", jobError);
    return NextResponse.json(
      { error: "Unable to queue price update job." },
      { status: 500 }
    );
  }

  return NextResponse.json({
    jobId: jobRow.id,
    runId: jobRow.id,
    status: jobRow.status,
    queuedAt: jobRow.created_at,
    listingId: id,
    reportId: plan.reportId,
    nightsQueued: Object.keys(calendarPayload).length,
    nightsSkipped: plan.nightsSkipped,
    nightsFloored: plan.nightsFloored,
    nightsCapped: plan.nightsCapped,
    rangeStart: plan.rangeStart,
    rangeEnd: plan.rangeEnd,
  });
}
