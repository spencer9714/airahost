import { NextRequest, NextResponse } from "next/server";
import { createListingSchema } from "@/lib/schemas";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";

interface ReportSnapshot {
  id: string;
  share_id: string;
  status: string;
  report_type?: string;
  source_report_id?: string | null;
  created_at: string;
  completed_at?: string | null;
  market_captured_at?: string | null;
  input_date_start: string;
  input_date_end: string;
  result_summary: {
    nightlyMin?: number;
    nightlyMedian?: number;
    nightlyMax?: number;
    occupancyPct?: number;
    weekdayAvg?: number;
    weekendAvg?: number;
    estimatedMonthlyRevenue?: number;
    recommendedPrice?: Record<string, unknown>;
    compsSummary?: Record<string, unknown>;
    priceDistribution?: Record<string, unknown>;
  } | null;
  result_calendar?: Array<{
    date: string;
    dayOfWeek: string;
    isWeekend: boolean;
    basePrice: number;
    refundablePrice: number;
    nonRefundablePrice: number;
  }> | null;
}

interface ListingReportLinkRow {
  saved_listing_id: string;
  pricing_report_id: string;
  created_at: string;
  trigger: "manual" | "rerun" | "scheduled";
  pricing_reports: ReportSnapshot | ReportSnapshot[] | null;
}

function normalizeReportRelation(
  relation: ReportSnapshot | ReportSnapshot[] | null | undefined
): ReportSnapshot | null {
  if (Array.isArray(relation)) {
    return relation[0] ?? null;
  }
  return relation ?? null;
}

export async function GET() {
  try {
    const supabase = await getSupabaseServer();
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { data: listings, error } = await supabase
      .from("saved_listings")
      .select("*")
      .eq("user_id", user.id)
      .order("created_at", { ascending: false });

    if (error) {
      console.error("Listings fetch error:", error);
      return NextResponse.json(
        { error: "Failed to fetch listings" },
        { status: 500 }
      );
    }

    const listingRows = listings ?? [];
    const listingIds = listingRows.map((l) => l.id);

    if (listingIds.length === 0) {
      return NextResponse.json({ listings: [], recentReports: [] });
    }

    const { data: linkedRows, error: linkedError } = await supabase
      .from("listing_reports")
      .select(
        "saved_listing_id, pricing_report_id, created_at, trigger, pricing_reports:pricing_report_id(id, share_id, status, report_type, source_report_id, created_at, completed_at, market_captured_at, input_date_start, input_date_end, result_summary, result_calendar)"
      )
      .in("saved_listing_id", listingIds)
      .order("created_at", { ascending: false });

    if (linkedError) {
      console.error("Listing report links fetch error:", linkedError);
      return NextResponse.json(
        { error: "Failed to fetch listing reports" },
        { status: 500 }
      );
    }

    const links = (linkedRows ?? []) as unknown as ListingReportLinkRow[];

    // Backward-compatibility: old reports may be linked but hidden by RLS if user_id was null.
    const missingReportIds = links
      .filter((row) => !row.pricing_reports && row.pricing_report_id)
      .map((row) => row.pricing_report_id);
    const fallbackById = new Map<string, ReportSnapshot>();

    if (missingReportIds.length > 0) {
      const admin = getSupabaseAdmin();
      const { data: fallbackRows } = await admin
        .from("pricing_reports")
        .select(
          "id, share_id, status, report_type, source_report_id, created_at, completed_at, market_captured_at, input_date_start, input_date_end, result_summary, result_calendar"
        )
        .in("id", missingReportIds);

      for (const row of fallbackRows ?? []) {
        fallbackById.set(row.id, row as ReportSnapshot);
      }
    }

    // ── Per-listing selection ────────────────────────────────────
    // source-of-truth = latest scheduled nightly ready report ONLY.
    // manual / rerun / custom reports are never selected as the board report;
    // they live in recentReports / history only.
    //
    // latestJobByListing        → most recent link of ANY status (for active-job banner)
    // nightlyReadyByListing     → most recent scheduled ready report (board source-of-truth)
    // latestNightlyJobByListing → most recent scheduled link any status (for activeNightlyJob)
    const latestJobByListing = new Map<
      string,
      { row: ListingReportLinkRow; report: ReportSnapshot | null }
    >();
    const nightlyReadyByListing = new Map<
      string,
      { row: ListingReportLinkRow; report: ReportSnapshot }
    >();
    const latestNightlyJobByListing = new Map<
      string,
      { row: ListingReportLinkRow; report: ReportSnapshot | null }
    >();

    for (const row of links) {
      const report =
        normalizeReportRelation(row.pricing_reports) ??
        fallbackById.get(row.pricing_report_id) ??
        null;

      const id = row.saved_listing_id;

      // Most recent link overall (first seen per listing since links are DESC)
      if (!latestJobByListing.has(id)) {
        latestJobByListing.set(id, { row, report });
      }

      // Most recent scheduled link overall (for activeNightlyJob banner)
      if (row.trigger === "scheduled" && !latestNightlyJobByListing.has(id)) {
        latestNightlyJobByListing.set(id, { row, report });
      }

      // Board source-of-truth: scheduled + ready only. forecast_snapshot ignored.
      if (
        row.trigger === "scheduled" &&
        report?.status === "ready" &&
        report.report_type !== "forecast_snapshot" &&
        !nightlyReadyByListing.has(id)
      ) {
        nightlyReadyByListing.set(id, { row, report });
      }
    }

    const listingsWithLatest = listingRows.map((listing) => {
      const nightlyEntry = nightlyReadyByListing.get(listing.id);
      const jobEntry = latestJobByListing.get(listing.id);
      const nightlyJobEntry = latestNightlyJobByListing.get(listing.id);

      // Board source-of-truth: nightly scheduled ready report only. Null if none exists.
      const readyEntry = nightlyEntry ?? null;

      // runType: "nightly" when there is a ready nightly report, null otherwise.
      const runType: "nightly" | null = nightlyEntry ? "nightly" : null;

      const jobStatus = (jobEntry?.report?.status ?? null) as
        | "queued"
        | "running"
        | "error"
        | "ready"
        | null;

      // activeJob is only set when the most recent linked report is NOT ready.
      const activeJob =
        jobStatus && jobStatus !== "ready"
          ? {
              status: jobStatus as "queued" | "running" | "error",
              linkedAt: jobEntry!.row.created_at,
              shareId: jobEntry!.report?.share_id ?? null,
              trigger: jobEntry!.row.trigger ?? "manual",
            }
          : null;

      // activeNightlyJob: nightly scheduled job that is currently running/queued/errored
      const nightlyJobStatus = nightlyJobEntry?.report?.status ?? null;
      const activeNightlyJob =
        nightlyJobStatus && nightlyJobStatus !== "ready"
          ? {
              status: nightlyJobStatus as "queued" | "running" | "error",
              linkedAt: nightlyJobEntry!.row.created_at,
              shareId: nightlyJobEntry!.report?.share_id ?? null,
            }
          : null;

      return {
        ...listing,
        // latestReport: latest scheduled nightly ready report only. Null if none.
        latestReport: readyEntry?.report ?? null,
        latestLinkedAt: readyEntry?.row.created_at ?? null,
        latestTrigger: readyEntry?.row.trigger ?? null,
        // runType: "nightly" when board has data, null when empty.
        runType,
        // lastNightlyCompletedAt: when nightly last produced a ready report
        lastNightlyCompletedAt: nightlyEntry?.report?.completed_at ?? null,
        activeJob,
        activeNightlyJob,
      };
    });

    const nameById = new Map<string, string>();
    for (const listing of listingRows) {
      nameById.set(listing.id, listing.name);
    }

    // recentReports shows all linked reports (any status) sorted by recency, for the history panel.
    const recentReports = links
      .map((r) => ({
        ...r,
        pricing_reports:
          normalizeReportRelation(r.pricing_reports) ??
          fallbackById.get(r.pricing_report_id) ??
          null,
      }))
      .filter((r) => !!r.pricing_reports)
      .slice(0, 5)
      .map((r) => ({
        listingId: r.saved_listing_id,
        listingName: nameById.get(r.saved_listing_id) ?? "Listing",
        linkedAt: r.created_at,
        trigger: r.trigger,
        report: r.pricing_reports,
      }));

    return NextResponse.json({
      listings: listingsWithLatest,
      recentReports,
    });
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const supabase = await getSupabaseServer();
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await req.json();
    const parsed = createListingSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    const { name, inputAddress, inputAttributes, defaultDiscountPolicy } =
      parsed.data;

    const { data: listing, error } = await supabase
      .from("saved_listings")
      .insert({
        user_id: user.id,
        name,
        input_address: inputAddress,
        input_attributes: inputAttributes,
        default_discount_policy: defaultDiscountPolicy ?? null,
      })
      .select()
      .single();

    if (error) {
      console.error("Listing insert error:", error);
      return NextResponse.json(
        { error: "Failed to create listing" },
        { status: 500 }
      );
    }

    return NextResponse.json(listing, { status: 201 });
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
