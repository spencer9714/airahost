"use client";

import { useEffect, useState, useMemo, use } from "react";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import type { PricingReport, CalendarDay } from "@/lib/schemas";
import { generatePricingReport } from "@/core/pricingCore";

// Seeded demo report
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

  // Market tracking
  const [trackEmail, setTrackEmail] = useState("");
  const [trackWeekly, setTrackWeekly] = useState(true);
  const [trackUnderMarket, setTrackUnderMarket] = useState(true);
  const [trackSubmitted, setTrackSubmitted] = useState(false);
  const [trackLoading, setTrackLoading] = useState(false);

  useEffect(() => {
    if (shareId === "demo") {
      setReport(getDemoReport());
      setLoading(false);
      return;
    }

    fetch(`/api/r/${shareId}`)
      .then((res) => {
        if (!res.ok) throw new Error("Report not found");
        return res.json();
      })
      .then(setReport)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [shareId]);

  async function handleTrackSubmit() {
    if (!report) return;
    setTrackLoading(true);
    try {
      const res = await fetch("/api/track-market", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: trackEmail,
          address: report.inputAddress,
          notifyWeekly: trackWeekly,
          notifyUnderMarket: trackUnderMarket,
        }),
      });
      if (!res.ok) throw new Error("Failed");
      setTrackSubmitted(true);
    } catch {
      /* ignore for MVP */
    } finally {
      setTrackLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-center">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          <p className="text-muted">Generating your revenue report...</p>
        </div>
      </div>
    );
  }

  if (error || !report || !report.resultSummary) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-20 text-center">
        <h1 className="mb-4 text-2xl font-bold">Report not found</h1>
        <p className="text-muted">{error || "This report doesn't exist."}</p>
      </div>
    );
  }

  const s = report.resultSummary;
  const cal = report.resultCalendar ?? [];
  const dp = report.discountPolicy;

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      {/* Address header */}
      <p className="mb-1 text-sm text-muted">Revenue report for</p>
      <h1 className="mb-8 text-2xl font-bold">{report.inputAddress}</h1>

      {/* Section 1 — Revenue Opportunity */}
      <Card className="mb-6 border-accent/20 bg-accent/[0.02]">
        <p className="text-sm font-medium text-accent">Revenue Opportunity</p>
        <p className="mt-2 text-xl font-semibold">{s.insightHeadline}</p>
        <p className="mt-2 text-sm text-muted">
          Estimated monthly revenue at current pricing:{" "}
          <span className="font-semibold text-foreground">
            ${s.estimatedMonthlyRevenue.toLocaleString()}
          </span>
        </p>
      </Card>

      {/* Section 2 — Market Snapshot */}
      <h2 className="mb-4 text-lg font-semibold">Market snapshot</h2>
      <div className="mb-8 grid gap-4 sm:grid-cols-3">
        <Card>
          <p className="text-sm text-muted">Nightly range</p>
          <p className="mt-1 text-2xl font-bold">
            ${s.nightlyMin} – ${s.nightlyMax}
          </p>
          <p className="mt-1 text-sm text-muted">Median: ${s.nightlyMedian}</p>
        </Card>
        <Card>
          <p className="text-sm text-muted">Occupancy</p>
          <p className="mt-1 text-2xl font-bold">{s.occupancyPct}%</p>
          <p className="mt-1 text-sm text-muted">Estimated for this area</p>
        </Card>
        <Card>
          <p className="text-sm text-muted">Pricing strategy</p>
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

      {/* Section 3 — Price Calendar */}
      <PriceCalendar
        calendar={cal}
        calendarView={calendarView}
        onViewChange={setCalendarView}
      />

      {/* Section 4 — Discount Explanation */}
      <h2 className="mb-4 text-lg font-semibold">
        How your discounts work
      </h2>
      <Card className="mb-8">
        <div className="space-y-3 text-sm leading-relaxed text-muted">
          <p>
            Your weekly discount is{" "}
            <strong className="text-foreground">{dp.weeklyDiscountPct}%</strong>{" "}
            and your monthly discount is{" "}
            <strong className="text-foreground">
              {dp.monthlyDiscountPct}%
            </strong>
            .
          </p>
          <p>
            Cancellation policy:{" "}
            <strong className="text-foreground">
              {dp.refundable ? "Refundable" : "Non-refundable"}
            </strong>
            {!dp.refundable && (
              <>
                {" "}
                with an additional{" "}
                <strong className="text-foreground">
                  {dp.nonRefundableDiscountPct}%
                </strong>{" "}
                discount
              </>
            )}
            .
          </p>
          <p>
            Stacking mode:{" "}
            <strong className="text-foreground capitalize">
              {dp.stackingMode.replace("_", " ")}
            </strong>{" "}
            — max total discount capped at{" "}
            <strong className="text-foreground">
              {dp.maxTotalDiscountPct}%
            </strong>
            .
          </p>
          <div className="mt-4 rounded-xl bg-gray-50 p-4">
            <p className="font-medium text-foreground">Example</p>
            <p className="mt-1">
              For a 7-night stay, the average nightly rate after discounts is{" "}
              <strong className="text-foreground">
                ${s.weeklyStayAvgNightly}
              </strong>
              .
            </p>
            {cal.length >= 28 && (
              <p className="mt-1">
                For a 28-night stay, the average nightly rate after discounts is{" "}
                <strong className="text-foreground">
                  ${s.monthlyStayAvgNightly}
                </strong>
                .
              </p>
            )}
          </div>
        </div>
      </Card>

      {/* Section 5 — Market Tracking */}
      <h2 className="mb-4 text-lg font-semibold">Track your market</h2>
      <Card>
        {trackSubmitted ? (
          <div className="py-4 text-center">
            <p className="text-lg font-semibold text-success">
              You&apos;re all set!
            </p>
            <p className="mt-2 text-sm text-muted">
              We&apos;ll send updates to {trackEmail}.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-muted">
              Stay ahead of pricing changes in your market. We&apos;ll notify
              you so you can adjust your pricing with confidence.
            </p>

            <div>
              <label className="mb-1.5 block text-sm font-medium">
                Email address
              </label>
              <input
                type="email"
                placeholder="you@example.com"
                value={trackEmail}
                onChange={(e) => setTrackEmail(e.target.value)}
                className="w-full rounded-xl border border-border px-4 py-2.5 text-sm outline-none transition-colors focus:border-accent"
              />
            </div>

            <div className="flex flex-col gap-3 sm:flex-row sm:gap-6">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={trackWeekly}
                  onChange={(e) => setTrackWeekly(e.target.checked)}
                  className="accent-accent"
                />
                Email me weekly updates
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={trackUnderMarket}
                  onChange={(e) => setTrackUnderMarket(e.target.checked)}
                  className="accent-accent"
                />
                Alert me if I&apos;m under market
              </label>
            </div>

            <Button
              onClick={handleTrackSubmit}
              disabled={!trackEmail.includes("@") || trackLoading}
            >
              {trackLoading ? "Saving..." : "Start tracking"}
            </Button>
          </div>
        )}
      </Card>

      {/* Meta */}
      <p className="mt-8 text-center text-xs text-muted">
        Report generated by {report.coreVersion} on{" "}
        {new Date(report.createdAt).toLocaleDateString()}
      </p>
    </div>
  );
}

// ── Price Calendar Component ────────────────────────────────────

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
  // Build a lookup map: "YYYY-MM-DD" → CalendarDay
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

  // Build grid: array of 6 weeks × 7 days, each cell is date number or null
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
              // Day exists in the month but outside report range
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

            return (
              <div
                key={ds}
                className={`group relative flex aspect-square flex-col items-center justify-center rounded-xl border border-border/60 transition-shadow hover:shadow-md ${
                  entry.isWeekend ? "border-accent/20" : ""
                }`}
              >
                <span className="text-[10px] leading-none text-muted sm:text-xs">
                  {day}
                </span>
                <span
                  className={`mt-0.5 rounded-md px-1 py-0.5 text-xs font-semibold sm:text-sm ${colorClass}`}
                >
                  ${price}
                </span>

                {/* Tooltip on hover — effective view shows both prices */}
                {calendarView === "effective" && (
                  <div className="pointer-events-none absolute -top-16 left-1/2 z-10 hidden -translate-x-1/2 rounded-xl border border-border bg-white px-3 py-2 shadow-lg group-hover:block">
                    <p className="whitespace-nowrap text-[10px] text-muted">
                      Refundable: <strong>${entry.refundablePrice}</strong>
                    </p>
                    <p className="whitespace-nowrap text-[10px] text-muted">
                      Non-refund: <strong>${entry.nonRefundablePrice}</strong>
                    </p>
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
          {calendarView === "effective" && (
            <span className="text-muted">Hover for details</span>
          )}
        </div>

        {/* Month dots for multi-month ranges */}
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
