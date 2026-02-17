import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";

async function isReportSavedForUser(
  userId: string,
  reportId: string
): Promise<boolean> {
  const admin = getSupabaseAdmin();

  const { data: reportRow, error: reportErr } = await admin
    .from("pricing_reports")
    .select("id, user_id")
    .eq("id", reportId)
    .maybeSingle();

  if (reportErr || !reportRow) return false;
  if (reportRow.user_id === userId) return true;

  const { data: listingRows, error: listingsErr } = await admin
    .from("saved_listings")
    .select("id")
    .eq("user_id", userId);

  if (listingsErr || !listingRows || listingRows.length === 0) return false;

  const listingIds = listingRows.map((row) => row.id);
  const { data: linkedRows, error: linkedErr } = await admin
    .from("listing_reports")
    .select("id")
    .eq("pricing_report_id", reportId)
    .in("saved_listing_id", listingIds)
    .limit(1);

  if (linkedErr) return false;
  return Boolean(linkedRows && linkedRows.length > 0);
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const supabase = await getSupabaseServer();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const saved = await isReportSavedForUser(user.id, id);
  return NextResponse.json({ saved });
}

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const supabase = await getSupabaseServer();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Please sign in first." }, { status: 401 });
  }

  const { id } = await params;
  const admin = getSupabaseAdmin();

  const alreadySaved = await isReportSavedForUser(user.id, id);
  if (alreadySaved) {
    return NextResponse.json({ saved: true });
  }

  const { data: reportRow, error: reportErr } = await admin
    .from("pricing_reports")
    .select("id, input_address, input_attributes, discount_policy")
    .eq("id", id)
    .maybeSingle();

  if (reportErr || !reportRow) {
    return NextResponse.json({ error: "Report not found." }, { status: 404 });
  }

  const defaultName =
    typeof reportRow.input_address === "string" && reportRow.input_address.trim()
      ? reportRow.input_address.trim()
      : "Saved listing";

  const { data: listingRow, error: listingErr } = await admin
    .from("saved_listings")
    .insert({
      user_id: user.id,
      name: defaultName,
      input_address: reportRow.input_address,
      input_attributes: reportRow.input_attributes,
      default_discount_policy: reportRow.discount_policy,
      last_used_at: new Date().toISOString(),
    })
    .select("id")
    .single();

  if (listingErr || !listingRow) {
    return NextResponse.json(
      { error: "Failed to save listing." },
      { status: 500 }
    );
  }

  const { error: linkErr } = await admin.from("listing_reports").insert({
    saved_listing_id: listingRow.id,
    pricing_report_id: reportRow.id,
    trigger: "manual",
  });

  if (linkErr) {
    return NextResponse.json(
      { error: "Failed to link saved report." },
      { status: 500 }
    );
  }

  return NextResponse.json({ saved: true });
}
