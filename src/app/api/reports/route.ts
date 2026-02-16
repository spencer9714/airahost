import { NextRequest, NextResponse } from "next/server";
import { createReportRequestSchema } from "@/lib/schemas";
import { generateShareId } from "@/lib/shareId";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";
import { computeCacheKey } from "@/lib/cacheKey";

// ── Simple in-memory IP rate limiter ──────────────────────────
const rateMap = new Map<string, { count: number; resetAt: number }>();
const RATE_WINDOW_MS = 60_000; // 1 minute
const RATE_LIMIT = 10; // max requests per window per IP

function isRateLimited(ip: string): boolean {
  const now = Date.now();
  const entry = rateMap.get(ip);
  if (!entry || now > entry.resetAt) {
    rateMap.set(ip, { count: 1, resetAt: now + RATE_WINDOW_MS });
    return false;
  }
  entry.count++;
  return entry.count > RATE_LIMIT;
}

export async function POST(req: NextRequest) {
  try {
    // Rate limiting
    const ip =
      req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
      req.headers.get("x-real-ip") ||
      "unknown";
    if (isRateLimited(ip)) {
      return NextResponse.json(
        { error: "Too many requests. Please wait a moment." },
        { status: 429 }
      );
    }

    const body = await req.json();
    const parsed = createReportRequestSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    const {
      inputMode,
      listing,
      dates,
      discountPolicy,
      listingUrl,
      saveToListings,
    } = parsed.data;
    const shareId = generateShareId();

    const authClient = await getSupabaseServer();
    const {
      data: { user },
    } = await authClient.auth.getUser();
    const requestUserId = user?.id ?? null;

    let saveUserId: string | null = null;
    if (saveToListings?.enabled) {
      if (!requestUserId) {
        return NextResponse.json(
          { error: "Please sign in to save this listing." },
          { status: 401 }
        );
      }
      saveUserId = requestUserId;
    }

    const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    if (!supabaseUrl) {
      return NextResponse.json(
        { error: "Database not configured" },
        { status: 503 }
      );
    }

    const supabase = getSupabaseAdmin();

    // Compute cache key
    const cacheKey = computeCacheKey(
      listing.address,
      listing as unknown as Record<string, unknown>,
      dates.startDate,
      dates.endDate,
      discountPolicy as unknown as Record<string, unknown>,
      listingUrl,
      inputMode
    );

    // Check cache — if hit, create report as ready immediately
    let cachedSummary = null;
    let cachedCalendar = null;
    try {
      const { data: cacheRows } = await supabase
        .from("pricing_cache")
        .select("summary, calendar")
        .eq("cache_key", cacheKey)
        .gt("expires_at", new Date().toISOString())
        .limit(1);

      if (cacheRows && cacheRows.length > 0) {
        cachedSummary = cacheRows[0].summary;
        cachedCalendar = cacheRows[0].calendar;
      }
    } catch {
      // Cache lookup failed — proceed as queued
    }

    const isCacheHit = cachedSummary !== null;

    const enrichedInputAttributes = {
      ...listing,
      inputMode,
      listingUrl: listingUrl || null,
    };

    const report = {
      id: crypto.randomUUID(),
      user_id: requestUserId,
      share_id: shareId,
      input_address: listing.address,
      input_attributes: enrichedInputAttributes,
      input_date_start: dates.startDate,
      input_date_end: dates.endDate,
      discount_policy: discountPolicy,
      input_listing_url: listingUrl || null,
      cache_key: cacheKey,
      status: isCacheHit ? "ready" : "queued",
      core_version: isCacheHit ? "cache-hit" : "pending",
      result_summary: cachedSummary,
      result_calendar: cachedCalendar,
      result_core_debug: {
        cache_hit: isCacheHit,
        cache_key: cacheKey,
        request_source: "api/reports",
        input_mode: inputMode,
        report_input: {
          mode: inputMode,
          listing_url: listingUrl || null,
          listing_address: listing.address,
          listing_attributes: listing,
          date_start: dates.startDate,
          date_end: dates.endDate,
          discount_policy: discountPolicy,
        },
        created_at: new Date().toISOString(),
      },
      error_message: null,
    };

    const { data: insertRows, error } = await supabase
      .from("pricing_reports")
      .insert(report)
      .select("id")
      .limit(1);
    if (error) {
      console.error("Supabase insert error:", error);
      return NextResponse.json(
        { error: "Failed to create report" },
        { status: 500 }
      );
    }

    const reportId = (insertRows && insertRows[0]?.id) || report.id;

    if (saveToListings?.enabled && saveUserId) {
      const listingName = (saveToListings.name || "").trim() || listing.address;

      const { data: listingRow, error: listingErr } = await supabase
        .from("saved_listings")
        .insert({
          user_id: saveUserId,
          name: listingName,
          input_address: listing.address,
          input_attributes: enrichedInputAttributes,
          default_discount_policy: discountPolicy,
          last_used_at: new Date().toISOString(),
        })
        .select("id")
        .single();

      if (!listingErr && listingRow?.id) {
        await supabase.from("listing_reports").insert({
          saved_listing_id: listingRow.id,
          pricing_report_id: reportId,
          trigger: "manual",
        });
      }
    }

    return NextResponse.json({
      id: reportId,
      shareId: report.share_id,
      status: report.status,
    });
  } catch {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
