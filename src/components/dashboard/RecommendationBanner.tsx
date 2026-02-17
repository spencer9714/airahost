import Link from "next/link";
import { Button } from "@/components/Button";
import type { ReportSummary, RecommendedPrice } from "@/lib/schemas";

interface Props {
  listingName: string;
  summary: ReportSummary;
  recommendedPrice: RecommendedPrice | null;
  reportShareId: string;
  onRerun: () => void;
  isRerunning: boolean;
  propertyMeta: {
    propertyType: string;
    guests: number;
    beds: number;
    baths: number;
  } | null;
  lastAnalysisDate: string | null;
}

function positionBadge(suggestedPrice: number, marketMedian: number) {
  if (marketMedian <= 0) return null;
  const pct = Math.round(((suggestedPrice / marketMedian) - 1) * 100);
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
  listingName,
  summary,
  recommendedPrice,
  reportShareId,
  onRerun,
  isRerunning,
  propertyMeta,
  lastAnalysisDate,
}: Props) {
  const suggested = recommendedPrice?.nightly ?? summary.nightlyMedian;
  const median = summary.nightlyMedian;
  const badge = positionBadge(suggested, median);

  return (
    <div className="rounded-2xl border border-border bg-white p-6 sm:p-8">
      <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-4">
          {/* Listing name + meta */}
          <div>
            <h3 className="text-lg font-bold tracking-tight">{listingName}</h3>
            {propertyMeta && (
              <p className="mt-1 text-sm text-foreground/70">
                {propertyMeta.propertyType} · {propertyMeta.guests} guest
                {propertyMeta.guests !== 1 ? "s" : ""} · {propertyMeta.beds} bed
                {propertyMeta.beds !== 1 ? "s" : ""} · {propertyMeta.baths} bath
                {propertyMeta.baths !== 1 ? "s" : ""}
              </p>
            )}
          </div>

          {/* Price display */}
          <div className="flex items-end gap-6">
            <div>
              <p className="text-sm font-medium uppercase tracking-wide text-foreground/60">
                Suggested nightly
              </p>
              <p className="text-4xl font-bold tracking-tight">${suggested}</p>
            </div>
            <div>
              <p className="text-sm font-medium uppercase tracking-wide text-foreground/60">
                Market median
              </p>
              <p className="text-2xl font-semibold tracking-tight text-foreground/70">
                ${median}
              </p>
            </div>
          </div>

          {/* Badge */}
          {badge && (
            <span
              className={`inline-block rounded-full border px-3 py-1 text-sm font-semibold ${badge.color}`}
            >
              {badge.label}
            </span>
          )}

          {/* Analysis date */}
          {lastAnalysisDate && (
            <p className="text-sm text-foreground/60">
              Based on analysis from{" "}
              {new Date(lastAnalysisDate).toLocaleDateString()}
            </p>
          )}
        </div>

        {/* Actions */}
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
    </div>
  );
}
