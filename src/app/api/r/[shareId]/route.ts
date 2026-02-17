import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ shareId: string }> }
) {
  const { shareId } = await params;

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!supabaseUrl) {
    return NextResponse.json(
      { error: "Database not configured" },
      { status: 503 }
    );
  }

  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from("pricing_reports")
    .select("*")
    .eq("share_id", shareId)
    .single();

  if (error || !data) {
    return NextResponse.json({ error: "Report not found" }, { status: 404 });
  }

  const summary = data.result_summary;
  return NextResponse.json({
    id: data.id,
    shareId: data.share_id,
    status: data.status,
    coreVersion: data.core_version,
    inputAddress: data.input_address,
    inputAttributes: data.input_attributes,
    inputDateStart: data.input_date_start,
    inputDateEnd: data.input_date_end,
    discountPolicy: data.discount_policy,
    resultSummary: summary,
    resultCalendar: data.result_calendar,
    createdAt: data.created_at,
    errorMessage: data.error_message,
    workerAttempts: data.worker_attempts,
    // Transparency fields (extracted from result_summary for convenience)
    targetSpec: summary?.targetSpec ?? null,
    queryCriteria: summary?.queryCriteria ?? null,
    compsSummary: summary?.compsSummary ?? null,
    priceDistribution: summary?.priceDistribution ?? null,
    recommendedPrice: summary?.recommendedPrice ?? null,
    comparableListings: summary?.comparableListings ?? null,
  });
}
