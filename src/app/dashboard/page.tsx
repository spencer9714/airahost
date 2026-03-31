"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { getSupabaseBrowser } from "@/lib/supabase";
import { RecommendationBanner } from "@/components/dashboard/RecommendationBanner";
import { PricingHeatmap } from "@/components/dashboard/PricingHeatmap";
import { PriceLineChart } from "@/components/dashboard/PriceLineChart";
import { ForecastBasis } from "@/components/dashboard/ForecastBasis";
import { SmartAlerts } from "@/components/dashboard/SmartAlerts";
import { ListingCard } from "@/components/dashboard/ListingCard";
import { extractAirbnbListingId } from "@/lib/airbnb-utils";
import { BenchmarkModal } from "@/components/dashboard/BenchmarkModal";
import { resolveMarketCapturedAt } from "@/lib/freshness";
import type {
  PropertyType,
  CalendarDay,
  ReportSummary,
  RecommendedPrice,
  CompsSummary,
  PriceDistribution,
  DateMode,
} from "@/lib/schemas";

// latestReport is always status="ready" when non-null — the API now guarantees this.
// Only live_analysis reports are selected as source-of-truth; forecast_snapshot is ignored.
type LatestReport = {
  id: string;
  share_id: string;
  status: "ready";
  report_type?: "live_analysis";
  source_report_id?: string | null;
  created_at: string;
  /** Set when status transitions to ready (migration 010). Null for older reports. */
  completed_at?: string | null;
  /** When Airbnb market data was captured (migration 010). Use for freshness. */
  market_captured_at?: string | null;
  input_date_start: string;
  input_date_end: string;
  result_summary: {
    nightlyMin?: number;
    nightlyMedian?: number;
    nightlyMax?: number;
    occupancyPct?: number;
    weekdayAvg?: number;
    weekendAvg?: number;
    estimatedMonthlyRevenue?: number;
    recommendedPrice?: RecommendedPrice;
    compsSummary?: CompsSummary;
    priceDistribution?: PriceDistribution;
    // Live price intelligence (added by worker)
    observedListingPrice?: number | null;
    observedListingPriceDate?: string | null;
    observedListingPriceCapturedAt?: string | null;
    observedListingPriceSource?: string | null;
    observedListingPriceConfidence?: string | null;
    observedVsMarketDiff?: number | null;
    observedVsMarketDiffPct?: number | null;
    observedVsRecommendedDiff?: number | null;
    observedVsRecommendedDiffPct?: number | null;
    pricingPosition?: "above_market" | "at_market" | "below_market" | null;
    pricingAction?: "raise" | "lower" | "keep" | null;
    pricingActionTarget?: number | null;
    livePriceStatus?: string | null;
    livePriceStatusReason?: string | null;
  } | null;
  result_calendar?: CalendarDay[];
} | null;

// activeJob tracks the newest linked report when it is NOT ready.
// It exists alongside latestReport so the UI can show a banner without hiding pricing.
type ActiveJob = {
  status: "queued" | "running" | "error";
  linkedAt: string;
  shareId: string | null;
  trigger?: string;
};

type ListingRow = {
  id: string;
  name: string;
  input_address: string;
  input_attributes: {
    propertyType: PropertyType;
    bedrooms: number;
    bathrooms: number;
    maxGuests: number;
    beds?: number;
    amenities?: string[];
    address?: string;
    listingUrl?: string | null;
    listing_url?: string | null;
    preferredComps?: Array<{ listingUrl: string; name?: string; note?: string; enabled?: boolean }> | null;
  };
  created_at: string;
  last_used_at: string | null;
  default_date_mode?: DateMode;
  default_start_date?: string | null;
  default_end_date?: string | null;
  latestReport: LatestReport;
  latestLinkedAt: string | null;
  latestTrigger: "scheduled" | "manual" | "rerun" | null;
  /** "nightly" when the board has a ready nightly report, null otherwise */
  runType: "nightly" | null;
  /** When the nightly last completed successfully */
  lastNightlyCompletedAt: string | null;
  activeJob: ActiveJob | null;
  /** Nightly-specific job state (running/queued/errored) — separate from activeJob */
  activeNightlyJob: {
    status: "queued" | "running" | "error";
    linkedAt: string;
    shareId: string | null;
  } | null;
  // Pricing alert fields (migration 014)
  pricing_alerts_enabled?: boolean;
  last_alert_sent_at?: string | null;
  last_alert_direction?: string | null;
  last_live_price_status?: string | null;
  // Alert v2 fields (migration 015)
  minimum_booking_nights?: number;
  listing_url_validation_status?: string | null;
};

// RecentReportRow covers all statuses — recentReports from the API is not filtered by status.
type RecentReportRow = {
  listingId: string;
  listingName: string;
  linkedAt: string;
  trigger: "manual" | "rerun" | "scheduled";
  report: {
    id: string;
    share_id: string;
    status: string;
    created_at: string;
    input_date_start: string;
    input_date_end: string;
    result_summary: {
      nightlyMedian?: number;
      recommendedPrice?: { nightly: number | null };
    } | null;
  };
};

export default function DashboardPage() {
  const router = useRouter();
  const [authReady, setAuthReady] = useState(false);
  const [userName, setUserName] = useState("");
  const [userEmail, setUserEmail] = useState("");
  const [listings, setListings] = useState<ListingRow[]>([]);
  const [recentReports, setRecentReports] = useState<RecentReportRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rerunningId, setRerunningId] = useState<string | null>(null);
  const [activeListingId, setActiveListingId] = useState<string | null>(null);
  const [benchmarkModalListingId, setBenchmarkModalListingId] = useState<string | null>(null);
  const [pricingMode, setPricingMode] = useState<"refundable" | "nonRefundable">("refundable");
  const [showCustomPanel, setShowCustomPanel] = useState(false);
  // Default to tomorrow — custom analyses are future-only.
  const [customStart, setCustomStart] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    return d.toISOString().split("T")[0];
  });
  const [customEnd, setCustomEnd] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 30);
    return d.toISOString().split("T")[0];
  });
  // Today's date string used as the minimum selectable date across all date inputs.
  const todayStr = new Date().toISOString().split("T")[0];

  const loadDashboardData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/listings");
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) throw new Error("Failed to load dashboard data");

      const data = await res.json();
      const loadedListings = (data.listings ?? []) as ListingRow[];
      setListings(loadedListings);
      setRecentReports((data.recentReports ?? []) as RecentReportRow[]);

      // Auto-select once: prefer the listing with the most recently linked ready report.
      setActiveListingId((prev) => {
        if (prev || loadedListings.length === 0) return prev;
        const withReady = loadedListings
          .filter((l) => l.latestReport !== null)
          .sort((a, b) =>
            (b.latestLinkedAt ?? "").localeCompare(a.latestLinkedAt ?? "")
          );
        return withReady[0]?.id ?? loadedListings[0].id;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    const supabase = getSupabaseBrowser();
    supabase.auth.getUser().then(({ data: { user } }) => {
      if (!user) {
        router.push("/login");
        return;
      }
      const displayName =
        (user.user_metadata?.full_name as string | undefined) ||
        (user.user_metadata?.name as string | undefined) ||
        "";
      setUserName(displayName);
      setUserEmail(user.email ?? "");
      setAuthReady(true);
      void loadDashboardData();
    });
  }, [router, loadDashboardData]);

  async function handleRunAnalysis(
    listingId: string,
    dates: { startDate: string; endDate: string }
  ) {
    // Future-only guard: reject any start date before today.
    const todayStr = new Date().toISOString().split("T")[0];
    if (dates.startDate < todayStr) return;
    if (dates.endDate < dates.startDate) return;

    setRerunningId(listingId);
    try {
      const res = await fetch("/api/reports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ listingId, dateRange: dates }),
      });
      if (!res.ok) throw new Error("Failed to run analysis");
      const data = await res.json();
      await loadDashboardData();
      if (data.shareId) router.push(`/r/${data.shareId}`);
    } catch {
      // Keep UI stable; list refresh handles latest state.
    } finally {
      setRerunningId(null);
    }
  }

  async function handleDelete(listingId: string) {
    const res = await fetch(`/api/listings/${listingId}`, { method: "DELETE" });
    if (res.ok) {
      if (activeListingId === listingId) setActiveListingId(null);
      await loadDashboardData();
    }
  }

  async function handleRenameListing(listingId: string, nextName: string) {
    const trimmed = nextName.trim();
    if (!trimmed) return;

    const previousListings = listings;
    const previousRecent = recentReports;

    setListings((prev) =>
      prev.map((l) => (l.id === listingId ? { ...l, name: trimmed } : l))
    );
    setRecentReports((prev) =>
      prev.map((r) =>
        r.listingId === listingId ? { ...r, listingName: trimmed } : r
      )
    );

    const res = await fetch(`/api/listings/${listingId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });

    if (!res.ok) {
      setListings(previousListings);
      setRecentReports(previousRecent);
      throw new Error("Failed to rename listing");
    }
  }

  async function handleSavePreferredComps(
    listingId: string,
    preferredComps: Array<{ listingUrl: string; name?: string; note?: string; enabled?: boolean }> | null
  ) {
    const res = await fetch(`/api/listings/${listingId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferredComps }),
    });
    if (!res.ok) throw new Error("Failed to save benchmark listings");
    await loadDashboardData();
  }

  async function handleSaveAlertSettings(
    listingId: string,
    settings: {
      listingUrl?: string | null;
      minimumBookingNights?: number;
      pricingAlertsEnabled?: boolean;
    }
  ) {
    const res = await fetch(`/api/listings/${listingId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(settings.listingUrl !== undefined && { listingUrl: settings.listingUrl }),
        ...(settings.minimumBookingNights !== undefined && { minimumBookingNights: settings.minimumBookingNights }),
        ...(settings.pricingAlertsEnabled !== undefined && { pricingAlertsEnabled: settings.pricingAlertsEnabled }),
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error((body as { error?: string }).error ?? "Failed to save alert settings");
    }
    // Refresh listings so all fields (URL, min_nights, alert state) are in sync.
    await loadDashboardData();
  }

  // ── Derived state ─────────────────────────────────────────────────
  const activeListing = useMemo(
    () => listings.find((l) => l.id === activeListingId) ?? null,
    [listings, activeListingId]
  );

  // activeReport is always a "ready" report or null — no status check needed.
  const activeReport = activeListing?.latestReport ?? null;

  const activeSummary: ReportSummary | null = useMemo(() => {
    const s = activeReport?.result_summary;
    if (!s) return null;
    return {
      insightHeadline: "",
      nightlyMin: s.nightlyMin ?? 0,
      nightlyMedian: s.nightlyMedian ?? 0,
      nightlyMax: s.nightlyMax ?? 0,
      occupancyPct: s.occupancyPct ?? 0,
      weekdayAvg: s.weekdayAvg ?? 0,
      weekendAvg: s.weekendAvg ?? 0,
      estimatedMonthlyRevenue: s.estimatedMonthlyRevenue ?? 0,
      weeklyStayAvgNightly: 0,
      monthlyStayAvgNightly: 0,
      recommendedPrice: s.recommendedPrice,
      compsSummary: s.compsSummary,
      priceDistribution: s.priceDistribution,
      // Live price intelligence
      observedListingPrice: s.observedListingPrice ?? null,
      observedListingPriceDate: s.observedListingPriceDate ?? null,
      observedListingPriceCapturedAt: s.observedListingPriceCapturedAt ?? null,
      observedListingPriceSource: s.observedListingPriceSource ?? null,
      observedListingPriceConfidence: s.observedListingPriceConfidence ?? null,
      observedVsMarketDiff: s.observedVsMarketDiff ?? null,
      observedVsMarketDiffPct: s.observedVsMarketDiffPct ?? null,
      observedVsRecommendedDiff: s.observedVsRecommendedDiff ?? null,
      observedVsRecommendedDiffPct: s.observedVsRecommendedDiffPct ?? null,
      pricingPosition: s.pricingPosition ?? null,
      pricingAction: s.pricingAction ?? null,
      pricingActionTarget: s.pricingActionTarget ?? null,
      livePriceStatus: s.livePriceStatus ?? null,
      livePriceStatusReason: s.livePriceStatusReason ?? null,
    };
  }, [activeReport]);

  const activeCalendar = activeReport?.result_calendar ?? [];

  const listingCountText = useMemo(
    () => `${listings.length} listing${listings.length === 1 ? "" : "s"} saved`,
    [listings.length]
  );

  if (!authReady || loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <p className="text-sm text-foreground/40">Loading dashboard…</p>
      </div>
    );
  }

  const firstName = userName ? userName.split(" ")[0] : null;
  const activeAirbnbListingLabel = activeListing
    ? (() => {
        const airbnbId =
          extractAirbnbListingId(
            activeListing.input_attributes.listingUrl ??
              activeListing.input_attributes.listing_url ??
              null
          ) ??
          activeListing.input_address.match(/Airbnb Listing #(\d+)/i)?.[1] ??
          null;

        return airbnbId ? `Airbnb Listing #${airbnbId}` : null;
      })()
    : null;

  return (
    <div className="min-h-screen bg-gray-50/50">
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">

        {/* ── Header ── */}
        <div className="mb-8">
          <p className="mb-1 text-xs font-semibold uppercase tracking-widest text-foreground/30">
            Host Dashboard
          </p>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            {firstName ? `Welcome back, ${firstName}` : (userEmail || "Dashboard")}
          </h1>
          <p className="mt-1 text-base font-medium text-foreground/55">
            {listingCountText} · pricing analytics
          </p>
        </div>

        {error && (
          <div className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-5 py-4">
            <p className="text-sm text-rose-700">{error}</p>
          </div>
        )}

        {/* ════════════════════════════════════════
            Two-column layout: 280px sidebar + main
        ════════════════════════════════════════ */}
        <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[280px_1fr]">

          {/* ── Left: Saved Listings rail ── */}
          <section>
            <div className="mb-2.5 flex items-center justify-between px-0.5">
              <p className="text-xs font-semibold uppercase tracking-widest text-foreground/35">
                Saved Listings
              </p>
              {listings.length > 0 && (
                <span className="rounded-full bg-gray-200/80 px-2 py-px text-xs font-semibold text-foreground/45">
                  {listings.length}
                </span>
              )}
            </div>

            {listings.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-border bg-white px-6 py-10 text-center">
                <p className="text-base font-medium text-foreground/50">No listings yet.</p>
                <p className="mt-1 text-sm text-foreground/35">
                  Add a listing to start tracking pricing.
                </p>
                <Link href="/tool?from=dashboard" className="mt-5 inline-block">
                  <Button size="sm">Add listing</Button>
                </Link>
              </div>
            ) : (
              <div className="space-y-2">
                {listings.map((listing) => (
                  <ListingCard
                    key={listing.id}
                    listing={listing}
                    isActive={listing.id === activeListingId}
                    onSelect={() => setActiveListingId(listing.id)}
                    onDelete={() => handleDelete(listing.id)}
                    onViewHistory={() =>
                      router.push(`/dashboard/listings/${listing.id}`)
                    }
                    onRename={handleRenameListing}
                    onSavePreferredComps={handleSavePreferredComps}
                    onSaveAlertSettings={handleSaveAlertSettings}
                  />
                ))}
                <Link href="/tool?from=dashboard" className="block">
                  <button
                    type="button"
                    className="w-full rounded-2xl border border-dashed border-gray-200 py-3 text-sm font-medium text-foreground/35 transition-colors hover:border-gray-300 hover:text-foreground/55"
                  >
                    + Add new listing
                  </button>
                </Link>
              </div>
            )}
          </section>

          {/* ── Right: Market analysis panel ── */}
          <section>
            {activeListing && activeSummary && activeReport ? (
              <div className="space-y-4">

                {/* ── Panel header ── */}
                <div className="flex items-center justify-between px-0.5">
                  <div className="flex items-center gap-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-foreground/35">
                      Nightly Market Report
                    </p>
                    <span className="rounded-full bg-teal-50 px-2 py-0.5 text-[10px] font-semibold text-teal-700">
                      Nightly
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowCustomPanel((v) => !v)}
                    className={`text-sm font-semibold transition-colors ${
                      showCustomPanel
                        ? "text-foreground/55"
                        : "text-blue-600 hover:text-blue-700"
                    }`}
                  >
                    {showCustomPanel ? "Cancel" : "Run custom analysis ↗"}
                  </button>
                </div>

                {/* ── Custom analysis panel ── */}
                {showCustomPanel && (
                  <div className="rounded-2xl border border-blue-100 bg-blue-50/40 p-5">
                    <p className="mb-0.5 text-sm font-semibold text-foreground/80">
                      Custom Live Analysis
                    </p>
                    <p className="mb-4 text-xs text-foreground/50">
                      Scrapes fresh Airbnb market data for any date range. You&apos;ll be taken to the full report.
                    </p>
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                      <label className="flex-1 space-y-1.5">
                        <span className="block text-xs font-medium text-foreground/50">Start date</span>
                        <input
                          type="date"
                          value={customStart}
                          min={todayStr}
                          onChange={(e) => setCustomStart(e.target.value)}
                          className="w-full rounded-lg border border-blue-200/80 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400"
                        />
                      </label>
                      <label className="flex-1 space-y-1.5">
                        <span className="block text-xs font-medium text-foreground/50">End date</span>
                        <input
                          type="date"
                          value={customEnd}
                          min={customStart || todayStr}
                          onChange={(e) => setCustomEnd(e.target.value)}
                          className="w-full rounded-lg border border-blue-200/80 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400"
                        />
                      </label>
                      <button
                        type="button"
                        disabled={!customStart || !customEnd || customStart < todayStr || rerunningId === activeListing.id}
                        onClick={() => {
                          void handleRunAnalysis(activeListing.id, {
                            startDate: customStart,
                            endDate: customEnd,
                          });
                        }}
                        className="shrink-0 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-700 disabled:opacity-40"
                      >
                        {rerunningId === activeListing.id ? "Starting…" : "Run analysis"}
                      </button>
                    </div>
                  </div>
                )}

                {/* ── State banners ── */}
                {/* Nightly in-progress (separate from activeJob so it shows even when old data is displayed) */}
                {activeListing.activeNightlyJob && activeListing.activeNightlyJob.status !== "error" && (
                  <div className="flex items-center gap-3 rounded-xl border border-teal-200 bg-teal-50/70 px-4 py-3 text-xs font-medium text-teal-700">
                    <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-teal-500" />
                    Nightly 30-day market report generating — pricing below is from your last run.
                  </div>
                )}
                {activeListing.activeNightlyJob?.status === "error" && (
                  <div className="flex items-center gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs font-medium text-rose-700">
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
                    Last nightly report failed — showing previous data.
                  </div>
                )}
                {/* Non-nightly active job (manual rerun running/errored) */}
                {activeListing.activeJob && !activeListing.activeNightlyJob && (
                  <div
                    className={`flex items-center gap-3 rounded-xl border px-4 py-3 text-xs font-medium ${
                      activeListing.activeJob.status === "error"
                        ? "border-rose-200 bg-rose-50 text-rose-700"
                        : "border-amber-200 bg-amber-50 text-amber-700"
                    }`}
                  >
                    {activeListing.activeJob.status === "error" ? (
                      <>
                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
                        Last analysis failed — showing pricing from your previous completed run.
                      </>
                    ) : (
                      <>
                        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-500" />
                        New analysis running — pricing below is from your last completed run.
                      </>
                    )}
                  </div>
                )}
                {/* ── Price summary ── */}
                <RecommendationBanner
                  listingName={activeListing.name}
                  airbnbListingLabel={activeAirbnbListingLabel}
                  summary={activeSummary}
                  recommendedPrice={activeSummary.recommendedPrice ?? null}
                  reportShareId={activeReport.share_id}
                  propertyMeta={{
                    propertyType: activeListing.input_attributes.propertyType,
                    guests: activeListing.input_attributes.maxGuests,
                    beds:
                      activeListing.input_attributes.beds ??
                      activeListing.input_attributes.bedrooms,
                    baths: activeListing.input_attributes.bathrooms,
                  }}
                  benchmarkMeta={{
                    count:
                      activeListing.input_attributes.preferredComps?.filter(
                        (c) => c.enabled !== false && c.listingUrl
                      ).length ?? 0,
                    primaryUrl:
                      activeListing.input_attributes.preferredComps?.find(
                        (c) => c.enabled !== false && c.listingUrl
                      )?.listingUrl ?? null,
                    primaryName:
                      activeListing.input_attributes.preferredComps?.find(
                        (c) => c.enabled !== false && c.listingUrl
                      )?.name ?? null,
                  }}
                  onManageBenchmarks={() => setBenchmarkModalListingId(activeListing.id)}
                  lastAnalysisDate={activeListing.latestLinkedAt}
                />

                {/* ── Smart alerts ── */}
                <SmartAlerts
                  summary={activeSummary}
                  compsSummary={activeSummary.compsSummary ?? null}
                  priceDistribution={activeSummary.priceDistribution ?? null}
                  observedListingPrice={activeSummary.observedListingPrice ?? null}
                />

                {/* ── Price line chart ── */}
                {activeCalendar.length > 1 && (
                  <PriceLineChart
                    calendar={activeCalendar}
                    pricingMode={pricingMode}
                  />
                )}

                {/* ── 30-day pricing calendar ── */}
                {activeCalendar.length > 0 && (
                  <PricingHeatmap
                    calendar={activeCalendar}
                    pricingMode={pricingMode}
                    onModeChange={setPricingMode}
                  />
                )}

                {/* ── Market basis / freshness ── */}
                <ForecastBasis
                  marketCapturedAt={resolveMarketCapturedAt(activeReport, activeListing.latestLinkedAt)}
                  dateStart={activeReport.input_date_start}
                  dateEnd={activeReport.input_date_end}
                  reportType={activeReport.report_type}
                  trigger={activeListing.latestTrigger ?? undefined}
                  shareId={activeReport.share_id}
                  compsUsed={activeSummary.compsSummary?.usedForPricing ?? null}
                />
              </div>
            ) : activeListing ? (
              /* Listing selected but no nightly ready report */
              <div className="space-y-4">
                {/* Panel header */}
                <div className="px-0.5">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-foreground/35">
                    Nightly Market Report
                  </p>
                </div>

                {activeListing.activeNightlyJob?.status === "running" ||
                activeListing.activeNightlyJob?.status === "queued" ? (
                  <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-white px-8 py-14 text-center">
                    <span className="mb-3 inline-block h-2 w-2 animate-pulse rounded-full bg-teal-400" />
                    <p className="text-sm font-semibold text-foreground/60">Nightly report generating</p>
                    <p className="mt-1 text-sm text-foreground/40">
                      Your 30-day market board will appear here once complete.
                    </p>
                    {activeListing.activeNightlyJob.shareId && (
                      <Link
                        href={`/r/${activeListing.activeNightlyJob.shareId}`}
                        className="mt-3 text-xs font-medium text-accent hover:underline"
                      >
                        View live progress →
                      </Link>
                    )}
                  </div>
                ) : (
                  /* No nightly report yet — explain board semantics, offer custom analysis */
                  <div className="rounded-2xl border border-dashed border-border bg-white p-8">
                    <p className="text-sm font-semibold text-foreground/60">
                      No nightly report yet for {activeListing.name}
                    </p>
                    <p className="mt-1 text-sm text-foreground/40">
                      This board updates nightly. Custom analyses are saved to history and don&apos;t replace the board.
                    </p>
                    <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-end">
                      <label className="flex-1 space-y-1.5">
                        <span className="block text-xs font-medium text-foreground/50">Start date</span>
                        <input
                          type="date"
                          value={customStart}
                          min={todayStr}
                          onChange={(e) => setCustomStart(e.target.value)}
                          className="w-full rounded-lg border border-gray-200 bg-gray-50/60 px-3 py-2 text-sm outline-none focus:border-gray-300 focus:bg-white"
                        />
                      </label>
                      <label className="flex-1 space-y-1.5">
                        <span className="block text-xs font-medium text-foreground/50">End date</span>
                        <input
                          type="date"
                          value={customEnd}
                          min={customStart || todayStr}
                          onChange={(e) => setCustomEnd(e.target.value)}
                          className="w-full rounded-lg border border-gray-200 bg-gray-50/60 px-3 py-2 text-sm outline-none focus:border-gray-300 focus:bg-white"
                        />
                      </label>
                      <button
                        type="button"
                        disabled={!customStart || !customEnd || customStart < todayStr || rerunningId === activeListing.id}
                        onClick={() => {
                          void handleRunAnalysis(activeListing.id, {
                            startDate: customStart,
                            endDate: customEnd,
                          });
                        }}
                        className="shrink-0 rounded-xl bg-gray-900 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
                      >
                        {rerunningId === activeListing.id ? "Starting…" : "Run analysis"}
                      </button>
                    </div>
                    {activeListing.activeNightlyJob?.status === "error" && (
                      <p className="mt-3 text-xs text-rose-600">
                        Last nightly report failed. It will retry on the next scheduled run.
                      </p>
                    )}
                  </div>
                )}
              </div>
            ) : (
              /* Nothing selected */
              <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-white px-8 py-16 text-center">
                <p className="text-sm text-foreground/35">
                  Select a listing to view its market analysis
                </p>
              </div>
            )}
          </section>
        </div>

        {/* ── Benchmark management modal ── */}
        {benchmarkModalListingId !== null && (() => {
          const bListing = listings.find((l) => l.id === benchmarkModalListingId);
          if (!bListing) return null;
          return (
            <BenchmarkModal
              listing={{ id: bListing.id, name: bListing.name }}
              initialComps={
                (bListing.input_attributes.preferredComps ?? []).filter(
                  (c) => c.enabled !== false && c.listingUrl
                )
              }
              onClose={() => setBenchmarkModalListingId(null)}
              onSave={async (comps) => {
                await handleSavePreferredComps(
                  bListing.id,
                  comps.length > 0
                    ? comps.map((c) => ({ ...c, enabled: true }))
                    : null
                );
                // Modal calls onClose() after onSave resolves, which sets
                // benchmarkModalListingId to null via the onClose prop above.
              }}
            />
          );
        })()}

        {/* ════════════════════════════════════════
            Recent Reports — full width below
        ════════════════════════════════════════ */}
        {recentReports.length > 0 && (
          <section className="mt-6">
            <p className="mb-2.5 px-0.5 text-[11px] font-semibold uppercase tracking-widest text-foreground/35">
              Recent Live Analysis Reports
            </p>
            <div className="overflow-hidden rounded-2xl border border-border bg-white shadow-sm divide-y divide-border">
              {recentReports.slice(0, 5).map((item) => (
                <Link
                  key={`${item.listingId}-${item.report.id}`}
                  href={`/r/${item.report.share_id}`}
                  className="flex items-center justify-between px-5 py-3.5 transition-colors hover:bg-gray-50 first:rounded-t-2xl last:rounded-b-2xl"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">
                      {item.listingName}
                    </p>
                    <div className="mt-0.5 flex items-center gap-1.5">
                      <span className={`rounded-full px-1.5 py-px text-[10px] font-semibold ${
                        item.trigger === "scheduled"
                          ? "bg-teal-50 text-teal-700"
                          : item.trigger === "manual"
                          ? "bg-blue-50 text-blue-600"
                          : "bg-gray-100 text-gray-500"
                      }`}>
                        {item.trigger === "scheduled" ? "Nightly" : item.trigger === "manual" ? "Custom" : "Rerun"}
                      </span>
                      <span className="text-xs text-foreground/35">
                        {new Date(item.linkedAt).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                  <div className="ml-4 flex shrink-0 items-center gap-3">
                    {(() => {
                      const s = item.report.result_summary;
                      const price = s?.recommendedPrice?.nightly ?? s?.nightlyMedian;
                      return price != null ? (
                        <span className="text-sm font-semibold text-foreground">
                          ${price}
                          <span className="ml-0.5 text-xs font-normal text-foreground/35">/night</span>
                        </span>
                      ) : (
                        <span className="text-xs text-foreground/40 capitalize">
                          {item.report.status}
                        </span>
                      );
                    })()}
                    <span className="text-xs font-medium text-foreground/40">View →</span>
                  </div>
                </Link>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
