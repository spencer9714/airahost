"use client";

import { useEffect, useState, useMemo, useCallback, useRef, use } from "react";
import Link from "next/link";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { HowWeEstimated } from "@/components/report/HowWeEstimated";
import { getSupabaseBrowser } from "@/lib/supabase";
import type { PricingReport, CalendarDay } from "@/lib/schemas";
import { generatePricingReport } from "@/core/pricingCore";

// Demo report (unchanged)
function getDemoReport(): PricingReport {
  const result = generatePricingReport({
    listing: {
      address: "742 Evergreen Terrace, Springfield, OR",
      propertyType: "entire_home",
      bedrooms: 3,
      bathrooms: 2,
      maxGuests: 6,
      amenities: ["wifi", "kitchen", "washer", "dryer", "free_parking", "bbq"],
    },
    startDate: "2026-03-01",
    endDate: "2026-03-31",
    discountPolicy: {
      weeklyDiscountPct: 10,
      monthlyDiscountPct: 20,
      refundable: true,
      nonRefundableDiscountPct: 10,
      stackingMode: "compound",
      maxTotalDiscountPct: 40,
    },
  });

  return {
    id: "demo",
    shareId: "demo",
    createdAt: new Date().toISOString(),
    status: "ready",
    coreVersion: result.coreVersion,
    inputAddress: "742 Evergreen Terrace, Springfield, OR",
    inputAttributes: {
      address: "742 Evergreen Terrace, Springfield, OR",
      propertyType: "entire_home",
      bedrooms: 3,
      bathrooms: 2,
      maxGuests: 6,
      amenities: ["wifi", "kitchen", "washer", "dryer", "free_parking", "bbq"],
      lastMinuteStrategy: {
        mode: "auto",
        aggressiveness: 50,
        floor: 0.65,
        cap: 1.05,
      },
    },
    inputDateStart: "2026-03-01",
    inputDateEnd: "2026-03-31",
    discountPolicy: {
      weeklyDiscountPct: 10,
      monthlyDiscountPct: 20,
      refundable: true,
      nonRefundableDiscountPct: 10,
      stackingMode: "compound",
      maxTotalDiscountPct: 40,
    },
    resultSummary: result.summary,
    resultCalendar: result.calendar,
    errorMessage: null,
  };
}

// Polling config
const POLL_INTERVAL_MS = 2_000;
const SLOW_THRESHOLD_MS = 120_000; // 2 minutes

export default function ResultsPage({
  params,
}: {
  params: Promise<{ shareId: string }>;
}) {
  const { shareId } = use(params);
  const [report, setReport] = useState<PricingReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [calendarView, setCalendarView] = useState<"base" | "effective">(
    "base"
  );

  // Polling state
  const [pollStartedAt] = useState(() => Date.now());
  const [isSlow, setIsSlow] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Auth state
  const [isSignedIn, setIsSignedIn] = useState<boolean | null>(null);

  // Save-to-dashboard state
  const [isReportSaved, setIsReportSaved] = useState<boolean | null>(null);
  const [saveLoading, setSaveLoading] = useState(false);
  const [saveError, setSaveError] = useState("");

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
      if (Date.now() - pollStartedAt > SLOW_THRESHOLD_MS) {
        setIsSlow(true);
      }
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [shareId, fetchReport, pollStartedAt]);

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


  // Queued / Running state
  if (
    report &&
    (report.status === "queued" || report.status === "running") &&
    !error
  ) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="mx-auto max-w-md text-center">
          <div className="mx-auto mb-6 h-10 w-10 animate-spin rounded-full border-3 border-accent border-t-transparent" />
          <h2 className="mb-2 text-xl font-semibold">Analyzing your market</h2>
          <p className="mb-1 text-sm text-muted">
            {report.status === "queued"
              ? "Your report is in the queue..."
              : "Crunching the numbers..."}
          </p>
          <p className="text-sm text-muted">
            This typically takes 30 to 90 seconds.
          </p>

          {isSlow && (
            <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-800">
                This is taking longer than usual
              </p>
              <p className="mt-1 text-xs text-amber-700">
                The worker may be busy or temporarily offline. Your report will
                be processed as soon as possible.
              </p>
            </div>
          )}

          {report.inputAddress && (
            <p className="mt-4 text-xs text-muted">
              Report for: {report.inputAddress}
            </p>
          )}
        </div>
      </div>
    );
  }

  // Error state
  if (report && report.status === "error") {
    return (
      <div className="mx-auto max-w-5xl px-6 py-20 text-center">
        <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-rose-50">
          <span className="text-3xl">!</span>
        </div>
        <h1 className="mb-3 text-2xl font-bold">Something went wrong</h1>
        <p className="mb-6 text-muted">
          {report.errorMessage ||
            "We couldn't generate your pricing report. Please try again."}
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
        </div>
      </div>
    );
  }

  // Not found
  if (error || !report || !report.resultSummary) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-20 text-center">
        <h1 className="mb-4 text-2xl font-bold">Report not found</h1>
        <p className="text-muted">{error || "This report doesn't exist."}</p>
      </div>
    );
  }

  // Ready: show results
  const s = report.resultSummary;
  const dp = report.discountPolicy;
  const strategyUsed = report.inputAttributes?.lastMinuteStrategy ?? {
    mode: "auto" as const,
    aggressiveness: 50,
    floor: 0.65,
    cap: 1.05,
  };
  const dynamicRows = (report.resultCalendar ?? []).filter(
    (d) =>
      d.dynamicAdjustment &&
      typeof d.dynamicAdjustment.timeMultiplier === "number" &&
      typeof d.dynamicAdjustment.finalMultiplier === "number" &&
      typeof d.dynamicAdjustment.demandScore === "number"
  );
  const dynamicSummary =
    dynamicRows.length > 0
      ? (() => {
          const timeMin = Math.min(
            ...dynamicRows.map((d) => d.dynamicAdjustment!.timeMultiplier)
          );
          const timeMax = Math.max(
            ...dynamicRows.map((d) => d.dynamicAdjustment!.timeMultiplier)
          );
          const finalMin = Math.min(
            ...dynamicRows.map((d) => d.dynamicAdjustment!.finalMultiplier)
          );
          const finalMax = Math.max(
            ...dynamicRows.map((d) => d.dynamicAdjustment!.finalMultiplier)
          );
          const avgDemandScore =
            dynamicRows.reduce(
              (sum, d) => sum + d.dynamicAdjustment!.demandScore,
              0
            ) / dynamicRows.length;
          return { timeMin, timeMax, finalMin, finalMax, avgDemandScore };
        })()
      : null;
  const toPct = (m: number) => {
    const pct = Math.round((m - 1) * 100);
    return `${pct > 0 ? "+" : ""}${pct}%`;
  };
  const pctRange = (min: number, max: number) => `${toPct(min)} to ${toPct(max)}`;
  const stayLengthAverages =
    s.stayLengthAverages && s.stayLengthAverages.length > 0
      ? s.stayLengthAverages
      : [
          {
            stayLength: Math.min(7, (report.resultCalendar?.length ?? 0) || 7),
            avgNightly: s.weeklyStayAvgNightly,
            lengthDiscountPct: 0,
          },
          {
            stayLength: Math.min(28, (report.resultCalendar?.length ?? 0) || 28),
            avgNightly: s.monthlyStayAvgNightly,
            lengthDiscountPct: 0,
          },
        ].filter(
          (point, idx, arr) =>
            arr.findIndex((x) => x.stayLength === point.stayLength) === idx
        );

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      {/* Address header */}
      <p className="mb-1 text-sm text-muted">Revenue report for</p>
      <h1 className="mb-8 text-2xl font-bold">{report.inputAddress}</h1>

      {/* Section 1 - Revenue Opportunity */}
      <Card className="mb-6 border-accent/20 bg-accent/[0.02]">
        <p className="text-sm font-medium text-accent">Revenue Opportunity</p>
        <p className="mt-2 text-xl font-semibold">{s.insightHeadline}</p>
        <p className="mt-2 text-sm text-muted">
          Estimated monthly revenue at current pricing:{" "}
          <span className="font-semibold text-foreground">
            ${s.estimatedMonthlyRevenue.toLocaleString()}
          </span>
        </p>
        {s.selectedRangeNights && s.selectedRangeAvgNightly ? (
          <p className="mt-1 text-xs text-muted">
            Based on your selected {s.selectedRangeNights}-night stay average of $
            {s.selectedRangeAvgNightly}/night.
          </p>
        ) : null}
      </Card>

      {/* Section 2 - Market Snapshot */}
      <h2 className="mb-4 text-lg font-semibold">Market snapshot</h2>
      <div className="mb-8 grid gap-4 sm:grid-cols-3">
        <Card>
          <p className="text-sm text-muted">Nightly range</p>
          <p className="mt-1 text-2xl font-bold">
            ${s.nightlyMin} - ${s.nightlyMax}
          </p>
          <p className="mt-1 text-sm text-muted">Median: ${s.nightlyMedian}</p>
        </Card>
        <Card>
          <p className="text-sm text-muted">Occupancy</p>
          <p className="mt-1 text-2xl font-bold">{s.occupancyPct}%</p>
          <p className="mt-1 text-sm text-muted">Estimated for this area</p>
        </Card>
        <Card>
          <p className="text-sm text-muted">Weekday vs weekend</p>
          <div className="mt-1 flex items-baseline gap-3">
            <div>
              <p className="text-xl font-bold">${s.weekdayAvg}</p>
              <p className="text-xs text-muted">Weekday avg</p>
            </div>
            <span className="text-muted">/</span>
            <div>
              <p className="text-xl font-bold">${s.weekendAvg}</p>
              <p className="text-xs text-muted">Weekend avg</p>
            </div>
          </div>
        </Card>
      </div>

      {/* Save to dashboard CTA (signed-out users) */}
      {isSignedIn === false && shareId !== "demo" && (
        <Card className="mb-6 border-accent/20 bg-accent/3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Save this report to your dashboard</p>
              <p className="mt-1 text-xs text-muted">
                Create a free account to track pricing, rerun analyses, and compare over time.
              </p>
            </div>
            <Link href="/login?next=/dashboard">
              <Button size="sm">Sign up free</Button>
            </Link>
          </div>
        </Card>
      )}

      {/* Section 2.5 - How We Estimated (transparency) */}
      {(
        report.targetSpec ||
        report.resultSummary?.targetSpec ||
        report.compsSummary ||
        report.resultSummary?.compsSummary ||
        report.comparableListings ||
        report.resultSummary?.comparableListings
      ) && (
        <HowWeEstimated report={report} />
      )}

      {/* Section 3 - Price Calendar */}
      <PriceCalendar
        calendar={report.resultCalendar ?? []}
        calendarView={calendarView}
        onViewChange={setCalendarView}
      />

      {/* Section 4 - Discount Explanation */}
      <h2 className="mb-4 text-lg font-semibold">
        How your discounts work
      </h2>
      <Card className="mb-8">
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-gray-50/80 p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <p className="text-base font-semibold text-foreground">
                Last-minute pricing strategy
              </p>
              <span className="rounded-full border border-border bg-white px-2.5 py-1 text-xs font-medium text-foreground">
                {strategyUsed.mode === "auto" ? "Auto" : "Custom"}
              </span>
            </div>

            {dynamicSummary ? (
              <div className="space-y-2">
                <StatRow
                  label="Time-to-check-in impact"
                  value={pctRange(dynamicSummary.timeMin, dynamicSummary.timeMax)}
                />
                <StatRow
                  label="Demand context"
                  value={`Score: ${dynamicSummary.avgDemandScore.toFixed(2)} | Occupancy: ${s.occupancyPct}%`}
                />
                <StatRow
                  label="Final adjustment"
                  value={pctRange(dynamicSummary.finalMin, dynamicSummary.finalMax)}
                />
              </div>
            ) : (
              <p className="text-sm text-muted-foreground font-medium">
                Detailed last-minute breakdown is unavailable for this report.
              </p>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-xl border border-border bg-gray-50/80 p-4">
              <p className="mb-3 text-base font-semibold text-foreground">
                Length-of-stay discounts
              </p>
              <div className="space-y-2">
                <StatRow label="Weekly" value={`${dp.weeklyDiscountPct}%`} badge />
                <StatRow label="Monthly" value={`${dp.monthlyDiscountPct}%`} badge />
              </div>
            </div>

            <div className="rounded-xl border border-border bg-gray-50/80 p-4">
              <p className="mb-3 text-base font-semibold text-foreground">
                Booking policy
              </p>
              <div className="space-y-2">
                <StatRow
                  label="Cancellation"
                  value={dp.refundable ? "Refundable" : "Non-refundable"}
                  badge
                />
                <StatRow
                  label="Stacking mode"
                  value={dp.stackingMode.replace("_", " ")}
                  badge
                  capitalize
                />
                <StatRow
                  label="Max discount cap"
                  value={`${dp.maxTotalDiscountPct}%`}
                  badge
                />
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-gray-50/80 p-4">
            <p className="mb-3 text-base font-semibold text-foreground">
              Example pricing impact
            </p>
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3 rounded-lg bg-white px-3 py-2">
                <p className="text-sm text-muted">
                  1-night stay (no length-of-stay discount)
                </p>
                <p className="text-sm font-semibold text-foreground tabular-nums">
                  ${stayLengthAverages.find((x) => x.stayLength === 1)?.avgNightly ?? s.selectedRangeAvgNightly ?? s.nightlyMedian}
                </p>
              </div>
              <div className="flex items-center justify-between gap-3 rounded-lg bg-white px-3 py-2">
                <p className="text-sm text-muted">
                  7-night stay (includes weekly discount)
                </p>
                <p className="text-sm font-semibold text-foreground tabular-nums">
                  ${stayLengthAverages.find((x) => x.stayLength === 7)?.avgNightly ?? s.weeklyStayAvgNightly}
                </p>
              </div>
            </div>
          </div>
        </div>
      </Card>
      {/* Section 5 - Track your market */}
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
        Report generated by {report.coreVersion} on{" "}
        {new Date(report.createdAt).toLocaleDateString()}
      </p>
    </div>
  );
}

function StatRow({
  label,
  value,
  badge = false,
  capitalize = false,
}: {
  label: string;
  value: string;
  badge?: boolean;
  capitalize?: boolean;
}) {
  const valueClass = "text-sm font-semibold text-foreground";

  return (
    <div className="flex items-center justify-between gap-3">
      <p className="text-sm font-medium text-muted-foreground">{label}</p>
      {badge ? (
        <span
          className={`rounded-full border border-border bg-white px-2.5 py-1 text-sm font-semibold text-foreground ${
            capitalize ? "capitalize" : ""
          }`}
        >
          {value}
        </span>
      ) : (
        <p className={`${valueClass} tabular-nums ${capitalize ? "capitalize" : ""}`}>
          {value}
        </p>
      )}
    </div>
  );
}

// Price Calendar component

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const DAY_HEADERS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

interface MonthData {
  year: number;
  month: number; // 0-indexed
  label: string;
}

function PriceCalendar({
  calendar,
  calendarView,
  onViewChange,
}: {
  calendar: CalendarDay[];
  calendarView: "base" | "effective";
  onViewChange: (v: "base" | "effective") => void;
}) {
  // Build a lookup map: "YYYY-MM-DD" -> CalendarDay
  const dayMap = useMemo(() => {
    const m = new Map<string, CalendarDay>();
    for (const d of calendar) m.set(d.date, d);
    return m;
  }, [calendar]);

  // Determine which months are spanned
  const months = useMemo<MonthData[]>(() => {
    if (calendar.length === 0) return [];
    const first = calendar[0].date.split("-").map(Number);
    const last = calendar[calendar.length - 1].date.split("-").map(Number);
    const result: MonthData[] = [];
    let y = first[0], m = first[1] - 1;
    while (y < last[0] || (y === last[0] && m <= last[1] - 1)) {
      result.push({ year: y, month: m, label: `${MONTH_NAMES[m]} ${y}` });
      m++;
      if (m > 11) { m = 0; y++; }
    }
    return result;
  }, [calendar]);

  const [activeMonth, setActiveMonth] = useState(0);

  if (months.length === 0) return null;

  const current = months[activeMonth];

  // First day of month (0=Sun) and total days in month
  const firstDow = new Date(Date.UTC(current.year, current.month, 1)).getUTCDay();
  const daysInMonth = new Date(Date.UTC(current.year, current.month + 1, 0)).getUTCDate();

  // Build grid: array of weeks x 7 days; each cell is a date number or null.
  const weeks: (number | null)[][] = [];
  let week: (number | null)[] = new Array(firstDow).fill(null);
  for (let d = 1; d <= daysInMonth; d++) {
    week.push(d);
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }

  function dateStr(day: number) {
    const mm = String(current.month + 1).padStart(2, "0");
    const dd = String(day).padStart(2, "0");
    return `${current.year}-${mm}-${dd}`;
  }

  // Price range for color coding
  const prices = calendar.map((d) => d.basePrice);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);

  function priceColor(price: number): string {
    if (maxPrice === minPrice) return "bg-accent/5";
    const ratio = (price - minPrice) / (maxPrice - minPrice);
    if (ratio < 0.33) return "bg-emerald-50 text-emerald-700";
    if (ratio < 0.66) return "bg-amber-50 text-amber-700";
    return "bg-rose-50 text-rose-700";
  }

  return (
    <div className="mb-8">
      {/* Header row */}
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-lg font-semibold">Price calendar</h2>
        <div className="flex gap-1 rounded-xl border border-border p-0.5">
          <button
            onClick={() => onViewChange("base")}
            className={`rounded-lg px-3 py-1 text-sm transition-colors ${
              calendarView === "base"
                ? "bg-foreground text-white"
                : "text-muted hover:text-foreground"
            }`}
          >
            Base price
          </button>
          <button
            onClick={() => onViewChange("effective")}
            className={`rounded-lg px-3 py-1 text-sm transition-colors ${
              calendarView === "effective"
                ? "bg-foreground text-white"
                : "text-muted hover:text-foreground"
            }`}
          >
            Effective price
          </button>
        </div>
      </div>

      <Card className="p-4 sm:p-6">
        {/* Month navigation */}
        <div className="mb-5 flex items-center justify-between">
          <button
            onClick={() => setActiveMonth((p) => Math.max(0, p - 1))}
            disabled={activeMonth === 0}
            className="flex h-9 w-9 items-center justify-center rounded-full border border-border text-sm transition-colors hover:bg-gray-50 disabled:opacity-30"
            aria-label="Previous month"
          >
            &larr;
          </button>
          <h3 className="text-base font-semibold">{current.label}</h3>
          <button
            onClick={() =>
              setActiveMonth((p) => Math.min(months.length - 1, p + 1))
            }
            disabled={activeMonth === months.length - 1}
            className="flex h-9 w-9 items-center justify-center rounded-full border border-border text-sm transition-colors hover:bg-gray-50 disabled:opacity-30"
            aria-label="Next month"
          >
            &rarr;
          </button>
        </div>

        {/* Day-of-week headers */}
        <div className="grid grid-cols-7 gap-1 sm:gap-2">
          {DAY_HEADERS.map((dh) => (
            <div
              key={dh}
              className="pb-2 text-center text-xs font-medium text-muted"
            >
              {dh}
            </div>
          ))}

          {/* Calendar cells */}
          {weeks.flat().map((day, idx) => {
            if (day === null) {
              return <div key={`empty-${idx}`} className="aspect-square" />;
            }

            const ds = dateStr(day);
            const entry = dayMap.get(ds);

            if (!entry) {
              return (
                <div
                  key={ds}
                  className="flex aspect-square flex-col items-center justify-center rounded-xl border border-border/40 opacity-30"
                >
                  <span className="text-xs">{day}</span>
                </div>
              );
            }

            const price =
              calendarView === "base"
                ? entry.basePrice
                : entry.refundablePrice;
            const colorClass = priceColor(entry.basePrice);
            const flags = entry.flags ?? [];
            const isInterpolated = flags.includes("interpolated");
            const isMissing = flags.includes("missing_data");

            return (
              <div
                key={ds}
                className={`group relative flex aspect-square flex-col items-center justify-center rounded-xl border border-border/60 transition-shadow hover:shadow-md ${
                  entry.isWeekend ? "border-accent/20" : ""
                } ${isInterpolated && !isMissing ? "border-dashed" : ""}`}
              >
                <span className="text-[10px] leading-none text-muted sm:text-xs">
                  {day}
                </span>
                <span
                  className={`mt-0.5 rounded-md px-1 py-0.5 text-xs font-semibold sm:text-sm ${colorClass}`}
                >
                  {isMissing ? "?" : isInterpolated ? "~" : ""}${price}
                </span>

                {calendarView === "effective" && (
                  <div className="pointer-events-none absolute -top-16 left-1/2 z-10 hidden -translate-x-1/2 rounded-xl border border-border bg-white px-3 py-2 shadow-lg group-hover:block">
                    <p className="whitespace-nowrap text-[10px] text-muted">
                      Refundable: <strong>${entry.refundablePrice}</strong>
                    </p>
                    <p className="whitespace-nowrap text-[10px] text-muted">
                      Non-refund: <strong>${entry.nonRefundablePrice}</strong>
                    </p>
                    {isInterpolated && (
                      <p className="whitespace-nowrap text-[10px] text-amber-600">Estimated from nearby days</p>
                    )}
                    {isMissing && (
                      <p className="whitespace-nowrap text-[10px] text-rose-600">No market data available</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Legend */}
        <div className="mt-4 flex flex-wrap items-center justify-center gap-4 border-t border-border pt-4 text-xs text-muted">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded bg-emerald-50 border border-emerald-200" />
            Lower
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded bg-amber-50 border border-amber-200" />
            Average
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded bg-rose-50 border border-rose-200" />
            Higher
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded border border-dashed border-gray-400" />
            ~Estimated
          </span>
          <span className="flex items-center gap-1.5">
            <span className="text-rose-500 font-medium">?</span>
            No data
          </span>
          {calendarView === "effective" && (
            <span className="text-muted">Hover for details</span>
          )}
        </div>

        {months.length > 1 && (
          <div className="mt-3 flex items-center justify-center gap-1.5">
            {months.map((m, i) => (
              <button
                key={m.label}
                onClick={() => setActiveMonth(i)}
                className={`h-2 w-2 rounded-full transition-colors ${
                  i === activeMonth ? "bg-accent" : "bg-border"
                }`}
                aria-label={m.label}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}




