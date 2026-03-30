/**
 * POST /api/internal/nightly/schedule
 *
 * Railway cron endpoint — call this once per day (e.g. 00:00 UTC).
 *
 * What it does:
 *   For every saved_listing, creates a live_analysis pricing_report covering
 *   the next 30 days (tomorrow → tomorrow + 29).  The worker processes it
 *   exactly like a manual live analysis — it scrapes fresh Airbnb market data
 *   using the listing's saved address, amenities, property type, and benchmark
 *   / comparable-list settings.
 *
 *   trigger = 'scheduled' on listing_reports distinguishes these from
 *   manual runs so the dashboard can label them "30-Day Market Report".
 *
 * Auth:
 *   Authorization: Bearer <INTERNAL_API_SECRET>
 *
 * Response (always 200 for scheduler-safe retries):
 *   { scheduled, skipped, total, results[] }
 */

import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";
import { generateShareId } from "@/lib/shareId";
import { computeCacheKey } from "@/lib/cacheKey";

const INTERNAL_API_SECRET = process.env.INTERNAL_API_SECRET;

/** Skip if a scheduled job already exists within this window (23 h). */
const DEDUP_HOURS = 23;

/** How many days of coverage each nightly report provides (always 30). */
const NIGHTLY_DAYS = 30;

/** Fallback timezone for listings that have no resolved timezone yet. */
const FALLBACK_TIMEZONE = "America/Los_Angeles";

/**
 * Compute the nightly date range for a specific IANA timezone.
 * startDate = local tomorrow in that timezone
 * endDate   = local tomorrow + 29 days
 *
 * Uses Intl.DateTimeFormat to get the correct local calendar date,
 * avoiding UTC truncation errors for listings outside UTC.
 */
function nightlyDateRangeForTimezone(timezone: string): { startDate: string; endDate: string } {
  let tz = timezone;
  // Validate the timezone — fall back if Intl rejects it
  try {
    Intl.DateTimeFormat(undefined, { timeZone: tz });
  } catch {
    tz = FALLBACK_TIMEZONE;
  }

  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });

  // Get today's local date string (YYYY-MM-DD) in the listing's timezone
  const todayParts = fmt.formatToParts(new Date());
  const todayStr = `${todayParts.find(p => p.type === "year")!.value}-${todayParts.find(p => p.type === "month")!.value}-${todayParts.find(p => p.type === "day")!.value}`;

  // Advance by 1 day (tomorrow) and by NIGHTLY_DAYS days (end) using UTC math
  // anchored on the local date — avoids DST drift within the window.
  const todayDate = new Date(`${todayStr}T12:00:00Z`); // noon UTC = safe midday anchor
  const startMs = todayDate.getTime() + 1 * 24 * 60 * 60 * 1000;
  const endMs   = todayDate.getTime() + NIGHTLY_DAYS * 24 * 60 * 60 * 1000;

  const fmtDate = (ms: number) => {
    const parts = fmt.formatToParts(new Date(ms));
    return `${parts.find(p => p.type === "year")!.value}-${parts.find(p => p.type === "month")!.value}-${parts.find(p => p.type === "day")!.value}`;
  };

  return { startDate: fmtDate(startMs), endDate: fmtDate(endMs) };
}

type SkipReason =
  | "duplicate_pending"
  | "duplicate_recent_ready"
  | "no_attributes"
  | "api_error";

type ResultEntry = {
  listingId: string;
  listingName: string;
  scheduled: boolean;
  reason?: SkipReason | "created";
  reportId?: string;
  shareId?: string;
};

export async function POST(req: NextRequest) {
  // ── Auth ─────────────────────────────────────────────────────────────────
  if (!INTERNAL_API_SECRET) {
    console.error("[nightly/schedule] INTERNAL_API_SECRET not configured");
    return NextResponse.json(
      { error: "Scheduler not configured — INTERNAL_API_SECRET missing" },
      { status: 500 }
    );
  }
  const authHeader = req.headers.get("authorization") ?? "";
  const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : "";
  if (token !== INTERNAL_API_SECRET) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const admin = getSupabaseAdmin();
  const dedupCutoff = new Date(
    Date.now() - DEDUP_HOURS * 60 * 60 * 1000
  ).toISOString();
  const now = new Date().toISOString();
  const targetEnv = process.env.WORKER_TARGET_ENV ?? "production";
  console.log(`[nightly/schedule] target_env=${targetEnv} job_lane=nightly`);

  // ── Fetch all saved listings ─────────────────────────────────────────────
  const { data: listings, error: listingsErr } = await admin
    .from("saved_listings")
    .select(
      "id, name, user_id, input_address, input_attributes, default_discount_policy, listing_timezone"
    )
    .order("created_at", { ascending: false });

  if (listingsErr) {
    console.error("[nightly/schedule] listings fetch failed", listingsErr);
    return NextResponse.json(
      { error: "Failed to fetch listings", detail: listingsErr.message },
      { status: 500 }
    );
  }

  if (!listings || listings.length === 0) {
    return NextResponse.json({ scheduled: 0, skipped: 0, total: 0, results: [] });
  }

  // ── Per-listing dedup check: any scheduled job in last DEDUP_HOURS hours ──
  const listingIds = listings.map((l) => l.id);
  const { data: recentLinks } = await admin
    .from("listing_reports")
    .select(
      "saved_listing_id, trigger, pricing_reports:pricing_report_id(id, status)"
    )
    .in("saved_listing_id", listingIds)
    .eq("trigger", "scheduled")
    .gte("created_at", dedupCutoff);

  // Build a set of listing IDs that already have a recent scheduled job
  type PricingReportRow = { id: string; status: string } | { id: string; status: string }[] | null;
  const recentlyScheduled = new Set<string>();
  for (const link of recentLinks ?? []) {
    const rr = link.pricing_reports as PricingReportRow;
    const r = Array.isArray(rr) ? rr[0] : rr;
    if (r && (r.status === "queued" || r.status === "running" || r.status === "ready")) {
      recentlyScheduled.add(link.saved_listing_id);
    }
  }

  // ── Create jobs ───────────────────────────────────────────────────────────
  const results: ResultEntry[] = [];

  for (const listing of listings) {
    if (recentlyScheduled.has(listing.id)) {
      results.push({
        listingId: listing.id,
        listingName: listing.name,
        scheduled: false,
        reason: "duplicate_recent_ready",
      });
      continue;
    }

    // Per-listing timezone-aware date range
    const listingTz = (listing.listing_timezone as string | null | undefined) ?? FALLBACK_TIMEZONE;
    const { startDate, endDate } = nightlyDateRangeForTimezone(listingTz);

    const attrs = (listing.input_attributes ?? {}) as Record<string, unknown>;
    const address: string = listing.input_address ?? "";

    if (!address) {
      results.push({
        listingId: listing.id,
        listingName: listing.name,
        scheduled: false,
        reason: "no_attributes",
      });
      continue;
    }

    // Determine input mode — mirror /api/reports logic
    const VALID_INPUT_MODES = ["url", "criteria", "criteria-by-city", "criteria-by-zip"];
    const savedInputMode = VALID_INPUT_MODES.includes(attrs.inputMode as string)
      ? (attrs.inputMode as string)
      : "criteria";
    const listingUrl = (attrs.listingUrl as string | null | undefined) ?? null;
    const inputMode = listingUrl ? "url" : savedInputMode;

    const discountPolicy = (listing.default_discount_policy ?? {}) as Record<string, unknown>;

    // Build full input_attributes (same shape as manual runs)
    const inputAttributes: Record<string, unknown> = {
      ...attrs,
      inputMode,
      listingUrl: listingUrl ?? null,
    };

    const cacheKey = computeCacheKey(
      address,
      inputAttributes,
      startDate,
      endDate,
      discountPolicy,
      listingUrl ?? undefined,
      inputMode
    );

    const reportId = crypto.randomUUID();
    const shareId = generateShareId();

    const report = {
      id: reportId,
      user_id: listing.user_id,
      share_id: shareId,
      listing_id: listing.id,
      report_type: "live_analysis",
      input_address: address,
      target_env: targetEnv,
      job_lane: "nightly",
      input_attributes: inputAttributes,
      input_date_start: startDate,
      input_date_end: endDate,
      discount_policy: discountPolicy,
      input_listing_url: listingUrl,
      cache_key: cacheKey,
      status: "queued",
      core_version: "pending",
      result_summary: null,
      result_calendar: null,
      completed_at: null,
      market_captured_at: null,
      error_message: null,
      result_core_debug: {
        // Force fresh scrape — nightly reports must always have current market data.
        force_rerun: true,
        cache_hit: false,
        cache_key: cacheKey,
        nightly: true,
        nightly_date_range: { startDate, endDate },
        request_source: "api/internal/nightly/schedule",
        input_mode: inputMode,
        listing_id: listing.id,
        created_at: now,
      },
    };

    const { error: insertErr } = await admin.from("pricing_reports").insert(report);

    if (insertErr) {
      console.error(
        `[nightly/schedule] insert failed for listing ${listing.id}:`,
        insertErr
      );
      results.push({
        listingId: listing.id,
        listingName: listing.name,
        scheduled: false,
        reason: "api_error",
      });
      continue;
    }

    // Link to listing with trigger='scheduled'
    await admin.from("listing_reports").insert({
      saved_listing_id: listing.id,
      pricing_report_id: reportId,
      trigger: "scheduled",
    });

    results.push({
      listingId: listing.id,
      listingName: listing.name,
      scheduled: true,
      reason: "created",
      reportId,
      shareId,
    });
  }

  const scheduled = results.filter((r) => r.scheduled).length;
  const skipped = results.length - scheduled;

  console.log(
    `[nightly/schedule] per-listing timezone dates: ` +
      `${scheduled} scheduled, ${skipped} skipped of ${listings.length} listings`
  );

  return NextResponse.json({
    scheduled,
    skipped,
    total: listings.length,
    results,
  });
}
