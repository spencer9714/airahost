import Link from "next/link";
import { Button } from "@/components/Button";
import type { ReportSummary, RecommendedPrice } from "@/lib/schemas";

interface Props {
  summary: ReportSummary;
  recommendedPrice: RecommendedPrice | null;
  reportShareId: string;
  onRerun: () => void;
  isRerunning: boolean;
  listingName: string;
  propertyMeta: {
    propertyType: string;
    guests: number;
    beds: number;
    baths: number;
  } | null;
  benchmarkMeta?: {
    count: number;
    primaryUrl: string | null;
  } | null;
  lastAnalysisDate: string | null;
  /**
   * Real listing price captured by the background worker from the user's live
   * Airbnb listing.  When present it becomes the primary basis for
   * above-market / below-market positioning.  null/undefined means the worker
   * hasn't observed the price yet — fall back to recommendation-vs-market copy.
   */
  observedListingPrice?: number | null;
}

/**
 * Compute a market-position badge.
 *
 * @param price        The price to compare (observed listing price OR recommended price).
 * @param marketMedian The market median nightly rate.
 * @param source       Semantic source of `price` — controls badge wording.
 *   "observed"    → "Your price X% above/below market"  (worker-observed listing price)
 *   "recommended" → "Recommendation X% above/below market"  (our forecast suggestion)
 */
function marketPositionBadge(
  price: number,
  marketMedian: number,
  source: "observed" | "recommended"
): { label: string; color: string } | null {
  if (marketMedian <= 0) return null;
  const pct = Math.round((price / marketMedian - 1) * 100);

  if (pct < -3) {
    return {
      label:
        source === "observed"
          ? `Your price ${Math.abs(pct)}% below market`
          : `Recommendation ${Math.abs(pct)}% below market`,
      color: "bg-emerald-50 text-emerald-800 border-emerald-300",
    };
  }
  if (pct > 3) {
    return {
      label:
        source === "observed"
          ? `Your price ${pct}% above market`
          : `Recommendation ${pct}% above market`,
      color: "bg-amber-50 text-amber-800 border-amber-300",
    };
  }
  return {
    label:
      source === "observed" ? "Your price at market" : "Recommendation at market",
    color: "bg-gray-100 text-gray-700 border-gray-300",
  };
}

export function RecommendationBanner({
  summary,
  recommendedPrice,
  reportShareId,
  onRerun,
  isRerunning,
  listingName,
  propertyMeta,
  benchmarkMeta,
  lastAnalysisDate,
  observedListingPrice,
}: Props) {
  const recommended = recommendedPrice?.nightly ?? summary.nightlyMedian;
  const median = summary.nightlyMedian;

  // Market-position badge: prefer observed listing price when available.
  const badge =
    observedListingPrice != null
      ? marketPositionBadge(observedListingPrice, median, "observed")
      : marketPositionBadge(recommended, median, "recommended");

  const stats = [
    {
      label: "Market median",
      value: median ? `$${median}` : "—",
    },
    {
      label: "Occupancy est.",
      value: summary.occupancyPct ? `${summary.occupancyPct}%` : "—",
    },
    {
      label: "Weekday avg",
      value: summary.weekdayAvg ? `$${summary.weekdayAvg}` : "—",
    },
    {
      label: "Weekend avg",
      value: summary.weekendAvg ? `$${summary.weekendAvg}` : "—",
    },
    {
      label: "Monthly est.",
      value: summary.estimatedMonthlyRevenue
        ? `$${summary.estimatedMonthlyRevenue.toLocaleString()}`
        : "—",
    },
  ];

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-white shadow-sm">
      {/* ── Hero: price + CTAs ── */}
      <div className="flex flex-col gap-5 p-6 sm:flex-row sm:items-start sm:justify-between sm:p-7">
        {/* Left: price block */}
        <div className="space-y-2">
          <div>
            {/* When a worker-observed listing price is available, show it first
                as the primary pricing signal. The recommendation stays visible
                below as context. */}
            {observedListingPrice != null ? (
              <>
                <p className="text-xs font-semibold uppercase tracking-widest text-foreground/40">
                  Your listing price
                </p>
                <div className="mt-1.5 flex items-baseline gap-3">
                  <p className="text-5xl font-bold tracking-tight">
                    ${observedListingPrice}
                  </p>
                  {badge && (
                    <span
                      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${badge.color}`}
                    >
                      {badge.label}
                    </span>
                  )}
                </div>
                <div className="mt-2 space-y-0.5 text-sm text-foreground/50">
                  {median > 0 && (
                    <p>
                      Market median:{" "}
                      <span className="font-semibold text-foreground/70">${median}</span>
                    </p>
                  )}
                  <p>
                    Suggested:{" "}
                    <span className="font-semibold text-foreground/70">${recommended}</span>
                  </p>
                </div>
              </>
            ) : (
              <>
                {/* No observed listing price — show recommendation with explicit qualifier */}
                <p className="text-xs font-semibold uppercase tracking-widest text-foreground/40">
                  Suggested nightly rate
                </p>
                <div className="mt-1.5 flex items-baseline gap-3">
                  <p className="text-5xl font-bold tracking-tight">${recommended}</p>
                  {badge && (
                    <span
                      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${badge.color}`}
                    >
                      {badge.label}
                    </span>
                  )}
                </div>
                {median > 0 && recommended !== median && (
                  <p className="mt-1 text-sm text-foreground/50">
                    Market median:{" "}
                    <span className="font-semibold text-foreground/70">${median}</span>
                  </p>
                )}
              </>
            )}
          </div>

          <div className="space-y-0.5">
            {propertyMeta && (
              <p className="text-sm text-foreground/55">
                {propertyMeta.propertyType} · {propertyMeta.guests} guest
                {propertyMeta.guests !== 1 ? "s" : ""} · {propertyMeta.beds} bed
                {propertyMeta.beds !== 1 ? "s" : ""} · {propertyMeta.baths} bath
                {propertyMeta.baths !== 1 ? "s" : ""}
              </p>
            )}
            {benchmarkMeta?.count ? (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded-full bg-amber-100 px-2 py-0.5 font-semibold text-amber-800">
                  {benchmarkMeta.count} benchmark
                  {benchmarkMeta.count !== 1 ? "s" : ""}
                </span>
                {benchmarkMeta.primaryUrl && (
                  <a
                    href={benchmarkMeta.primaryUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="max-w-65 truncate text-amber-700 hover:underline"
                  >
                    Primary benchmark
                  </a>
                )}
              </div>
            ) : null}
            {lastAnalysisDate && (
              <p className="text-xs text-foreground/35">
                Analysis from {new Date(lastAnalysisDate).toLocaleDateString()}
              </p>
            )}
          </div>
        </div>

        {/* Right: CTAs */}
        <div className="flex shrink-0 flex-col gap-2 sm:items-end">
          <Link href={`/r/${reportShareId}`}>
            <Button size="md">View full report</Button>
          </Link>
          <Button
            size="sm"
            variant="secondary"
            onClick={onRerun}
            disabled={isRerunning}
          >
            {isRerunning ? "Re-analyzing…" : "Re-run analysis"}
          </Button>
          <p className="hidden text-right text-[11px] text-foreground/35 sm:block">
            {listingName}
          </p>
        </div>
      </div>

      {/* ── KPI stats row ── */}
      <div className="grid grid-cols-3 divide-x divide-y divide-border border-t border-border sm:grid-cols-5 sm:divide-y-0">
        {stats.map((stat) => (
          <div key={stat.label} className="px-4 py-3.5">
            <p className="text-[11px] font-medium uppercase tracking-wide text-foreground/40">
              {stat.label}
            </p>
            <p className="mt-0.5 text-base font-bold text-foreground">
              {stat.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
