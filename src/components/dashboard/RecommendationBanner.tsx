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
}

function positionBadge(suggestedPrice: number, marketMedian: number) {
  if (marketMedian <= 0) return null;
  const pct = Math.round((suggestedPrice / marketMedian - 1) * 100);
  if (pct < -3) {
    return {
      label: `${Math.abs(pct)}% below market`,
      color: "bg-emerald-50 text-emerald-800 border-emerald-300",
    };
  }
  if (pct > 3) {
    return {
      label: `${pct}% above market`,
      color: "bg-amber-50 text-amber-800 border-amber-300",
    };
  }
  return {
    label: "At market",
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
}: Props) {
  const suggested = recommendedPrice?.nightly ?? summary.nightlyMedian;
  const median = summary.nightlyMedian;
  const badge = positionBadge(suggested, median);

  const stats = [
    {
      label: "Market median",
      value: median ? `$${median}` : "-",
    },
    {
      label: "Occupancy est.",
      value: summary.occupancyPct ? `${summary.occupancyPct}%` : "-",
    },
    {
      label: "Monthly est.",
      value: summary.estimatedMonthlyRevenue
        ? `$${summary.estimatedMonthlyRevenue.toLocaleString()}`
        : "-",
    },
    {
      label: "Weekend avg",
      value: summary.weekendAvg ? `$${summary.weekendAvg}` : "-",
    },
  ];

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-white shadow-sm">
      <div className="flex flex-col gap-6 p-6 sm:flex-row sm:items-start sm:justify-between sm:p-7">
        <div className="space-y-3">
          <div>
            <p className="text-sm font-semibold text-foreground/65">
              {listingName}
            </p>
            <p className="text-xs font-semibold uppercase tracking-widest text-foreground/40">
              Suggested nightly rate
            </p>
            <div className="mt-1 flex items-baseline gap-3">
              <p className="text-5xl font-bold tracking-tight">${suggested}</p>
              {badge && (
                <span
                  className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${badge.color}`}
                >
                  {badge.label}
                </span>
              )}
            </div>
          </div>

          <div className="space-y-1">
            {propertyMeta && (
              <p className="text-sm text-foreground/60">
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
                    className="max-w-[280px] truncate text-amber-700 hover:underline"
                  >
                    Primary benchmark
                  </a>
                )}
              </div>
            ) : null}
            {lastAnalysisDate && (
              <p className="text-xs text-foreground/40">
                Analysis from {new Date(lastAnalysisDate).toLocaleDateString()}
              </p>
            )}
          </div>
        </div>

        <div className="flex shrink-0 flex-col gap-2">
          <Link href={`/r/${reportShareId}`}>
            <Button size="md">View full report</Button>
          </Link>
          <Button
            size="sm"
            variant="ghost"
            onClick={onRerun}
            disabled={isRerunning}
          >
            {isRerunning ? "Re-analyzing..." : "Re-run analysis"}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 divide-x divide-y divide-border border-t border-border sm:grid-cols-4 sm:divide-y-0">
        {stats.map((stat) => (
          <div key={stat.label} className="px-5 py-4">
            <p className="text-xs text-foreground/40">{stat.label}</p>
            <p className="mt-0.5 text-base font-semibold text-foreground">
              {stat.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
