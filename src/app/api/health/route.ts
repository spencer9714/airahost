import { NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";

export async function GET() {
  const hasUrl = !!process.env.NEXT_PUBLIC_SUPABASE_URL;
  const hasAnon = !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const hasService = !!process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!hasUrl || !hasAnon) {
    return NextResponse.json(
      {
        ok: false,
        checks: {
          supabaseUrl: hasUrl,
          supabaseAnonKey: hasAnon,
          supabaseServiceRoleKey: hasService,
        },
      },
      { status: 503 }
    );
  }

  let dbReachable = false;
  try {
    const supabase = getSupabaseAdmin();
    const { error } = await supabase.from("pricing_reports").select("id").limit(1);
    dbReachable = !error;
  } catch {
    dbReachable = false;
  }

  return NextResponse.json(
    {
      ok: dbReachable,
      checks: {
        supabaseUrl: hasUrl,
        supabaseAnonKey: hasAnon,
        supabaseServiceRoleKey: hasService,
        dbReachable,
      },
      timestamp: new Date().toISOString(),
    },
    { status: dbReachable ? 200 : 503 }
  );
}
