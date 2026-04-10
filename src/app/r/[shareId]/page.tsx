"use client";

import { useEffect, useState, useCallback, useRef, useMemo, use } from "react";
import Link from "next/link";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { HowWeEstimated } from "@/components/report/HowWeEstimated";
import { ComparableListingsSection } from "@/components/report/ComparableListingsSection";
import { PricingHeatmap } from "@/components/dashboard/PricingHeatmap";
import { getSupabaseBrowser } from "@/lib/supabase";
import type {
  PricingReport,
  TargetSpec,
  QueryCriteria,
  CompsSummary,
  ComparableListing,
  BenchmarkInfo,
} from "@/lib/schemas";
import { generatePricingReport } from "@/core/pricingCore";

// Demo report — realistic property, dynamic date range (next 30 days).
function getDemoReport(): PricingReport {
  const today = new Date();
  const startDate = today.toISOString().split("T")[0];
  const end = new Date(today);
  end.setDate(today.getDate() + 30);
  const endDate = end.toISOString().split("T")[0];

  // Four sampled scrape dates spread across the 30-day window.
  const sampleDates = [0, 7, 14, 21].map((offset) => {
    const d = new Date(today);
    d.setDate(today.getDate() + offset);
    return d.toISOString().split("T")[0];
  });
  const [d0, d7, d14, d21] = sampleDates;

  const demoAddress = "2847 Hillcrest Drive, Santa Barbara, CA";
  const demoPolicy = {
    weeklyDiscountPct: 10,
    monthlyDiscountPct: 20,
    refundable: true,
    nonRefundableDiscountPct: 10,
    stackingMode: "compound" as const,
    maxTotalDiscountPct: 40,
  };

  const result = generatePricingReport({
    listing: {
      address: demoAddress,
      propertyType: "entire_home",
      bedrooms: 2,
      bathrooms: 1,
      maxGuests: 4,
      amenities: ["wifi", "kitchen", "washer", "free_parking", "pool"],
    },
    startDate,
    endDate,
    discountPolicy: demoPolicy,
  });

  // ── Transparency data ──────────────────────────────────────────

  const targetSpec: TargetSpec = {
    title: "2847 Hillcrest Drive",
    location: "Santa Barbara, CA",
    propertyType: "Entire home",
    accommodates: 4,
    bedrooms: 2,
    beds: 2,
    baths: 1,
    amenities: ["Wifi", "Kitchen", "Washer", "Free parking on premises", "Pool"],
    rating: null,
    reviews: null,
  };

  const queryCriteria: QueryCriteria = {
    locationBasis: "Santa Barbara, CA",
    searchAdults: 2,
    checkin: startDate,
    checkout: endDate,
    propertyTypeFilter: "entire_home",
    tolerances: { accommodates: 2, bedrooms: 1, beds: 2, baths: 1 },
  };

  const benchmarkInfo: BenchmarkInfo = {
    benchmarkUsed: true,
    benchmarkUrl: "https://www.airbnb.com/rooms/45892310",
    benchmarkFetchStatus: "search_hit",
    benchmarkFetchMethod: "search_result_card",
    avgBenchmarkPrice: 188,
    avgMarketPrice: 196,
    marketAdjustmentPct: 4.3,
    appliedMarketWeight: 0.35,
    effectiveMarketWeight: 0.29,
    maxAdjCap: 0.35,
    benchmarkTargetSimilarity: 0.93,
    benchmarkMismatchLevel: "high_match",
    outlierDays: 1,
    conflictDetected: false,
    fallbackReason: null,
    fetchStats: {
      searchHits: 28,
      directFetches: 2,
      failed: 0,
      totalDays: 30,
      highConfidenceDays: 24,
      mediumConfidenceDays: 4,
      lowConfidenceDays: 2,
    },
    secondaryComps: null,
    consensusSignal: "strong",
  };

  const comparableListings: ComparableListing[] = [
    {
      id: "comp-sb-1",
      title: "Charming 2BR Cottage Near State Street",
      propertyType: "Entire home",
      accommodates: 4,
      bedrooms: 2,
      baths: 1,
      nightlyPrice: 185,
      currency: "USD",
      similarity: 0.94,
      rating: 4.87,
      reviews: 142,
      location: "Santa Barbara, CA",
      url: null,
      queryNights: 1,
      usedInPricingDays: 28,
      priceByDate: { [d0]: 178, [d7]: 192, [d14]: 201, [d21]: 183 },
    },
    {
      id: "comp-sb-2",
      title: "Sunny 2-Bedroom Home — Walk to Beach",
      propertyType: "Entire home",
      accommodates: 4,
      bedrooms: 2,
      baths: 1,
      nightlyPrice: 209,
      currency: "USD",
      similarity: 0.91,
      rating: 4.92,
      reviews: 88,
      location: "Santa Barbara, CA",
      url: null,
      queryNights: 1,
      usedInPricingDays: 26,
      priceByDate: { [d0]: 199, [d7]: 215, [d14]: 222, [d21]: 204 },
    },
    {
      id: "comp-sb-3",
      title: "Modern Bungalow with Patio — Eastside SB",
      propertyType: "Entire home",
      accommodates: 3,
      bedrooms: 2,
      baths: 1,
      nightlyPrice: 172,
      currency: "USD",
      similarity: 0.88,
      rating: 4.78,
      reviews: 215,
      location: "Santa Barbara, CA",
      url: null,
      queryNights: 1,
      usedInPricingDays: 30,
      priceByDate: { [d0]: 165, [d7]: 175, [d14]: 182, [d21]: 170 },
    },
    {
      id: "comp-sb-4",
      title: "Bright 2BD with Private Garden",
      propertyType: "Entire home",
      accommodates: 4,
      bedrooms: 2,
      baths: 1,
      nightlyPrice: 193,
      currency: "USD",
      similarity: 0.87,
      rating: 4.83,
      reviews: 61,
      location: "Santa Barbara, CA",
      url: null,
      queryNights: 1,
      usedInPricingDays: 24,
      priceByDate: { [d0]: 186, [d7]: 198, [d14]: 207, [d21]: 190 },
    },
    {
      id: "comp-sb-5",
      title: "Cozy Craftsman Near Downtown & Funk Zone",
      propertyType: "Entire home",
      accommodates: 4,
      bedrooms: 2,
      baths: 1,
      nightlyPrice: 168,
      currency: "USD",
      similarity: 0.85,
      rating: 4.71,
      reviews: 307,
      location: "Santa Barbara, CA",
      url: null,
      queryNights: 1,
      usedInPricingDays: 29,
      priceByDate: { [d0]: 162, [d7]: 172, [d14]: 179, [d21]: 165 },
    },
    {
      id: "comp-sb-6",
      title: "Santa Barbara Retreat with Pool Access",
      propertyType: "Entire home",
      accommodates: 5,
      bedrooms: 2,
      baths: 2,
      nightlyPrice: 228,
      currency: "USD",
      similarity: 0.82,
      rating: 4.95,
      reviews: 43,
      location: "Santa Barbara, CA",
      url: null,
      queryNights: 1,
      usedInPricingDays: 22,
      priceByDate: { [d0]: 219, [d7]: 235, [d14]: 248, [d21]: 222 },
    },
    {
      id: "comp-sb-7",
      title: "Updated 2BR Rancho — Quiet Street",
      propertyType: "Entire home",
      accommodates: 4,
      bedrooms: 2,
      baths: 1,
      nightlyPrice: 176,
      currency: "USD",
      similarity: 0.80,
      rating: 4.68,
      reviews: 129,
      location: "Santa Barbara, CA",
      url: null,
      queryNights: 1,
      usedInPricingDays: 27,
      priceByDate: { [d0]: 169, [d7]: 180, [d14]: 188, [d21]: 174 },
    },
    {
      id: "comp-sb-8",
      title: "Stylish 2BR with Rooftop Deck & Views",
      propertyType: "Entire home",
      accommodates: 4,
      bedrooms: 2,
      baths: 1,
      nightlyPrice: 214,
      currency: "USD",
      similarity: 0.77,
      rating: 4.88,
      reviews: 76,
      location: "Santa Barbara, CA",
      url: null,
      queryNights: 1,
      usedInPricingDays: 20,
      priceByDate: { [d0]: 205, [d7]: 220, [d14]: 229, [d21]: 210 },
    },
  ];

  const compsSummary: CompsSummary = {
    collected: 24,
    afterFiltering: 12,
    usedForPricing: 8,
    filterStage: "strict",
    topSimilarity: 0.94,
    avgSimilarity: 0.86,
    sampledDays: 4,
    interpolatedDays: 26,
    missingDays: 0,
    belowSimilarityFloor: 4,
    filterFloor: 0.65,
    lowCompConfidenceDays: 0,
  };

  return {
    id: "demo",
    shareId: "demo",
    createdAt: new Date().toISOString(),
    status: "ready",
    coreVersion: result.coreVersion,
    inputAddress: demoAddress,
    inputAttributes: {
      address: demoAddress,
      propertyType: "entire_home",
      bedrooms: 2,
      bathrooms: 1,
      maxGuests: 4,
      amenities: ["wifi", "kitchen", "washer", "free_parking", "pool"],
      lastMinuteStrategy: {
        mode: "auto",
        aggressiveness: 50,
        floor: 0.65,
        cap: 1.05,
      },
    },
    inputDateStart: startDate,
    inputDateEnd: endDate,
    discountPolicy: demoPolicy,
    resultSummary: {
      ...result.summary,
      targetSpec,
      queryCriteria,
      benchmarkInfo,
      comparableListings,
      compsSummary,
    },
    resultCalendar: result.calendar,
    errorMessage: null,
  };
}

// Polling config
const POLL_INTERVAL_MS = 2_000;

// Staleness tiers based on worker_heartbeat_at age
const STALE_FRESH_MS = 45_000;    // < 45s  → fresh (normal spinner)
const STALE_SLOW_MS = 90_000;     // 45–90s → slow (mild warning)
const STALE_DELAYED_MS = 300_000; // 90–300s → delayed (strong warning)
// > 300s → unavailable

type StalenessTier = "fresh" | "slow" | "delayed" | "unavailable";

function getStaleness(workerHeartbeatAt: string | null | undefined): StalenessTier {
  if (!workerHeartbeatAt) return "fresh"; // no heartbeat yet = just started
  const ageMs = Date.now() - new Date(workerHeartbeatAt).getTime();
  if (ageMs < STALE_FRESH_MS) return "fresh";
  if (ageMs < STALE_SLOW_MS) return "slow";
  if (ageMs < STALE_DELAYED_MS) return "delayed";
  return "unavailable";
}

const STAGE_LABELS: Record<string, string> = {
  connecting: "Connecting to browser",
  extracting_target: "Extracting listing details",
  fetching_benchmark: "Fetching benchmark listing",
  searching_comps: "Searching comparable listings",
  pricing: "Computing pricing estimates",
  saving_results: "Saving results",
  completed: "Complete",
};

function getFriendlyReportError(message: string | null | undefined) {
  if (!message) {
    return "We couldn't generate your pricing report. Please try again.";
  }

  if (message.includes("Could not reach Airbnb data")) {
    return "We found Airbnb listings, but couldn't verify enough nightly prices for this report. Please try again in a moment.";
  }

  if (message.includes("Could not collect enough pricing data")) {
    return "We couldn't collect enough trustworthy nightly prices to build this report. Please try again in a moment.";
  }

  return message;
}

export default function ResultsPage({
  params,
}: {
  params: Promise<{ shareId: string }>;
}) {
  const { shareId } = use(params);
  const [report, setReport] = useState<PricingReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Polling state
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Auth state
  const [isSignedIn, setIsSignedIn] = useState<boolean | null>(null);

  // Save-to-dashboard state
  const [isReportSaved, setIsReportSaved] = useState<boolean | null>(null);
  const [saveLoading, setSaveLoading] = useState(false);
  const [saveError, setSaveError] = useState("");

  // Auto-Apply status for this listing (null = loading, false = not configured)
  const [autoApplyConfigured, setAutoApplyConfigured] = useState<boolean | null>(null);
  const reportListingId = (report as (typeof report & { listingId?: string | null }))?.listingId ?? null;

  // Date the user clicked on the heatmap (null = no selection).
  const [clickedDate, setClickedDate] = useState<string | null>(null);

  // Reset the clicked date whenever the report changes.
  const reportId = report?.id ?? null;
  useEffect(() => { setClickedDate(null); }, [reportId]);

  // Snap clickedDate to the nearest date that actually has comp price data.
  // comps are only sampled on a handful of dates across the 30-day window,
  // so without snapping most tile clicks would show "No data for this date".
  const snappedDate = useMemo((): string | null => {
    if (!clickedDate || !report) return clickedDate;
    const listings =
      report.comparableListings ?? report.resultSummary?.comparableListings ?? [];
    const sampledDates = new Set<string>();
    for (const listing of listings) {
      if ((listing as { priceByDate?: Record<string, number> }).priceByDate) {
        for (const d of Object.keys(
          (listing as { priceByDate: Record<string, number> }).priceByDate
        )) {
          sampledDates.add(d);
        }
      }
    }
    if (sampledDates.size === 0) return clickedDate; // no data at all — pass through
    const target = new Date(clickedDate + "T00:00:00Z").getTime();
    let best = clickedDate;
    let bestDiff = Infinity;
    for (const d of sampledDates) {
      const diff = Math.abs(new Date(d + "T00:00:00Z").getTime() - target);
      if (diff < bestDiff) { bestDiff = diff; best = d; }
    }
    return best;
  }, [clickedDate, report]);

  const fetchReport = useCallback(async () => {
    try {
      const res = await fetch(`/api/r/${shareId}`);
      if (!res.ok) throw new Error("Report not found");
      const data = await res.json();
      setReport(data);

      // Stop polling once in a terminal state
      if (data.status === "ready" || data.status === "error") {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        setLoading(false);
      }
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
  }, [shareId]);

  useEffect(() => {
    if (shareId === "demo") {
      setReport(getDemoReport());
      setLoading(false);
      return;
    }

    // Initial fetch
    fetchReport();

    // Start polling
    pollRef.current = setInterval(() => {
      fetchReport();
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [shareId, fetchReport]);

  useEffect(() => {
    const supabase = getSupabaseBrowser();
    supabase.auth.getUser().then(({ data: { user } }) => {
      setIsSignedIn(!!user);
    });
  }, []);

  async function handleSaveToDashboard() {
    if (!report) return;
    setSaveLoading(true);
    setSaveError("");
    try {
      const res = await fetch(`/api/reports/${report.id}/save`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.error || "Failed to save report");
      }
      setIsReportSaved(true);
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaveLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function checkSavedStatus() {
      if (!report?.id || isSignedIn !== true) {
        setIsReportSaved(null);
        return;
      }

      try {
        setSaveError("");
        const res = await fetch(`/api/reports/${report.id}/save`, {
          method: "GET",
          cache: "no-store",
        });
        if (res.status === 401) {
          if (!cancelled) setIsReportSaved(false);
          return;
        }
        if (!res.ok) {
          throw new Error("Failed to check saved status");
        }
        const data = await res.json();
        if (!cancelled) setIsReportSaved(Boolean(data?.saved));
      } catch {
        if (!cancelled) {
          setIsReportSaved(false);
        }
      }
    }

    checkSavedStatus();
    return () => {
      cancelled = true;
    };
  }, [report?.id, isSignedIn]);

  // Fetch Auto-Apply settings for this listing when signed in
  useEffect(() => {
    let cancelled = false;
    async function checkAutoApply() {
      if (!reportListingId || isSignedIn !== true) {
        setAutoApplyConfigured(false);
        return;
      }
      try {
        const res = await fetch(`/api/listings/${reportListingId}`, { cache: "no-store" });
        if (!res.ok) { if (!cancelled) setAutoApplyConfigured(false); return; }
        const data = await res.json();
        if (!cancelled) setAutoApplyConfigured(!!data?.auto_apply_last_updated_at);
      } catch {
        if (!cancelled) setAutoApplyConfigured(false);
      }
    }
    checkAutoApply();
    return () => { cancelled = true; };
  }, [reportListingId, isSignedIn]);


  // Queued / Running state
  if (
    report &&
    (report.status === "queued" || report.status === "running") &&
    !error
  ) {
    const staleness = getStaleness(report.workerHeartbeatAt);
    const progress = report.progressMeta;
    const pct = progress?.pct ?? 0;
    const stageLabel = progress?.stage ? (STAGE_LABELS[progress.stage] ?? progress.stage) : null;
    const estSec = progress?.est_seconds_remaining;

    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="mx-auto w-full max-w-md text-center">
          {staleness !== "unavailable" && (
            <div className="mx-auto mb-6 h-10 w-10 animate-spin rounded-full border-3 border-accent border-t-transparent" />
          )}
          {staleness === "unavailable" && (
            <div className="mx-auto mb-6 flex h-10 w-10 items-center justify-center rounded-full bg-rose-100 text-rose-500">
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M12 3a9 9 0 100 18A9 9 0 0012 3z" />
              </svg>
            </div>
          )}

          <h2 className="mb-2 text-xl font-semibold">
            {staleness === "unavailable" ? "Worker unreachable" : "Analyzing your market"}
          </h2>

          {/* Progress bar — shown once worker has sent first progress update */}
          {progress && staleness !== "unavailable" && (
            <div className="mb-4 px-2">
              <div className="mb-1 flex items-center justify-between text-xs text-muted">
                <span>{stageLabel ?? "Working..."}</span>
                <span>{pct}%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-surface-alt">
                <div
                  className="h-full rounded-full bg-accent transition-all duration-500"
                  style={{ width: `${pct}%` }}
                />
              </div>
              {estSec != null && estSec > 0 && (
                <p className="mt-1 text-xs text-muted">
                  ~{estSec < 60 ? `${estSec}s` : `${Math.round(estSec / 60)}m`} remaining
                </p>
              )}
            </div>
          )}

          {!progress && (
            <p className="mb-1 text-sm text-muted">
              {report.status === "queued"
                ? "Your report is in the queue..."
                : "Crunching the numbers..."}
            </p>
          )}

          {progress?.message && staleness !== "unavailable" && (
            <p className="mb-1 text-sm text-muted">{progress.message}</p>
          )}

          {staleness === "fresh" && !progress && (
            <p className="text-sm text-muted">This typically takes 30 to 90 seconds.</p>
          )}

          {staleness === "slow" && (
            <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-800">Taking a bit longer than usual</p>
              <p className="mt-1 text-xs text-amber-700">
                The worker is still running — hold tight.
              </p>
            </div>
          )}

          {staleness === "delayed" && (
            <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-800">This is taking longer than expected</p>
              <p className="mt-1 text-xs text-amber-700">
                The worker may be overloaded. Your report will be processed as soon as possible.
              </p>
            </div>
          )}

          {staleness === "unavailable" && (
            <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-4">
              <p className="text-sm font-medium text-rose-800">Worker appears to be offline</p>
              <p className="mt-1 text-xs text-rose-700">
                No heartbeat received in over 5 minutes. Your report will resume automatically when the worker comes back online.
              </p>
            </div>
          )}

          {report.inputAddress && (
            <p className="mt-4 text-xs text-muted">
              Report for: {report.inputAddress}
            </p>
          )}

          <p className="mt-6 text-xs text-muted">
            You can navigate away — this report will be ready when you return.
          </p>
          <Link
            href="/dashboard"
            className="mt-2 inline-block text-sm font-medium text-accent hover:underline"
          >
            ← Back to dashboard
          </Link>
        </div>
      </div>
    );
  }

  // Error state
  if (report && report.status === "error") {
    return (
      <div className="mx-auto max-w-5xl px-4 py-12 text-center sm:px-6 sm:py-20">
        <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-rose-50">
          <span className="text-3xl">!</span>
        </div>
        <h1 className="mb-3 text-2xl font-bold">Something went wrong</h1>
        <p className="mb-6 text-muted">
          {getFriendlyReportError(report.errorMessage)}
        </p>
        <Button
          onClick={() => {
            window.location.href = "/tool";
          }}
        >
          Try again
        </Button>
      </div>
    );
  }

  // Initial loading
  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-center">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          <p className="text-muted">Loading report...</p>
          <Link
            href="/dashboard"
            className="mt-4 inline-block text-sm font-medium text-accent hover:underline"
          >
            ← Back to dashboard
          </Link>
        </div>
      </div>
    );
  }

  // Not found
  if (error || !report || !report.resultSummary) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-12 text-center sm:px-6 sm:py-20">
        <h1 className="mb-4 text-2xl font-bold">Report not found</h1>
        <p className="text-muted">{error || "This report doesn't exist."}</p>
      </div>
    );
  }

  // Ready: show results
  const s = report.resultSummary;
  const suggestedNightly = s.recommendedPrice?.nightly ?? s.nightlyMedian;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
      {/* Sample report banner */}
      {shareId === "demo" && (
        <div className="mb-6 flex items-center gap-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3">
          <span className="shrink-0 rounded-full bg-blue-600 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white">
            Sample
          </span>
          <p className="text-sm text-blue-800">
            This is a generated example report using illustrative data.{" "}
            <Link
              href="/tool"
              className="font-medium underline underline-offset-2 hover:text-blue-900"
            >
              Analyze your own listing →
            </Link>
          </p>
        </div>
      )}

      {/* Header */}
      <p className="mb-1 text-xs text-muted">{report.inputAddress}</p>
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Pricing Report</h1>

      {/* Hero: suggested rate + KPI strip */}
      <div className="mb-6 overflow-hidden rounded-2xl border border-border bg-white">
        <div className="px-4 py-4 sm:px-6 sm:py-5">
          <p className="mb-1.5 text-xs text-foreground/40">Suggested rate</p>
          <div className="flex items-baseline gap-3">
            <span className="text-4xl font-bold tracking-tight">${suggestedNightly}</span>
            <span className="text-sm text-foreground/40">/night</span>
          </div>
          {s.insightHeadline && (
            <p className="mt-2 text-sm text-foreground/50">{s.insightHeadline}</p>
          )}
        </div>
        <div className="grid grid-cols-2 divide-x divide-border/50 border-t border-border/50 sm:grid-cols-3 md:grid-cols-5">
          {[
            { label: "Market median", value: s.nightlyMedian ? `$${s.nightlyMedian}` : "—" },
            { label: "Occupancy est.", value: s.occupancyPct ? `${s.occupancyPct}%` : "—" },
            { label: "Weekday avg", value: s.weekdayAvg ? `$${s.weekdayAvg}` : "—" },
            { label: "Weekend avg", value: s.weekendAvg ? `$${s.weekendAvg}` : "—" },
            { label: "Monthly est.", value: s.estimatedMonthlyRevenue ? `$${s.estimatedMonthlyRevenue.toLocaleString()}` : "—" },
          ].map((stat) => (
            <div key={stat.label} className="px-3 py-2.5 sm:px-4 sm:py-3">
              <p className="text-[11px] text-foreground/35">{stat.label}</p>
              <p className="mt-0.5 text-sm font-semibold text-foreground/70">{stat.value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Save CTA (signed-out) */}
      {isSignedIn === false && shareId !== "demo" && (
        <Card className="mb-6 border-accent/20 bg-accent/3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Save to your dashboard</p>
              <p className="mt-0.5 text-xs text-muted">
                Track pricing and rerun analyses over time.
              </p>
            </div>
            <Link href="/login?next=/dashboard">
              <Button size="sm">Sign up free</Button>
            </Link>
          </div>
        </Card>
      )}

      {/* 30-Day Pricing Plan */}
      {(report.resultCalendar ?? []).length > 0 && (
        <div className="mb-6 space-y-3">
          <PricingHeatmap
            calendar={report.resultCalendar ?? []}
            selectable={isSignedIn === true && autoApplyConfigured === true}
            onApplyDates={() => {
              window.location.href = "/dashboard";
            }}
            onFocusDate={(date) => setClickedDate(date)}
            focusedDate={clickedDate}
          />

          {/* Contextual Comparable Listings panel — appears immediately below the
              heatmap when a date is focused. This is the primary comps experience
              on the report page; HowWeEstimated will hide its duplicate comps block. */}
          {(() => {
            const compsListings =
              report.comparableListings ??
              report.resultSummary?.comparableListings ??
              null;
            const comps =
              report.compsSummary ?? report.resultSummary?.compsSummary ?? null;
            if (!clickedDate || !compsListings || compsListings.length === 0) return null;
            return (
              <div className="overflow-hidden rounded-2xl border border-sky-200/70 bg-white">
                <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-3">
                  <div>
                    <p className="text-sm font-semibold text-foreground/80">
                      Comparable listings
                      <span className="ml-1.5 font-normal text-foreground/50">
                        for {clickedDate}
                      </span>
                    </p>
                    {snappedDate !== clickedDate && (
                      <p className="mt-0.5 text-xs text-amber-700">
                        No comp data for {clickedDate} — showing nearest sampled day below.
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setClickedDate(null)}
                    aria-label="Close comparable listings panel"
                    className="shrink-0 rounded-lg px-2 py-1 text-xs font-medium text-foreground/35 transition-colors hover:bg-gray-100 hover:text-foreground/65"
                  >
                    ✕
                  </button>
                </div>
                <div className="px-5 py-4">
                  <ComparableListingsSection
                    listings={compsListings as ComparableListing[]}
                    comps={comps}
                    embedded={true}
                    selectedDate={snappedDate}
                    clickedDate={clickedDate}
                  />
                </div>
              </div>
            );
          })()}

          {/* Auto-Apply CTA — only when signed in but not configured */}
          {isSignedIn === true && autoApplyConfigured === false && (
            <div className="overflow-hidden rounded-2xl border border-gray-200 bg-gray-50">
              <div className="flex items-start gap-4 px-5 py-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-gray-200">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="text-foreground/60" aria-hidden="true">
                    <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                    <polyline points="13 2 13 9 20 9" />
                  </svg>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-foreground/80">Set up Auto-Apply to use this calendar</p>
                  <p className="mt-0.5 text-xs leading-snug text-foreground/45">
                    Configure Auto-Apply in your dashboard to select nights and apply pricing recommendations directly to Airbnb.
                  </p>
                  <a
                    href="/dashboard"
                    className="mt-3 inline-flex items-center gap-1.5 rounded-xl bg-foreground px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-foreground/80"
                  >
                    Go to dashboard
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M5 12h14M12 5l7 7-7 7" />
                    </svg>
                  </a>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* How We Estimated */}
      {(
        report.targetSpec ||
        report.resultSummary?.targetSpec ||
        report.compsSummary ||
        report.resultSummary?.compsSummary ||
        report.comparableListings ||
        report.resultSummary?.comparableListings ||
        report.benchmarkInfo ||
        report.resultSummary?.benchmarkInfo ||
        (Array.isArray(report.inputAttributes?.preferredComps) &&
          report.inputAttributes.preferredComps.some(
            (comp) => comp.enabled !== false && comp.listingUrl
          ))
      ) && (
        <HowWeEstimated
          report={report}
          selectedDate={snappedDate}
          clickedDate={clickedDate}
          hideComparableListings={true}
        />
      )}

      {/* Section 4 - Track your market */}
      {isSignedIn === false && (
        <div className="mb-8">
          <h2 className="mb-4 text-lg font-semibold">Track your market</h2>
          <Card className="border-accent/20 bg-accent/[0.02]">
            <p className="text-sm text-muted">
              Save this report to your dashboard and track pricing over time.
            </p>
            <div className="mt-4 flex flex-col gap-2 sm:flex-row">
              <Link href="/login?next=/dashboard">
                <Button size="sm">Sign up free</Button>
              </Link>
              <Link href="/login?next=/dashboard" className="self-start">
                <Button size="sm" variant="secondary">
                  Sign in
                </Button>
              </Link>
            </div>
          </Card>
        </div>
      )}

      {isSignedIn === true && isReportSaved === false && (
        <div className="mb-8">
          <h2 className="mb-4 text-lg font-semibold">Track your market</h2>
          <Card>
            <p className="text-base font-semibold text-foreground">
              Save to your dashboard
            </p>
            <p className="mt-1 text-sm text-muted">
              Keep this report and rerun analysis anytime.
            </p>
            {saveError && <p className="mt-3 text-sm text-rose-600">{saveError}</p>}
            <div className="mt-4">
              <Button onClick={handleSaveToDashboard} disabled={saveLoading}>
                {saveLoading ? "Saving..." : "Save to dashboard"}
              </Button>
            </div>
          </Card>
        </div>
      )}

      {/* Meta */}
      <p className="mt-8 text-center text-xs text-muted">
        {shareId === "demo"
          ? "Sample report — values are illustrative, not from live market data"
          : `Report generated by ${report.coreVersion} on ${new Date(report.createdAt).toLocaleDateString()}`}
      </p>
    </div>
  );
}



