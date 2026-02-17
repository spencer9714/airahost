import { Card } from "@/components/Card";
import type { CompsSummary, PriceDistribution } from "@/lib/schemas";

const STAGE_BADGES: Record<string, { label: string; color: string }> = {
  day_by_day: { label: "Live market data", color: "bg-sky-50 text-sky-700" },
  strict: { label: "Strict match", color: "bg-emerald-50 text-emerald-700" },
  medium: { label: "Medium match", color: "bg-amber-50 text-amber-700" },
  fallback_all: { label: "Broad match", color: "bg-rose-50 text-rose-700" },
  mock: { label: "Modeled", color: "bg-gray-100 text-gray-600" },
  empty: { label: "No data", color: "bg-gray-100 text-gray-500" },
};

function DistributionBar({ dist }: { dist: PriceDistribution }) {
  const min = dist.min ?? 0;
  const max = dist.max ?? 0;
  const p25 = dist.p25 ?? min;
  const p75 = dist.p75 ?? max;
  const median = dist.median ?? 0;
  const range = max - min;

  if (range <= 0) {
    return (
      <p className="text-sm text-muted">
        {median > 0 ? `Median: $${median}` : "No distribution data available."}
      </p>
    );
  }

  const p25Pct = ((p25 - min) / range) * 100;
  const p75Pct = ((p75 - min) / range) * 100;
  const medianPct = ((median - min) / range) * 100;

  return (
    <div>
      {/* Bar */}
      <div className="relative mt-2 h-6 w-full rounded-full bg-gray-100">
        {/* P25-P75 range */}
        <div
          className="absolute top-0 h-full rounded-full bg-accent/20"
          style={{ left: `${p25Pct}%`, width: `${p75Pct - p25Pct}%` }}
        />
        {/* Median marker */}
        <div
          className="absolute top-0 h-full w-0.5 bg-accent"
          style={{ left: `${medianPct}%` }}
        />
      </div>

      {/* Labels */}
      <div className="mt-1.5 flex justify-between text-xs text-muted">
        <span>${min}</span>
        {dist.p25 != null && <span>p25: ${p25}</span>}
        <span className="font-medium text-foreground">
          Median: ${median}
        </span>
        {dist.p75 != null && <span>p75: ${p75}</span>}
        <span>${max}</span>
      </div>
    </div>
  );
}

export function CompsDistributionCard({
  comps,
  distribution,
}: {
  comps: CompsSummary;
  distribution: PriceDistribution;
}) {
  const badge = STAGE_BADGES[comps.filterStage] ?? STAGE_BADGES.empty;

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted">
          Pricing data quality
        </h3>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${badge.color}`}
        >
          {badge.label}
        </span>
      </div>

      {/* Funnel */}
      <div className="mb-4 flex items-center gap-2 text-sm">
        <div className="rounded-lg border border-border px-3 py-1.5 text-center">
          <p className="text-lg font-bold">{comps.collected}</p>
          <p className="text-xs text-muted">Collected</p>
        </div>
        <span className="text-muted">&rarr;</span>
        <div className="rounded-lg border border-border px-3 py-1.5 text-center">
          <p className="text-lg font-bold">{comps.afterFiltering}</p>
          <p className="text-xs text-muted">Filtered</p>
        </div>
        <span className="text-muted">&rarr;</span>
        <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-1.5 text-center">
          <p className="text-lg font-bold">{comps.usedForPricing}</p>
          <p className="text-xs text-muted">Used</p>
        </div>
      </div>

      {/* Similarity scores */}
      {comps.topSimilarity != null && (
        <div className="mb-3 flex gap-4 text-xs text-muted">
          <span>
            Top similarity:{" "}
            <strong className="text-foreground">
              {Math.round(comps.topSimilarity * 100)}%
            </strong>
          </span>
          {comps.avgSimilarity != null && (
            <span>
              Avg:{" "}
              <strong className="text-foreground">
                {Math.round(comps.avgSimilarity * 100)}%
              </strong>
            </span>
          )}
        </div>
      )}

      {/* Price distribution */}
      <h4 className="mb-1 text-xs font-medium text-muted">
        Price distribution ({distribution.currency})
      </h4>
      <DistributionBar dist={distribution} />

      <p className="mt-3 text-xs text-muted">
        Prices reflect nightly rates of comparable listings in the same area for
        your selected dates. Actual pricing may vary.
      </p>
    </Card>
  );
}
