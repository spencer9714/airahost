import Link from "next/link";
import type { ReportSummary, RecommendedPrice } from "@/lib/schemas";

interface Props {
  summary: ReportSummary;
  recommendedPrice: RecommendedPrice | null;
  reportShareId: string;
  listingName: string;
  airbnbListingLabel?: string | null;
  airbnbListingUrl?: string | null;
  propertyMeta: {
    propertyType: string;
    guests: number;
    beds: number;
    baths: number;
  } | null;
  benchmarkMeta?: {
    count: number;
    primaryUrl: string | null;
    primaryName?: string | null;
  } | null;
  onManageBenchmarks?: () => void;
  lastAnalysisDate: string | null;
  /** @deprecated — use summary.observedListingPrice */
  observedListingPrice?: number | null;
}

function actionChip(summary: ReportSummary): { label: string; color: string } | null {
  const action = summary.pricingAction;
  const target = summary.pricingActionTarget;
  if (!action || !target) return null;
  if (action === "keep") return null;
  return {
    label: action === "raise" ? `Raise to $${target}` : `Lower to $${target}`,
    color:
      action === "raise"
        ? "bg-blue-50 text-blue-700 border-blue-200"
        : "bg-amber-50 text-amber-700 border-amber-200",
  };
}

function vsMarketColor(diff: number): string {
  if (Math.abs(diff) <= 3) return "text-foreground/40";
  return diff > 0 ? "text-amber-600/80" : "text-emerald-600/80";
}

export function RecommendationBanner({
  summary,
  recommendedPrice,
  reportShareId,
  airbnbListingLabel,
  airbnbListingUrl,
  benchmarkMeta,
  onManageBenchmarks,
  observedListingPrice: _legacyObs,
}: Props) {
  const observedPrice = summary.observedListingPrice ?? _legacyObs ?? null;
  const recommended = recommendedPrice?.nightly ?? summary.nightlyMedian;
  const median = summary.nightlyMedian;
  const livePriceStatus = summary.livePriceStatus ?? null;

  const chip = actionChip(summary);

  const displayPrice = observedPrice ?? recommended;
  const priceLabel = observedPrice != null ? "Live price" : "Suggested rate";

  const kpiStats = [
    { label: "Market median", value: median ? `$${median}` : "—" },
    { label: "Occupancy est.", value: summary.occupancyPct ? `${summary.occupancyPct}%` : "—" },
    { label: "Weekday avg", value: summary.weekdayAvg ? `$${summary.weekdayAvg}` : "—" },
    { label: "Weekend avg", value: summary.weekendAvg ? `$${summary.weekendAvg}` : "—" },
    {
      label: "Monthly est.",
      value: summary.estimatedMonthlyRevenue
        ? `$${summary.estimatedMonthlyRevenue.toLocaleString()}`
        : "—",
    },
  ];

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-white">
      {/* ── Hero ── */}
      <div className="px-4 py-4 sm:px-6 sm:py-5">
        <div className="flex items-start justify-between gap-3 sm:gap-6">

          {/* Left: focal price */}
          <div className="min-w-0">
            <p className="mb-1.5 text-xs text-foreground/40">{priceLabel}</p>

            <div className="flex flex-wrap items-baseline gap-3">
              <span className="text-4xl font-bold tracking-tight">${displayPrice}</span>

              {/* Action chip — primary recommendation */}
              {chip && (
                <span className={`inline-block rounded-lg border px-2.5 py-1 text-xs font-semibold ${chip.color}`}>
                  {chip.label}
                </span>
              )}

              {/* Position badge when no action chip */}
              {!chip && observedPrice != null && summary.observedVsMarketDiffPct != null && (
                (() => {
                  const pct = summary.observedVsMarketDiffPct;
                  const abs = Math.abs(pct);
                  const label =
                    abs <= 3
                      ? "At market"
                      : `${abs}% ${pct > 0 ? "above" : "below"} market`;
                  const color =
                    abs <= 3
                      ? "bg-gray-100 text-gray-600 border-gray-200"
                      : pct > 0
                      ? "bg-amber-50 text-amber-700 border-amber-200"
                      : "bg-emerald-50 text-emerald-700 border-emerald-200";
                  return (
                    <span className={`inline-block rounded-lg border px-2.5 py-1 text-xs font-semibold ${color}`}>
                      {label}
                    </span>
                  );
                })()
              )}
            </div>

            {/* One quiet comparison line */}
            {observedPrice != null && median > 0 && (
              <p className="mt-2 text-sm text-foreground/45">
                Market median{" "}
                <span className="font-medium text-foreground/60">${median}</span>
                {summary.observedVsMarketDiff != null && (
                  <span className={`ml-1.5 text-xs ${vsMarketColor(summary.observedVsMarketDiff)}`}>
                    ({summary.observedVsMarketDiff > 0 ? "+" : ""}{summary.observedVsMarketDiff})
                  </span>
                )}
                {recommended > 0 && recommended !== observedPrice && (
                  <>
                    {" · "}recommended{" "}
                    <span className="font-medium text-foreground/60">${recommended}</span>
                  </>
                )}
              </p>
            )}

            {/* No live price — quiet status note */}
            {observedPrice == null && (
              <p className="mt-2 text-xs text-foreground/35">
                {livePriceStatus === "no_listing_url"
                  ? "Add your Airbnb URL in settings to track live pricing."
                  : livePriceStatus === "no_price_found"
                  ? "Live price not found for today."
                  : livePriceStatus === "scrape_failed"
                  ? "Live price check failed — retrying tonight."
                  : "Live price not yet captured."}
              </p>
            )}
          </div>

          {/* Right: links */}
          <div className="flex shrink-0 flex-col items-end gap-2">
            <Link
              href={`/r/${reportShareId}`}
              className="text-sm font-medium text-accent transition-colors hover:underline"
            >
              Full report →
            </Link>
            {airbnbListingLabel && (
              airbnbListingUrl ? (
                <a
                  href={airbnbListingUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-foreground/30 transition-colors hover:text-foreground/55"
                >
                  {airbnbListingLabel} ↗
                </a>
              ) : (
                <p className="text-xs text-foreground/30">{airbnbListingLabel}</p>
              )
            )}
            {benchmarkMeta?.count ? (
              <button
                type="button"
                onClick={onManageBenchmarks}
                className="text-xs text-amber-700/60 transition-colors hover:text-amber-700"
              >
                {benchmarkMeta.count} benchmark{benchmarkMeta.count !== 1 ? "s" : ""} ↗
              </button>
            ) : null}
          </div>
        </div>
      </div>

      {/* ── KPI strip ── */}
      <div className="grid grid-cols-2 divide-x divide-border/50 border-t border-border/50 sm:grid-cols-3 md:grid-cols-5">
        {kpiStats.map((stat) => (
          <div key={stat.label} className="px-3 py-2.5 sm:px-4 sm:py-3">
            <p className="text-[11px] text-foreground/35">{stat.label}</p>
            <p className="mt-0.5 text-sm font-semibold text-foreground/70">{stat.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
