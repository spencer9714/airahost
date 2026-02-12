import { NextRequest, NextResponse } from "next/server";
import { trackMarketRequestSchema } from "@/lib/schemas";
import { getSupabaseAdmin } from "@/lib/supabase";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const parsed = trackMarketRequestSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    const { email, address, notifyWeekly, notifyUnderMarket } = parsed.data;

    const record = {
      id: crypto.randomUUID(),
      email,
      address,
      notify_weekly: notifyWeekly,
      notify_under_market: notifyUnderMarket,
    };

    // Persist to Supabase if configured
    const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    if (supabaseUrl) {
      const supabase = getSupabaseAdmin();
      const { error } = await supabase
        .from("market_tracking_preferences")
        .insert(record);

      if (error) {
        console.error("Supabase insert error:", error);
        return NextResponse.json(
          { error: "Failed to save preferences" },
          { status: 500 }
        );
      }
    }

    return NextResponse.json({ success: true, id: record.id });
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
