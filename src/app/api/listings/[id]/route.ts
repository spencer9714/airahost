import { NextRequest, NextResponse } from "next/server";
import { updateListingSchema } from "@/lib/schemas";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const supabase = await getSupabaseServer();
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { data: listing, error } = await supabase
      .from("saved_listings")
      .select("*")
      .eq("id", id)
      .eq("user_id", user.id)
      .single();

    if (error || !listing) {
      return NextResponse.json(
        { error: "Listing not found" },
        { status: 404 }
      );
    }

    // Fetch linked reports
    const { data: reports } = await supabase
      .from("listing_reports")
      .select(
        "id, trigger, created_at, pricing_report_id, pricing_reports:pricing_report_id(id, share_id, status, created_at, input_date_start, input_date_end, result_summary)"
      )
      .eq("saved_listing_id", id)
      .order("created_at", { ascending: false })
      .limit(20);

    const reportRows = (reports ?? []) as Array<{
      id: string;
      trigger: string;
      created_at: string;
      pricing_report_id: string;
      pricing_reports: {
        id: string;
        share_id: string;
        status: string;
        created_at: string;
        input_date_start: string;
        input_date_end: string;
        result_summary: { nightlyMedian?: number } | null;
      } | null;
    }>;

    const missingReportIds = reportRows
      .filter((row) => !row.pricing_reports && row.pricing_report_id)
      .map((row) => row.pricing_report_id);

    if (missingReportIds.length > 0) {
      const admin = getSupabaseAdmin();
      const { data: fallbackRows } = await admin
        .from("pricing_reports")
        .select(
          "id, share_id, status, created_at, input_date_start, input_date_end, result_summary"
        )
        .in("id", missingReportIds);

      const fallbackById = new Map(
        (fallbackRows ?? []).map((r) => [r.id as string, r])
      );

      for (const row of reportRows) {
        if (!row.pricing_reports) {
          row.pricing_reports =
            (fallbackById.get(row.pricing_report_id) as typeof row.pricing_reports) ??
            null;
        }
      }
    }

    return NextResponse.json({ listing, reports: reportRows });
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const supabase = await getSupabaseServer();
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const body = await req.json();
    const parsed = updateListingSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    const updates: Record<string, unknown> = {};
    if (parsed.data.name !== undefined) updates.name = parsed.data.name;
    if (parsed.data.inputAddress !== undefined)
      updates.input_address = parsed.data.inputAddress;
    if (parsed.data.inputAttributes !== undefined)
      updates.input_attributes = parsed.data.inputAttributes;
    if (parsed.data.defaultDiscountPolicy !== undefined)
      updates.default_discount_policy = parsed.data.defaultDiscountPolicy;

    const { data: listing, error } = await supabase
      .from("saved_listings")
      .update(updates)
      .eq("id", id)
      .eq("user_id", user.id)
      .select()
      .single();

    if (error || !listing) {
      return NextResponse.json(
        { error: "Listing not found" },
        { status: 404 }
      );
    }

    return NextResponse.json(listing);
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const supabase = await getSupabaseServer();
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (!user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { error } = await supabase
      .from("saved_listings")
      .delete()
      .eq("id", id)
      .eq("user_id", user.id);

    if (error) {
      return NextResponse.json(
        { error: "Failed to delete listing" },
        { status: 500 }
      );
    }

    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
