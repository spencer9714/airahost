"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { getSupabaseBrowser } from "@/lib/supabase";
import { RecommendationBanner } from "@/components/dashboard/RecommendationBanner";
import { PricingHeatmap } from "@/components/dashboard/PricingHeatmap";
import { SmartAlerts } from "@/components/dashboard/SmartAlerts";
import { ListingCard } from "@/components/dashboard/ListingCard";
import type {
  PropertyType,
  CalendarDay,
  ReportSummary,
  RecommendedPrice,
  CompsSummary,
  PriceDistribution,
  DateMode,
} from "@/lib/schemas";

type LatestReport = {
  id: string;
  share_id: string;
  status: "queued" | "running" | "ready" | "error";
  created_at: string;
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
  } | null;
  result_calendar?: CalendarDay[];
} | null;

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
  };
  created_at: string;
  last_used_at: string | null;
  default_date_mode?: DateMode;
  default_start_date?: string | null;
  default_end_date?: string | null;
  latestReport: LatestReport;
  latestLinkedAt: string | null;
};

type RecentReportRow = {
  listingId: string;
  listingName: string;
  linkedAt: string;
  trigger: "manual" | "rerun" | "scheduled";
  report: NonNullable<LatestReport>;
};

type ListingDetailResponse = {
  listing: ListingRow;
  reports: Array<{
    id: string;
    trigger: string;
    created_at: string;
    pricing_reports: {
      share_id: string;
      status: string;
      result_summary: { nightlyMedian?: number } | null;
    } | null;
  }>;
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
  const [expandedListingId, setExpandedListingId] = useState<string | null>(
    null
  );
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyRows, setHistoryRows] = useState<
    ListingDetailResponse["reports"]
  >([]);
  const [activeListingId, setActiveListingId] = useState<string | null>(null);
  const [pricingMode, setPricingMode] = useState<
    "refundable" | "nonRefundable"
  >("refundable");


  const loadDashboardData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/listings");
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        throw new Error("Failed to load dashboard data");
      }
      const data = await res.json();
      const loadedListings = (data.listings ?? []) as ListingRow[];
      setListings(loadedListings);
      setRecentReports((data.recentReports ?? []) as RecentReportRow[]);
      // Auto-select only once (or after delete reset): prefer most recently analyzed listing.
      setActiveListingId((prev) => {
        if (prev || loadedListings.length === 0) return prev;
        const withReports = loadedListings
          .filter(
            (l) =>
              l.latestReport?.status === "ready" && l.latestReport.result_summary
          )
          .sort((a, b) => {
            const aDate = a.latestLinkedAt ?? "";
            const bDate = b.latestLinkedAt ?? "";
            return bDate.localeCompare(aDate);
          });
        return withReports[0]?.id ?? loadedListings[0].id;
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

  async function loadListingHistory(listingId: string) {
    if (expandedListingId === listingId) {
      setExpandedListingId(null);
      setHistoryRows([]);
      return;
    }
    setExpandedListingId(listingId);
    setHistoryLoading(true);
    try {
      const res = await fetch(`/api/listings/${listingId}`);
      if (!res.ok) throw new Error("Failed to load listing details");
      const data = (await res.json()) as ListingDetailResponse;
      setHistoryRows(data.reports ?? []);
    } catch {
      setHistoryRows([]);
    } finally {
      setHistoryLoading(false);
    }
  }

  async function handleRunAnalysis(
    listingId: string,
    dates: { startDate: string; endDate: string }
  ) {
    setRerunningId(listingId);
    try {
      const res = await fetch("/api/reports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          listingId,
          dateRange: dates,
        }),
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

  function handleSaveDateDefaults(
    listingId: string,
    mode: DateMode,
    startDate: string | null,
    endDate: string | null
  ) {
    // Fire-and-forget PATCH — debounced by the ListingCard
    void fetch(`/api/listings/${listingId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        defaultDateMode: mode,
        defaultStartDate: startDate,
        defaultEndDate: endDate,
      }),
    });
  }

  async function handleDelete(listingId: string) {
    const res = await fetch(`/api/listings/${listingId}`, {
      method: "DELETE",
    });
    if (res.ok) {
      if (activeListingId === listingId) setActiveListingId(null);
      if (expandedListingId === listingId) {
        setExpandedListingId(null);
        setHistoryRows([]);
      }
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

  // ── Derived state ──────────────────────────────────────────────
  const activeListing = useMemo(
    () => listings.find((l) => l.id === activeListingId) ?? null,
    [listings, activeListingId]
  );

  const activeReport = activeListing?.latestReport;
  const activeSummary: ReportSummary | null = useMemo(() => {
    const s = activeReport?.result_summary;
    if (!s || activeReport?.status !== "ready") return null;
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
    };
  }, [activeReport]);

  const activeCalendar = activeReport?.result_calendar ?? [];

  const listingCountText = useMemo(
    () => `${listings.length} listing${listings.length === 1 ? "" : "s"}`,
    [listings.length]
  );

  const readyListings = useMemo(
    () =>
      listings
        .filter(
          (l) =>
            l.latestReport?.status === "ready" && l.latestReport.result_summary
        )
        .map((l) => ({ id: l.id, name: l.name })),
    [listings]
  );

  function handleListingSelect(id: string) {
    setActiveListingId(id);
  }

  if (!authReady || loading) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-base text-foreground/60">Loading dashboard...</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-10 px-6 py-10">
      {/* Header */}
      <section className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium uppercase tracking-wide text-foreground/50">
            Dashboard
          </p>
          <h1 className="text-3xl font-bold tracking-tight">
            Welcome back, {userName || userEmail}
          </h1>
          <p className="mt-1 text-base text-foreground/60">
            Track your listings and optimize pricing. You currently manage{" "}
            {listingCountText}.
          </p>
        </div>
        <Link href="/tool?from=dashboard">
          <Button size="md">New analysis</Button>
        </Link>
      </section>

      {error && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-6 py-4">
          <p className="text-base text-rose-800">{error}</p>
        </div>
      )}

      {/* ═══ Section A: Today's Recommendation ═══ */}
      {activeListing && activeSummary && activeReport && (
        <section className="space-y-6">
          {/* Title row + listing selector */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="text-2xl font-bold tracking-tight">
              Today&apos;s recommendation
            </h2>
            {readyListings.length > 1 && (
              <select
                value={activeListing.id}
                onChange={(e) => handleListingSelect(e.target.value)}
                className="min-w-0 max-w-xs rounded-xl border border-border bg-white px-4 py-2.5 text-base font-semibold text-foreground outline-none focus:border-accent"
              >
                {readyListings.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Recommendation card */}
          <RecommendationBanner
            listingName={activeListing.name}
            summary={activeSummary}
            recommendedPrice={activeSummary.recommendedPrice ?? null}
            reportShareId={activeReport.share_id}
            onRerun={() => {
              const today = new Date().toISOString().split("T")[0];
              const end = new Date();
              end.setDate(end.getDate() + 30);
              void handleRunAnalysis(activeListing.id, {
                startDate: today,
                endDate: end.toISOString().split("T")[0],
              });
            }}
            isRerunning={rerunningId === activeListing.id}
            propertyMeta={{
              propertyType: activeListing.input_attributes.propertyType,
              guests: activeListing.input_attributes.maxGuests,
              beds:
                activeListing.input_attributes.beds ??
                activeListing.input_attributes.bedrooms,
              baths: activeListing.input_attributes.bathrooms,
            }}
            lastAnalysisDate={activeListing.latestLinkedAt}
          />

          {/* Pricing Heatmap */}
          {activeCalendar.length > 0 && (
            <PricingHeatmap
              calendar={activeCalendar}
              pricingMode={pricingMode}
              onModeChange={setPricingMode}
            />
          )}

          {/* Smart Alerts */}
          {activeSummary && (
            <div>
              <h3 className="mb-4 text-xl font-bold tracking-tight">Alerts</h3>
              <SmartAlerts
                summary={activeSummary}
                compsSummary={activeSummary.compsSummary ?? null}
                priceDistribution={activeSummary.priceDistribution ?? null}
              />
            </div>
          )}
        </section>
      )}

      {/* ═══ Section B: Saved Listings ═══ */}
      <section className="space-y-4">
        <div className="flex items-center gap-3">
          <h2 className="text-2xl font-bold tracking-tight">Saved Listings</h2>
          {listings.length > 0 && (
            <span className="rounded-full bg-gray-100 px-3 py-1 text-sm font-semibold text-foreground/60">
              {listings.length}
            </span>
          )}
        </div>
        {listings.length === 0 ? (
          <Card className="text-center">
            <p className="text-base text-foreground/60">
              No saved listings yet. Add your first listing to start tracking
              pricing performance.
            </p>
          </Card>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-border bg-white">
            <div className="divide-y divide-border">
              {listings.map((listing) => (
                <ListingCard
                  key={listing.id}
                  listing={listing}
                  isActive={listing.id === activeListingId}
                  onSelect={() => setActiveListingId(listing.id)}
                  onRunAnalysis={handleRunAnalysis}
                  onDelete={() => handleDelete(listing.id)}
                  onViewDetails={() => loadListingHistory(listing.id)}
                  onViewHistory={() =>
                    router.push(`/dashboard/listings/${listing.id}`)
                  }
                  isRunning={rerunningId === listing.id}
                  isExpanded={expandedListingId === listing.id}
                  historyLoading={historyLoading}
                  historyRows={historyRows}
                  onRename={handleRenameListing}
                  onSaveDateDefaults={handleSaveDateDefaults}
                />
              ))}
            </div>
          </div>
        )}
      </section>

      {/* Recent Reports */}
      <section className="space-y-4">
        <h2 className="text-2xl font-bold tracking-tight">Recent Reports</h2>
        {recentReports.length === 0 ? (
          <div className="rounded-2xl border border-border bg-white px-6 py-5">
            <p className="text-base text-foreground/60">No reports yet.</p>
          </div>
        ) : (
          <div className="rounded-2xl border border-border bg-white divide-y divide-border">
            {recentReports.slice(0, 5).map((item) => (
              <Link
                key={`${item.listingId}-${item.report.id}`}
                href={`/r/${item.report.share_id}`}
                className="flex items-center justify-between px-6 py-4 text-base hover:bg-gray-50 transition-colors first:rounded-t-2xl last:rounded-b-2xl"
              >
                <span className="text-foreground/70">
                  {new Date(item.linkedAt).toLocaleDateString()} –{" "}
                  {item.listingName}
                </span>
                <span className="font-semibold">
                  {item.report.result_summary?.nightlyMedian
                    ? `$${item.report.result_summary.nightlyMedian}/night`
                    : item.report.status}
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
