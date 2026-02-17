import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";
import {
  lastMinuteStrategyPreferenceSchema,
  type LastMinuteStrategyPreference,
} from "@/lib/schemas";

const DEFAULT_STRATEGY: LastMinuteStrategyPreference = {
  mode: "auto",
  aggressiveness: 50,
  floor: 0.65,
  cap: 1.05,
};

function isRecoverableReadError(error: { code?: string } | null): boolean {
  if (!error?.code) return false;
  // 42P01: undefined_table (migration not applied yet)
  // PGRST116: row not found / empty result edge from PostgREST
  return error.code === "42P01" || error.code === "PGRST116";
}

async function reportExists(reportId: string): Promise<boolean> {
  const admin = getSupabaseAdmin();
  const { data, error } = await admin
    .from("pricing_reports")
    .select("id")
    .eq("id", reportId)
    .maybeSingle();
  return !error && !!data;
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
  if (!(await reportExists(id))) {
    return NextResponse.json({ error: "Report not found" }, { status: 404 });
  }

  const { data, error } = await supabase
    .from("user_pricing_preferences")
    .select("mode, aggressiveness, floor, cap")
    .eq("user_id", user.id)
    .eq("pricing_report_id", id)
    .maybeSingle();

  if (error) {
    if (isRecoverableReadError(error)) {
      return NextResponse.json({ strategy: DEFAULT_STRATEGY });
    }
    return NextResponse.json({ error: "Failed to load strategy" }, { status: 500 });
  }

  let strategy = DEFAULT_STRATEGY;
  if (data) {
    const parsed = lastMinuteStrategyPreferenceSchema.safeParse({
      mode: data.mode,
      aggressiveness: Number(data.aggressiveness),
      floor: Number(data.floor),
      cap: Number(data.cap),
    });
    strategy = parsed.success ? parsed.data : DEFAULT_STRATEGY;
  }

  return NextResponse.json({ strategy });
}

export async function POST(
  req: NextRequest,
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
  if (!(await reportExists(id))) {
    return NextResponse.json({ error: "Report not found" }, { status: 404 });
  }

  const body = await req.json().catch(() => null);
  const parsed = lastMinuteStrategyPreferenceSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid strategy payload" }, { status: 400 });
  }

  const payload = parsed.data;
  const { error } = await supabase.from("user_pricing_preferences").upsert(
    {
      user_id: user.id,
      pricing_report_id: id,
      mode: payload.mode,
      aggressiveness: payload.aggressiveness,
      floor: payload.floor,
      cap: payload.cap,
      updated_at: new Date().toISOString(),
    },
    { onConflict: "user_id,pricing_report_id" }
  );

  if (error) {
    return NextResponse.json({ error: "Failed to save strategy" }, { status: 500 });
  }

  return NextResponse.json({ strategy: payload });
}
