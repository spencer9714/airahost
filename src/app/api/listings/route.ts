import { NextRequest, NextResponse } from "next/server";
import { createListingSchema } from "@/lib/schemas";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";

interface ReportSnapshot {
  id: string;
  share_id: string;
  status: string;
  created_at: string;
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
        "saved_listing_id, pricing_report_id, created_at, trigger, pricing_reports:pricing_report_id(id, share_id, status, created_at, input_date_start, input_date_end, result_summary, result_calendar)"
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
          "id, share_id, status, created_at, input_date_start, input_date_end, result_summary, result_calendar"
        )
        .in("id", missingReportIds);

      for (const row of fallbackRows ?? []) {
        fallbackById.set(row.id, row as ReportSnapshot);
      }
    }

    // ── Two-pass per-listing selection ───────────────────────────
    // latestJobByListing  → most recent link of ANY status (for active-job tracking)
    // latestReadyByListing → most recent link whose report is status="ready" (for pricing display)
    //
    // This decouples "what is running right now" from "what pricing data to show".
    // If the newest run failed or is still in-progress, we still surface the last
    // successful report's pricing rather than blanking the dashboard.
    const latestJobByListing = new Map<
      string,
      { row: ListingReportLinkRow; report: ReportSnapshot | null }
    >();
    const latestReadyByListing = new Map<
      string,
      { row: ListingReportLinkRow; report: ReportSnapshot }
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

      // Most recent READY report (keep iterating until we find one)
      if (report?.status === "ready" && !latestReadyByListing.has(id)) {
        latestReadyByListing.set(id, { row, report });
      }
    }

    const listingsWithLatest = listingRows.map((listing) => {
      const readyEntry = latestReadyByListing.get(listing.id);
      const jobEntry = latestJobByListing.get(listing.id);

      const jobStatus = (jobEntry?.report?.status ?? null) as
        | "queued"
        | "running"
        | "error"
        | "ready"
        | null;

      // activeJob is only set when the most recent linked report is NOT ready.
      // This tells the UI a new analysis is running or the last one failed,
      // so it can show a banner while still displaying the ready report's pricing.
      const activeJob =
        jobStatus && jobStatus !== "ready"
          ? {
              status: jobStatus as "queued" | "running" | "error",
              linkedAt: jobEntry!.row.created_at,
              shareId: jobEntry!.report?.share_id ?? null,
            }
          : null;

      return {
        ...listing,
        // latestReport is ALWAYS the most recent ready report (or null if none exist).
        latestReport: readyEntry?.report ?? null,
        // latestLinkedAt reflects when the ready report was linked — used as "last analysed" date.
        latestLinkedAt: readyEntry?.row.created_at ?? null,
        activeJob,
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
