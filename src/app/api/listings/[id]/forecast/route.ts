/**
 * POST /api/listings/[id]/forecast
 *
 * Creates a forecast_snapshot job for a saved listing.  Designed to be
 * called by a Railway cron scheduler as well as directly from the dashboard.
 *
 * Strategy:
 *   1. Find the latest ready live_analysis report linked to this listing.
 *   2. If the source market data is stale (> SOURCE_STALE_DAYS old), refuse
 *      and signal the caller to run a fresh live analysis first.
 *   3. If a forecast job already exists in a pending state within the last
 *      DEDUP_WINDOW_MINUTES, return early (idempotent).
 *   4. Insert a forecast_snapshot pricing_report (status=queued) with
 *      source_report_id pointing at the source live_analysis.
 *
 * Response shape (always 200 for scheduler-safe retries, errors use 4xx/5xx):
 *   { created: true,  reason: "created",            reportId, shareId }
 *   { created: false, reason: "duplicate_pending",  reportId, shareId }  // existing job
 *   { created: false, reason: "source_too_stale",   staleDays }          // need live refresh
 *   { created: false, reason: "no_source_report" }                       // no live report yet
 */

import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";
import { generateShareId } from "@/lib/shareId";

// A source live_analysis older than this is considered too stale to forecast from.
// Matches the stale freshness threshold in freshness.ts.
const SOURCE_STALE_DAYS = 7;

// Don't create a new forecast if one is already pending within this window.
const DEDUP_WINDOW_MINUTES = 60;

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id: listingId } = await params;

    // ── Auth ────────────────────────────────────────────────────────────────
    const authClient = await getSupabaseServer();
    const {
      data: { user },
    } = await authClient.auth.getUser();

    if (!user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    // ── Verify listing ownership ─────────────────────────────────────────────
    const { data: listing, error: listingErr } = await authClient
      .from("saved_listings")
      .select("id, input_address, input_attributes, default_discount_policy")
      .eq("id", listingId)
      .eq("user_id", user.id)
      .single();

    if (listingErr || !listing) {
      return NextResponse.json({ error: "Listing not found" }, { status: 404 });
    }

    const admin = getSupabaseAdmin();

    // ── Find latest ready live_analysis linked to this listing ───────────────
    // We join through listing_reports → pricing_reports to respect the ownership chain.
    const { data: sourceLinks, error: sourceErr } = await admin
      .from("listing_reports")
      .select(
        "created_at, pricing_reports:pricing_report_id(id, share_id, report_type, status, market_captured_at, completed_at, created_at, input_date_start, input_date_end, input_attributes, discount_policy)"
      )
      .eq("saved_listing_id", listingId)
      .order("created_at", { ascending: false })
      .limit(20);

    if (sourceErr) {
      console.error("Forecast: source report lookup failed", sourceErr);
      return NextResponse.json(
        { error: "Failed to look up source reports" },
        { status: 500 }
      );
    }

    // Find the most recent ready live_analysis
    type SourceReport = {
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
    };

    const normalizeReport = (r: SourceReport | SourceReport[] | null): SourceReport | null => {
      if (!r) return null;
      return Array.isArray(r) ? r[0] ?? null : r;
    };

    let sourceReport: SourceReport | null = null;
    for (const link of sourceLinks ?? []) {
      const r = normalizeReport(
        link.pricing_reports as SourceReport | SourceReport[] | null
      );
      if (
        r &&
        r.status === "ready" &&
        (r.report_type === "live_analysis" || !r.report_type)
      ) {
        sourceReport = r;
        break;
      }
    }

    if (!sourceReport) {
      return NextResponse.json(
        { created: false, reason: "no_source_report" },
        { status: 200 }
      );
    }

    // ── Stale check ──────────────────────────────────────────────────────────
    // Use market_captured_at → completed_at → created_at fallback chain.
    const sourceMCT =
      sourceReport.market_captured_at ??
      sourceReport.completed_at ??
      sourceReport.created_at;

    const sourceDaysOld = Math.floor(
      (Date.now() - new Date(sourceMCT).getTime()) / 86_400_000
    );

    if (sourceDaysOld > SOURCE_STALE_DAYS) {
      return NextResponse.json(
        {
          created: false,
          reason: "source_too_stale",
          staleDays: sourceDaysOld,
          message: `Source live analysis is ${sourceDaysOld} days old (limit: ${SOURCE_STALE_DAYS}). Run a fresh live analysis first.`,
        },
        { status: 200 }
      );
    }

    // ── Idempotency: check for a recent pending forecast ─────────────────────
    const dedupCutoff = new Date(
      Date.now() - DEDUP_WINDOW_MINUTES * 60 * 1000
    ).toISOString();

    const { data: pendingLinks } = await admin
      .from("listing_reports")
      .select(
        "created_at, pricing_reports:pricing_report_id(id, share_id, status, report_type)"
      )
      .eq("saved_listing_id", listingId)
      .gte("created_at", dedupCutoff)
      .order("created_at", { ascending: false })
      .limit(10);

    for (const link of pendingLinks ?? []) {
      const r = normalizeReport(
        link.pricing_reports as SourceReport | SourceReport[] | null
      );
      if (
        r &&
        r.report_type === "forecast_snapshot" &&
        (r.status === "queued" || r.status === "running")
      ) {
        return NextResponse.json(
          {
            created: false,
            reason: "duplicate_pending",
            reportId: r.id,
            shareId: r.share_id,
          },
          { status: 200 }
        );
      }
    }

    // ── Create the forecast_snapshot job ─────────────────────────────────────
    const reportId = crypto.randomUUID();
    const shareId = generateShareId();
    const now = new Date().toISOString();
    const targetEnv = process.env.WORKER_TARGET_ENV ?? "production";

    const report = {
      id: reportId,
      user_id: user.id,
      share_id: shareId,
      listing_id: listingId,
      report_type: "forecast_snapshot",
      source_report_id: sourceReport.id,
      input_address: listing.input_address,
      target_env: targetEnv,
      // Inherit dates and attributes from source report so the forecast covers
      // the same period under the same pricing policy.
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
        request_source: "api/listings/[id]/forecast",
        created_at: now,
      },
    };

    const { error: insertErr } = await admin.from("pricing_reports").insert(report);

    if (insertErr) {
      console.error("Forecast: report insert failed", insertErr);
      return NextResponse.json(
        { error: "Failed to create forecast job", detail: insertErr.message },
        { status: 500 }
      );
    }

    // Link to listing
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
  } catch (err) {
    console.error("Forecast creation error:", err);
    return NextResponse.json(
      {
        error: "Internal server error",
        detail: err instanceof Error ? err.message : String(err),
      },
      { status: 500 }
    );
  }
}
