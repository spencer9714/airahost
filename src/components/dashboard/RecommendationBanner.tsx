import Link from "next/link";
import { Card } from "@/components/Card";
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
      color: "bg-emerald-50 text-emerald-700 border-emerald-200",
    };
  }
  if (pct > 3) {
    return {
      label: `${pct}% above market`,
      color: "bg-amber-50 text-amber-700 border-amber-200",
    };
  }
  return {
    label: "At market",
    color: "bg-gray-50 text-gray-600 border-gray-200",
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
    <Card className="border-accent/20 bg-accent/[0.02]">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-bold">{listingName}</p>

          {propertyMeta && (
            <p className="mt-0.5 text-xs text-muted">
              {propertyMeta.propertyType} · {propertyMeta.guests} guest
              {propertyMeta.guests !== 1 ? "s" : ""} · {propertyMeta.beds} bed
              {propertyMeta.beds !== 1 ? "s" : ""} · {propertyMeta.baths} bath
              {propertyMeta.baths !== 1 ? "s" : ""}
            </p>
          )}

          <div className="mt-3 flex items-baseline gap-4">
            <div>
              <p className="text-3xl font-bold">${suggested}</p>
              <p className="text-xs text-muted">Suggested nightly</p>
            </div>
            <div className="text-center">
              <p className="text-lg font-semibold text-muted">${median}</p>
              <p className="text-xs text-muted">Market median</p>
            </div>
          </div>

          {badge && (
            <span
              className={`mt-2 inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${badge.color}`}
            >
              {badge.label}
            </span>
          )}

          {lastAnalysisDate && (
            <p className="mt-2 text-xs text-muted">
              Based on your last analysis on{" "}
              {new Date(lastAnalysisDate).toLocaleDateString()}
            </p>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <Link href={`/r/${reportShareId}`}>
            <Button size="sm">View full report</Button>
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
    </Card>
  );
}
