import { NextRequest, NextResponse } from "next/server";
import { rerunListingSchema } from "@/lib/schemas";
import { generateShareId } from "@/lib/shareId";
import { getSupabaseAdmin } from "@/lib/supabase";
import { getSupabaseServer } from "@/lib/supabaseServer";
import { computeCacheKey } from "@/lib/cacheKey";

export async function POST(
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

    // Fetch the saved listing (RLS scopes to user)
    const { data: listing, error: listingError } = await supabase
      .from("saved_listings")
      .select("*")
      .eq("id", id)
      .eq("user_id", user.id)
      .single();

    if (listingError || !listing) {
      return NextResponse.json(
        { error: "Listing not found" },
        { status: 404 }
      );
    }

    // Parse rerun body
    const body = await req.json();
    const parsed = rerunListingSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid input", details: parsed.error.flatten() },
        { status: 400 }
      );
    }

    const attrs = (listing.input_attributes ?? {}) as Record<string, unknown>;
    const fallbackInputMode =
      attrs.inputMode === "url" || attrs.inputMode === "criteria"
        ? attrs.inputMode
        : "criteria";
    const fallbackListingUrl =
      typeof attrs.listingUrl === "string" ? attrs.listingUrl : undefined;

    const {
      dates,
      inputMode = fallbackInputMode,
      listingUrl = fallbackListingUrl,
    } = parsed.data;
    const discountPolicy =
      parsed.data.discountPolicy ?? listing.default_discount_policy ?? {};
    const attributes = listing.input_attributes;
    const address = listing.input_address;

    // Use admin client for cache + report creation (bypasses RLS for pricing_cache)
    const admin = getSupabaseAdmin();

    // Cache key
    const cacheKey = computeCacheKey(
      address,
      attributes as Record<string, unknown>,
      dates.startDate,
      dates.endDate,
      discountPolicy as Record<string, unknown>,
      listingUrl,
      inputMode
    );

    // Check cache
    let cachedSummary = null;
    let cachedCalendar = null;
    try {
      const { data: cacheRows } = await admin
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
      // Cache miss — proceed as queued
    }

    const isCacheHit = cachedSummary !== null;
    const shareId = generateShareId();

    const report = {
      id: crypto.randomUUID(),
      user_id: user.id,
      share_id: shareId,
      listing_id: id,
      input_address: address,
      input_attributes: { ...attributes, inputMode },
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
        request_source: "api/listings/[id]/rerun",
        input_mode: inputMode,
        listing_id: id,
        report_input: {
          mode: inputMode,
          listing_url: listingUrl || null,
          listing_address: address,
          listing_attributes: attributes,
          date_start: dates.startDate,
          date_end: dates.endDate,
          discount_policy: discountPolicy,
        },
        created_at: new Date().toISOString(),
      },
      error_message: null,
    };

    const { error: insertError } = await admin
      .from("pricing_reports")
      .insert(report);

    if (insertError) {
      console.error("Report insert error:", insertError);
      return NextResponse.json(
        { error: "Failed to create report" },
        { status: 500 }
      );
    }

    // Link report to listing
    await admin.from("listing_reports").insert({
      saved_listing_id: id,
      pricing_report_id: report.id,
      trigger: "rerun",
    });

    // Update last_used_at
    await supabase
      .from("saved_listings")
      .update({ last_used_at: new Date().toISOString() })
      .eq("id", id);

    return NextResponse.json({
      id: report.id,
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
