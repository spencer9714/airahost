"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
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
    preferredComps?: Array<{ listingUrl: string; note?: string; enabled?: boolean }> | null;
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
    preferredComps: Array<{ listingUrl: string; note?: string; enabled?: boolean }> | null
  ) {
    const res = await fetch(`/api/listings/${listingId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferredComps }),
    });
    if (!res.ok) {
      throw new Error("Failed to save benchmark listings");
    }
    await loadDashboardData();
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
      <div className="flex min-h-[60vh] items-center justify-center">
        <p className="text-sm text-foreground/40">Loading dashboard…</p>
      </div>
    );
  }

  const firstName = userName ? userName.split(" ")[0] : null;

  return (
    <div className="min-h-screen bg-gray-50/50">
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12">

        {/* ── Header ── */}
        <div className="mb-10 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-widest text-foreground/35">
              Host Dashboard
            </p>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
              {firstName ? `Welcome back, ${firstName}` : (userEmail || "Dashboard")}
            </h1>
            <p className="mt-0.5 text-sm text-foreground/50">
              {listingCountText} saved · pricing analytics
            </p>
          </div>
          <Link href="/tool?from=dashboard">
            <Button size="md">+ New analysis</Button>
          </Link>
        </div>

        {error && (
          <div className="mb-8 rounded-xl border border-rose-200 bg-rose-50 px-5 py-4">
            <p className="text-sm text-rose-700">{error}</p>
          </div>
        )}

        {/* ════════════════════════════════════════
            Section 1 — Saved Listings
        ════════════════════════════════════════ */}
        <section className="mb-10">
          <div className="mb-4 flex items-center gap-2.5">
            <p className="text-xs font-semibold uppercase tracking-widest text-foreground/35">
              Saved Listings
            </p>
            {listings.length > 0 && (
              <span className="inline-flex items-center rounded-full bg-gray-200/70 px-2 py-0.5 text-[11px] font-semibold text-foreground/50">
                {listings.length}
              </span>
            )}
          </div>

          {listings.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-border bg-white px-8 py-10 text-center">
              <p className="text-sm font-medium text-foreground/50">
                No saved listings yet.
              </p>
              <p className="mt-1 text-sm text-foreground/40">
                Run your first analysis to start tracking pricing.
              </p>
              <Link href="/tool?from=dashboard" className="mt-5 inline-block">
                <Button size="sm">Run analysis</Button>
              </Link>
            </div>
          ) : (
            <div className="overflow-hidden rounded-2xl border border-border bg-white divide-y divide-border shadow-sm">
              {listings.map((listing) => (
                <ListingCard
                  key={listing.id}
                  listing={listing}
                  isActive={listing.id === activeListingId}
                  onSelect={() => setActiveListingId(listing.id)}
                  onRunAnalysis={handleRunAnalysis}
                  onDelete={() => handleDelete(listing.id)}
                  onViewHistory={() =>
                    router.push(`/dashboard/listings/${listing.id}`)
                  }
                  isRunning={rerunningId === listing.id}
                  onRename={handleRenameListing}
                  onSaveDateDefaults={handleSaveDateDefaults}
                  onSavePreferredComps={handleSavePreferredComps}
                />
              ))}
            </div>
          )}
        </section>

        {/* ════════════════════════════════════════
            Section 2 — Pricing Insights
        ════════════════════════════════════════ */}
        {activeListing && activeSummary && activeReport && (
          <section className="mb-10">
            <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-widest text-foreground/35">
                  Pricing Insights
                </p>
                <h2 className="mt-0.5 text-base font-semibold text-foreground">
                  {activeListing.name}
                </h2>
              </div>
              {readyListings.length > 1 && (
                <select
                  value={activeListing.id}
                  onChange={(e) => handleListingSelect(e.target.value)}
                  className="rounded-lg border border-border bg-white px-3 py-2 text-sm font-medium text-foreground outline-none focus:border-accent"
                >
                  {readyListings.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
              )}
            </div>

            <div className="space-y-4">
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
            benchmarkMeta={{
              count:
                activeListing.input_attributes.preferredComps?.filter(
                  (comp) => comp.enabled !== false && comp.listingUrl
                ).length ?? 0,
              primaryUrl:
                activeListing.input_attributes.preferredComps?.find(
                  (comp) => comp.enabled !== false && comp.listingUrl
                )?.listingUrl ?? null,
            }}
            lastAnalysisDate={activeListing.latestLinkedAt}
          />

              {activeCalendar.length > 0 && (
                <PricingHeatmap
                  calendar={activeCalendar}
                  pricingMode={pricingMode}
                  onModeChange={setPricingMode}
                />
              )}

              <div>
                <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-foreground/35">
                  Alerts
                </p>
                <SmartAlerts
                  summary={activeSummary}
                  compsSummary={activeSummary.compsSummary ?? null}
                  priceDistribution={activeSummary.priceDistribution ?? null}
                />
              </div>
            </div>
          </section>
        )}

        {/* ════════════════════════════════════════
            Section 3 — Recent Reports
        ════════════════════════════════════════ */}
        <section>
          <p className="mb-4 text-xs font-semibold uppercase tracking-widest text-foreground/35">
            Recent Reports
          </p>
          {recentReports.length === 0 ? (
            <div className="rounded-2xl border border-border bg-white px-6 py-5">
              <p className="text-sm text-foreground/40">No reports yet.</p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-2xl border border-border bg-white divide-y divide-border shadow-sm">
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
                    <p className="text-xs text-foreground/40">
                      {new Date(item.linkedAt).toLocaleDateString()}
                    </p>
                  </div>
                  <span className="ml-4 shrink-0 text-sm font-semibold text-foreground">
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
    </div>
  );
}
