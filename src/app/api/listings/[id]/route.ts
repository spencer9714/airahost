import { NextRequest, NextResponse } from "next/server";
import { updateListingSchema } from "@/lib/schemas";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";

interface LinkedReportSnapshot {
  id: string;
  share_id: string;
  status: string;
  created_at: string;
  input_date_start: string;
  input_date_end: string;
  result_summary: { nightlyMedian?: number } | null;
}

interface ReportHistoryRow {
  id: string;
  trigger: string;
  created_at: string;
  pricing_report_id: string;
  pricing_reports: LinkedReportSnapshot | null;
}

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

    const rawReportRows = (reports ?? []) as unknown as Array<{
      id: string;
      trigger: string;
      created_at: string;
      pricing_report_id: string;
      pricing_reports:
        | LinkedReportSnapshot
        | LinkedReportSnapshot[]
        | null
        | undefined;
    }>;

    const reportRows: ReportHistoryRow[] = rawReportRows.map((row) => {
      const relation = row.pricing_reports;
      const normalized = Array.isArray(relation) ? relation[0] ?? null : relation ?? null;
      return {
        id: row.id,
        trigger: row.trigger,
        created_at: row.created_at,
        pricing_report_id: row.pricing_report_id,
        pricing_reports: normalized,
      };
    });

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
          row.pricing_reports = fallbackById.get(row.pricing_report_id) ?? null;
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

    const { data: currentListing } = await supabase
      .from("saved_listings")
      .select("id, name, input_address")
      .eq("id", id)
      .eq("user_id", user.id)
      .single();

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

    // If listing name changed, propagate it to linked reports so report titles stay in sync.
    if (parsed.data.name !== undefined) {
      const nextName = parsed.data.name.trim();
      if (nextName) {
        const admin = getSupabaseAdmin();

        const { data: links } = await admin
          .from("listing_reports")
          .select("pricing_report_id")
          .eq("saved_listing_id", id);

        const reportIds = (links ?? [])
          .map((row) => row.pricing_report_id as string | null)
          .filter((v): v is string => Boolean(v));

        const directMatchCandidates = [
          currentListing?.name?.trim(),
          currentListing?.input_address?.trim(),
        ].filter((v): v is string => Boolean(v));

        // Linked reports are the authoritative source for this saved listing.
        if (reportIds.length > 0) {
          await admin
            .from("pricing_reports")
            .update({ input_address: nextName })
            .in("id", reportIds)
            .eq("user_id", user.id);

          const { data: reportRows } = await admin
            .from("pricing_reports")
            .select("id, input_attributes")
            .in("id", reportIds)
            .eq("user_id", user.id);

          for (const row of reportRows ?? []) {
            const attrs = ((row.input_attributes as Record<string, unknown> | null) ?? {});
            const nextAttrs: Record<string, unknown> = {
              ...attrs,
              address: nextName,
            };
            await admin
              .from("pricing_reports")
              .update({ input_attributes: nextAttrs })
              .eq("id", row.id)
              .eq("user_id", user.id);
          }
        }

        // Fallback: also sync any legacy reports still using old listing title/address.
        if (directMatchCandidates.length > 0) {
          await admin
            .from("pricing_reports")
            .update({ input_address: nextName })
            .eq("user_id", user.id)
            .in("input_address", directMatchCandidates);
        }
      }
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
