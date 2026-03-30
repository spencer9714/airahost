/**
 * POST /api/internal/forecast/schedule
 *
 * Internal scheduler endpoint — called by Railway cron (or any HTTP trigger).
 * NOT exposed to end users.  Validated by a shared secret.
 *
 * What it does:
 *   For every saved listing that has a ready live_analysis report within the
 *   freshness window, creates a forecast_snapshot job (if one is not already
 *   pending).  Delegates all business logic to the per-listing forecast API
 *   so dedup / stale / no-source rules are applied consistently.
 *
 * Auth:
 *   Header: Authorization: Bearer <INTERNAL_API_SECRET>
 *   Set INTERNAL_API_SECRET in both Railway and your Next.js deployment env.
 *
 * Response:
 *   { scheduled, skipped, results[] }
 *   Always 200 so Railway does not retry on business-logic skips.
 *   Returns 401 on bad secret, 500 on unexpected error.
 */

import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin } from "@/lib/supabase";

const INTERNAL_API_SECRET = process.env.INTERNAL_API_SECRET;

export async function POST(req: NextRequest) {
  // ── Auth ─────────────────────────────────────────────────────────────────
  if (!INTERNAL_API_SECRET) {
    console.error("INTERNAL_API_SECRET is not configured");
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
  const results: Array<{
    listingId: string;
    created: boolean;
    reason: string;
    reportId?: string;
    shareId?: string;
    detail?: string;
  }> = [];

  try {
    // ── Fetch all distinct listing IDs that have at least one ready report ──
    // We don't filter by user — this runs across all listings.
    const { data: links, error: linksErr } = await admin
      .from("listing_reports")
      .select("saved_listing_id")
      .order("created_at", { ascending: false });

    if (linksErr) {
      console.error("forecast/schedule: listing fetch failed", linksErr);
      return NextResponse.json(
        { error: "Failed to fetch listings", detail: linksErr.message },
        { status: 500 }
      );
    }

    // Deduplicate listing IDs
    const listingIds = [...new Set((links ?? []).map((r) => r.saved_listing_id))];

    if (listingIds.length === 0) {
      return NextResponse.json({ scheduled: 0, skipped: 0, results: [] });
    }

    // ── Call the per-listing forecast API for each listing ──────────────────
    // We derive the base URL from the incoming request so this works in any
    // environment (local, staging, production) without hardcoding.
    const baseUrl = new URL(req.url).origin;

    await Promise.allSettled(
      listingIds.map(async (listingId) => {
        try {
          const res = await fetch(
            `${baseUrl}/api/internal/forecast/listing/${listingId}`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${INTERNAL_API_SECRET}`,
              },
            }
          );

          if (!res.ok) {
            results.push({
              listingId,
              created: false,
              reason: "api_error",
              detail: `HTTP ${res.status}`,
            });
            return;
          }

          const data = (await res.json()) as {
            created: boolean;
            reason: string;
            reportId?: string;
            shareId?: string;
            staleDays?: number;
            message?: string;
          };

          results.push({
            listingId,
            created: data.created,
            reason: data.reason,
            reportId: data.reportId,
            shareId: data.shareId,
            detail: data.message,
          });
        } catch (err) {
          results.push({
            listingId,
            created: false,
            reason: "fetch_error",
            detail: err instanceof Error ? err.message : String(err),
          });
        }
      })
    );

    const scheduled = results.filter((r) => r.created).length;
    const skipped = results.length - scheduled;

    console.log(
      `[forecast/schedule] Done — ${scheduled} scheduled, ${skipped} skipped across ${listingIds.length} listings`
    );

    return NextResponse.json({ scheduled, skipped, results });
  } catch (err) {
    console.error("forecast/schedule: unexpected error", err);
    return NextResponse.json(
      {
        error: "Internal server error",
        detail: err instanceof Error ? err.message : String(err),
      },
      { status: 500 }
    );
  }
}
