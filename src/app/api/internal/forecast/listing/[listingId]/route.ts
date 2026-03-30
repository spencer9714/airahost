/**
 * POST /api/internal/forecast/listing/[listingId]
 *
 * Internal per-listing forecast trigger — service-role auth, no user session.
 * Called by the /api/internal/forecast/schedule orchestrator.
 *
 * Same business logic as /api/listings/[id]/forecast but uses admin client
 * and INTERNAL_API_SECRET instead of a user cookie.
 */

import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";
import { generateShareId } from "@/lib/shareId";

const INTERNAL_API_SECRET = process.env.INTERNAL_API_SECRET;

const SOURCE_STALE_DAYS = 7;
const DEDUP_WINDOW_MINUTES = 60;

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ listingId: string }> }
) {
  // ── Auth ──────────────────────────────────────────────────────────────────
  if (!INTERNAL_API_SECRET) {
    return NextResponse.json({ error: "Not configured" }, { status: 500 });
  }
  const authHeader = req.headers.get("authorization") ?? "";
  const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : "";
  if (token !== INTERNAL_API_SECRET) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { listingId } = await params;
  const admin = getSupabaseAdmin();

  // ── Find latest ready live_analysis for this listing ─────────────────────
  type ReportRow = {
    id: string;
    share_id: string;
    report_type: string | null;
    status: string;
    market_captured_at: string | null;
    completed_at: string | null;
    created_at: string;
    input_date_start: string;
    input_date_end: string;
    input_attributes: Record<string, unknown> | null;
    discount_policy: Record<string, unknown> | null;
    user_id: string | null;
  };

  const { data: links } = await admin
    .from("listing_reports")
    .select(
      "created_at, pricing_reports:pricing_report_id(id, share_id, report_type, status, market_captured_at, completed_at, created_at, input_date_start, input_date_end, input_attributes, discount_policy, user_id)"
    )
    .eq("saved_listing_id", listingId)
    .order("created_at", { ascending: false })
    .limit(20);

  const normalize = (r: ReportRow | ReportRow[] | null): ReportRow | null =>
    !r ? null : Array.isArray(r) ? r[0] ?? null : r;

  let sourceReport: ReportRow | null = null;
  for (const link of links ?? []) {
    const r = normalize(link.pricing_reports as ReportRow | ReportRow[] | null);
    if (r && r.status === "ready" && (r.report_type === "live_analysis" || !r.report_type)) {
      sourceReport = r;
      break;
    }
  }

  if (!sourceReport) {
    return NextResponse.json({ created: false, reason: "no_source_report" });
  }

  // ── Stale check ───────────────────────────────────────────────────────────
  const sourceMCT =
    sourceReport.market_captured_at ??
    sourceReport.completed_at ??
    sourceReport.created_at;

  const sourceDaysOld = Math.floor(
    (Date.now() - new Date(sourceMCT).getTime()) / 86_400_000
  );

  if (sourceDaysOld > SOURCE_STALE_DAYS) {
    return NextResponse.json({
      created: false,
      reason: "source_too_stale",
      staleDays: sourceDaysOld,
      message: `Source is ${sourceDaysOld}d old — run a live analysis first.`,
    });
  }

  // ── Idempotency check ─────────────────────────────────────────────────────
  const dedupCutoff = new Date(
    Date.now() - DEDUP_WINDOW_MINUTES * 60 * 1000
  ).toISOString();

  const { data: recentLinks } = await admin
    .from("listing_reports")
    .select(
      "pricing_reports:pricing_report_id(id, share_id, status, report_type)"
    )
    .eq("saved_listing_id", listingId)
    .gte("created_at", dedupCutoff)
    .order("created_at", { ascending: false })
    .limit(10);

  for (const link of recentLinks ?? []) {
    const r = normalize(
      link.pricing_reports as ReportRow | ReportRow[] | null
    );
    if (
      r &&
      r.report_type === "forecast_snapshot" &&
      (r.status === "queued" || r.status === "running")
    ) {
      return NextResponse.json({
        created: false,
        reason: "duplicate_pending",
        reportId: r.id,
        shareId: r.share_id,
      });
    }
  }

  // ── Fetch listing for address / attributes ────────────────────────────────
  const { data: listing } = await admin
    .from("saved_listings")
    .select("id, input_address, input_attributes, default_discount_policy")
    .eq("id", listingId)
    .single();

  if (!listing) {
    return NextResponse.json({ created: false, reason: "listing_not_found" });
  }

  // ── Create forecast_snapshot job ──────────────────────────────────────────
  const reportId = crypto.randomUUID();
  const shareId = generateShareId();
  const now = new Date().toISOString();
  const targetEnv = process.env.WORKER_TARGET_ENV ?? "production";

  const report = {
    id: reportId,
    user_id: sourceReport.user_id,
    share_id: shareId,
    listing_id: listingId,
    report_type: "forecast_snapshot",
    source_report_id: sourceReport.id,
    input_address: listing.input_address,
    target_env: targetEnv,
    input_date_start: sourceReport.input_date_start,
    input_date_end: sourceReport.input_date_end,
    input_attributes: sourceReport.input_attributes ?? listing.input_attributes,
    discount_policy: sourceReport.discount_policy ?? listing.default_discount_policy ?? {},
    status: "queued",
    core_version: "pending",
    result_summary: null,
    result_calendar: null,
    completed_at: null,
    market_captured_at: null,
    error_message: null,
    result_core_debug: {
      forecast_generation_mode: "derived_snapshot",
      source_report_id: sourceReport.id,
      source_market_captured_at: sourceMCT,
      source_days_old: sourceDaysOld,
      request_source: "api/internal/forecast/listing",
      created_at: now,
    },
  };

  const { error: insertErr } = await admin.from("pricing_reports").insert(report);

  if (insertErr) {
    console.error(`internal/forecast/listing/${listingId}: insert failed`, insertErr);
    return NextResponse.json(
      { error: "Insert failed", detail: insertErr.message },
      { status: 500 }
    );
  }

  await admin.from("listing_reports").insert({
    saved_listing_id: listingId,
    pricing_report_id: reportId,
    trigger: "scheduled",
  });

  return NextResponse.json({
    created: true,
    reason: "created",
    reportId,
    shareId,
    sourceReportId: sourceReport.id,
    sourceDaysOld,
  });
}
