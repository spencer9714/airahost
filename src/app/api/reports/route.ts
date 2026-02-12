import { NextRequest, NextResponse } from "next/server";
import { createReportRequestSchema } from "@/lib/schemas";
import { generatePricingReport } from "@/core/pricingCore";
import { generateShareId } from "@/lib/shareId";
import { getSupabaseAdmin } from "@/lib/supabase";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const parsed = createReportRequestSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    const { listing, dates, discountPolicy } = parsed.data;
    const shareId = generateShareId();

    // Generate pricing using mock core
    // TODO: Replace with pythonAdapter when Python service is ready
    const result = generatePricingReport({
      listing,
      startDate: dates.startDate,
      endDate: dates.endDate,
      discountPolicy,
    });

    const report = {
      id: crypto.randomUUID(),
      share_id: shareId,
      input_address: listing.address,
      input_attributes: listing,
      input_date_start: dates.startDate,
      input_date_end: dates.endDate,
      discount_policy: discountPolicy,
      status: "ready" as const,
      core_version: result.coreVersion,
      result_summary: result.summary,
      result_calendar: result.calendar,
      error_message: null,
    };

    // Persist to Supabase if configured
    const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    if (supabaseUrl) {
      const supabase = getSupabaseAdmin();
      const { error } = await supabase.from("pricing_reports").insert(report);
      if (error) {
        console.error("Supabase insert error:", error);
      }
    }

    return NextResponse.json({
      id: report.id,
      shareId: report.share_id,
      status: report.status,
      coreVersion: result.coreVersion,
      inputAddress: listing.address,
      inputAttributes: listing,
      inputDateStart: dates.startDate,
      inputDateEnd: dates.endDate,
      discountPolicy,
      resultSummary: result.summary,
      resultCalendar: result.calendar,
      createdAt: new Date().toISOString(),
      errorMessage: null,
    });
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
