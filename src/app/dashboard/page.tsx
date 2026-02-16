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

function getDefaultDates() {
  const start = new Date();
  start.setDate(start.getDate() + 7);
  const end = new Date(start);
  end.setDate(end.getDate() + 7);
  return {
    startDate: start.toISOString().split("T")[0],
    endDate: end.toISOString().split("T")[0],
  };
}

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
      // Auto-select first listing with a ready report
      if (!activeListingId && loadedListings.length > 0) {
        const withReport = loadedListings.find(
          (l) =>
            l.latestReport?.status === "ready" && l.latestReport.result_summary
        );
        setActiveListingId(withReport?.id ?? loadedListings[0].id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, [router, activeListingId]);

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

  async function handleRerun(listingId: string) {
    setRerunningId(listingId);
    const dates = getDefaultDates();
    try {
      const res = await fetch(`/api/listings/${listingId}/rerun`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dates }),
      });
      if (!res.ok) throw new Error("Failed to rerun analysis");
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

  if (!authReady || loading) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-sm text-muted">Loading dashboard...</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8 px-6 py-10">
      {/* Header */}
      <section className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm text-muted">Dashboard</p>
          <h1 className="text-3xl font-bold">
            Welcome back, {userName || userEmail}
          </h1>
          <p className="mt-1 text-sm text-muted">
            Track your listings and optimize pricing. You currently manage{" "}
            {listingCountText}.
          </p>
        </div>
        <Link href="/tool?from=dashboard">
          <Button size="sm">New analysis</Button>
        </Link>
      </section>

      {error && (
        <Card>
          <p className="text-sm text-warning">{error}</p>
        </Card>
      )}

      {/* Recommendation Banner */}
      {activeListing && activeSummary && activeReport && (
        <RecommendationBanner
          listingName={activeListing.name}
          summary={activeSummary}
          recommendedPrice={activeSummary.recommendedPrice ?? null}
          reportShareId={activeReport.share_id}
          onRerun={() => handleRerun(activeListing.id)}
          isRerunning={rerunningId === activeListing.id}
        />
      )}

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
        <section>
          <h2 className="mb-3 text-lg font-semibold">Alerts</h2>
          <SmartAlerts
            summary={activeSummary}
            compsSummary={activeSummary.compsSummary ?? null}
            priceDistribution={activeSummary.priceDistribution ?? null}
          />
        </section>
      )}

      {/* Listings */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Saved Listings</h2>
        {listings.length === 0 ? (
          <Card className="text-center">
            <p className="text-sm text-muted">
              No saved listings yet. Add your first listing to start tracking
              pricing performance.
            </p>
          </Card>
        ) : (
          <div className="grid gap-4">
            {listings.map((listing) => (
              <ListingCard
                key={listing.id}
                listing={listing}
                isActive={listing.id === activeListingId}
                onSelect={() => setActiveListingId(listing.id)}
                onRerun={() => handleRerun(listing.id)}
                onDelete={() => handleDelete(listing.id)}
                onViewDetails={() => loadListingHistory(listing.id)}
                isRerunning={rerunningId === listing.id}
                isExpanded={expandedListingId === listing.id}
                historyLoading={historyLoading}
                historyRows={historyRows}
              />
            ))}
          </div>
        )}
      </section>

      {/* Recent Reports */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Recent Reports</h2>
        <Card>
          {recentReports.length === 0 ? (
            <p className="text-sm text-muted">No reports yet.</p>
          ) : (
            <div className="space-y-2">
              {recentReports.slice(0, 5).map((item) => (
                <Link
                  key={`${item.listingId}-${item.report.id}`}
                  href={`/r/${item.report.share_id}`}
                  className="flex items-center justify-between rounded-xl border border-border px-3 py-2 text-sm hover:bg-gray-50"
                >
                  <span>
                    {new Date(item.linkedAt).toLocaleDateString()} -{" "}
                    {item.listingName}
                  </span>
                  <span className="font-medium">
                    {item.report.result_summary?.nightlyMedian
                      ? `$${item.report.result_summary.nightlyMedian}/night`
                      : item.report.status}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}
