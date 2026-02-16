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
    estimatedMonthlyRevenue?: number;
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
  pricing_reports: ReportSnapshot | null;
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

    const latestByListing = new Map<string, ListingReportLinkRow>();
    for (const row of links) {
      if (!latestByListing.has(row.saved_listing_id)) {
        latestByListing.set(row.saved_listing_id, {
          ...row,
          pricing_reports:
            row.pricing_reports ?? fallbackById.get(row.pricing_report_id) ?? null,
        });
      }
    }

    const listingsWithLatest = listingRows.map((listing) => {
      const latest = latestByListing.get(listing.id);
      return {
        ...listing,
        latestReport: latest?.pricing_reports ?? null,
        latestLinkedAt: latest?.created_at ?? null,
      };
    });

    const nameById = new Map<string, string>();
    for (const listing of listingRows) {
      nameById.set(listing.id, listing.name);
    }

    const recentReports = links
      .map((r) => ({
        ...r,
        pricing_reports:
          r.pricing_reports ?? fallbackById.get(r.pricing_report_id) ?? null,
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
