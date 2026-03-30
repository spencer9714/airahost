import Link from "next/link";
import { Button } from "@/components/Button";
import type { ReportSummary, RecommendedPrice } from "@/lib/schemas";
// Button is used for "View full report" only — rerun button removed by product decision.

interface Props {
  summary: ReportSummary;
  recommendedPrice: RecommendedPrice | null;
  reportShareId: string;
  listingName: string;
  airbnbListingLabel?: string | null;
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
  /**
   * @deprecated — live price now comes directly from summary.observedListingPrice.
   * Kept for backward compatibility but summary fields take precedence.
   */
  observedListingPrice?: number | null;
}

// Compute a pricing position badge
function positionBadge(
  diffPct: number,
  source: "vs_market" | "vs_recommended"
): { label: string; color: string } {
  const abs = Math.abs(diffPct);
  const dir = diffPct > 0 ? "above" : "below";
  const dirLabel = dir === "above" ? "above" : "below";
  const subject = source === "vs_market" ? "market" : "recommendation";

  if (abs <= 3) {
    return {
      label: source === "vs_market" ? "At market" : "At recommendation",
      color: "bg-gray-100 text-gray-700 border-gray-300",
    };
  }
  if (dir === "above") {
    return {
      label: `${abs}% ${dirLabel} ${subject}`,
      color: "bg-amber-50 text-amber-800 border-amber-300",
    };
  }
  return {
    label: `${abs}% ${dirLabel} ${subject}`,
    color: "bg-emerald-50 text-emerald-800 border-emerald-300",
  };
}

// One-sentence intelligence summary
function intelligenceLine(summary: ReportSummary): string | null {
  const obs = summary.observedListingPrice;
  const mkt = summary.nightlyMedian;
  const rec = summary.recommendedPrice?.nightly ?? null;

  if (obs == null) return null;

  const parts: string[] = [];

  if (mkt && mkt > 0) {
    const diff = obs - mkt;
    if (Math.abs(diff) <= 3) {
      parts.push(`Your live price is at the market median ($${mkt}).`);
    } else {
      parts.push(
        `Your live price is $${Math.abs(Math.round(diff))} ${diff > 0 ? "above" : "below"} the market median ($${mkt}).`
      );
    }
  }

  if (rec && rec > 0) {
    const diff = obs - rec;
    if (Math.abs(diff) > 10) {
      parts.push(
        `${diff > 0 ? "Lower" : "Raise"} to $${Math.round(rec)} to align with our recommendation.`
      );
    }
  }

  return parts.join(" ") || null;
}

// Pricing action chip
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
        : "bg-amber-50 text-amber-800 border-amber-200",
  };
}

export function RecommendationBanner({
  summary,
  recommendedPrice,
  reportShareId,
  airbnbListingLabel,
  propertyMeta,
  benchmarkMeta,
  onManageBenchmarks,
  lastAnalysisDate,
  observedListingPrice: _legacyObs,
}: Props) {
  // Live price from summary takes precedence; prop is kept for backward compat.
  const observedPrice = summary.observedListingPrice ?? _legacyObs ?? null;
  const recommended = recommendedPrice?.nightly ?? summary.nightlyMedian;
  const median = summary.nightlyMedian;
  const livePriceStatus = summary.livePriceStatus ?? null;
  const hasListingUrl = livePriceStatus !== "no_listing_url";

  const intel = intelligenceLine(summary);
  const chip = actionChip(summary);

  const kpiStats = [
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
      {/* ── Hero ── */}
      <div className="flex flex-col gap-5 p-6 sm:flex-row sm:items-start sm:justify-between sm:p-7">

        {/* Left: pricing intelligence */}
        <div className="space-y-3 min-w-0">

          {observedPrice != null ? (
            /* ── LIVE PRICE available ── */
            <div className="space-y-1">
              <p className="text-sm font-semibold uppercase tracking-widest text-foreground/40">
                Your live price
              </p>
              <div className="flex flex-wrap items-baseline gap-3">
                <p className="text-5xl font-bold tracking-tight">${observedPrice}</p>
                {summary.observedVsMarketDiffPct != null && (
                  <span
                    className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                      positionBadge(summary.observedVsMarketDiffPct, "vs_market").color
                    }`}
                  >
                    {positionBadge(summary.observedVsMarketDiffPct, "vs_market").label}
                  </span>
                )}
              </div>

              {/* Three-way comparison table */}
              <div className="mt-2 space-y-1 text-sm">
                {median > 0 && (
                  <p className="text-foreground/50">
                    Market median:{" "}
                    <span className="font-semibold text-foreground/75">${median}</span>
                    {summary.observedVsMarketDiff != null && (
                      <span className={`ml-1.5 text-xs ${summary.observedVsMarketDiff > 0 ? "text-amber-600" : "text-emerald-600"}`}>
                        ({summary.observedVsMarketDiff > 0 ? "+" : ""}{summary.observedVsMarketDiff})
                      </span>
                    )}
                  </p>
                )}
                {recommended > 0 && recommended !== observedPrice && (
                  <p className="text-foreground/50">
                    Recommended:{" "}
                    <span className="font-semibold text-foreground/75">${recommended}</span>
                    {summary.observedVsRecommendedDiff != null && (
                      <span className={`ml-1.5 text-xs ${summary.observedVsRecommendedDiff > 0 ? "text-amber-600" : "text-blue-600"}`}>
                        ({summary.observedVsRecommendedDiff > 0 ? "+" : ""}{summary.observedVsRecommendedDiff})
                      </span>
                    )}
                  </p>
                )}
              </div>

              {/* Intelligence summary + action */}
              {intel && (
                <p className="mt-1 text-sm font-medium text-foreground/70">{intel}</p>
              )}
              {chip && (
                <span className={`inline-block rounded-lg border px-3 py-1 text-xs font-semibold ${chip.color}`}>
                  {chip.label}
                </span>
              )}

              {summary.observedListingPriceDate && (
                <p className="text-xs text-foreground/30">
                  Observed for {new Date(summary.observedListingPriceDate + "T00:00:00").toLocaleDateString()}
                  {summary.observedListingPriceConfidence && summary.observedListingPriceConfidence !== "failed" && (
                    <> · {summary.observedListingPriceConfidence} confidence</>
                  )}
                </p>
              )}
            </div>
          ) : (
            /* ── No live price ── */
            <div className="space-y-1.5">
              <p className="text-sm font-semibold uppercase tracking-widest text-foreground/40">
                Suggested nightly rate
              </p>
              <div className="flex flex-wrap items-baseline gap-3">
                <p className="text-5xl font-bold tracking-tight">${recommended}</p>
                {median > 0 && recommended !== median && (() => {
                  const pct = Math.round((recommended / median - 1) * 100);
                  const badge = positionBadge(pct, "vs_market");
                  return (
                    <span className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${badge.color}`}>
                      {badge.label}
                    </span>
                  );
                })()}
              </div>
              {median > 0 && recommended !== median && (
                <p className="text-sm text-foreground/50">
                  Market median:{" "}
                  <span className="font-semibold text-foreground/70">${median}</span>
                </p>
              )}

              {/* Live price unavailable — explain why */}
              <div className="mt-2 rounded-lg border border-gray-100 bg-gray-50/80 px-3 py-2.5">
                <p className="text-xs text-foreground/50">
                  {livePriceStatus === "no_listing_url" || !hasListingUrl
                    ? "Add your Airbnb listing URL in settings to see live pricing."
                    : livePriceStatus === "no_price_found"
                    ? "Live price not found for this date."
                    : livePriceStatus === "scrape_failed"
                    ? "Live price check failed — will retry tonight."
                    : "Live price not yet captured."}
                </p>
              </div>
            </div>
          )}

          {/* Property + benchmark meta */}
          <div className="space-y-1">
            {propertyMeta && (
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm text-foreground/55">
                  {propertyMeta.propertyType} · {propertyMeta.guests} guest
                  {propertyMeta.guests !== 1 ? "s" : ""} · {propertyMeta.beds} bed
                  {propertyMeta.beds !== 1 ? "s" : ""} · {propertyMeta.baths} bath
                  {propertyMeta.baths !== 1 ? "s" : ""}
                </p>
                {benchmarkMeta?.count ? (
                  <button
                    type="button"
                    onClick={onManageBenchmarks}
                    className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800 cursor-pointer hover:bg-amber-200 transition-colors"
                    title="Manage benchmark listings"
                  >
                    {benchmarkMeta.count} benchmark{benchmarkMeta.count !== 1 ? "s" : ""}
                  </button>
                ) : null}
                {benchmarkMeta?.primaryName && (
                  <span className="text-xs text-foreground/50">
                    vs {benchmarkMeta.primaryName}
                  </span>
                )}
              </div>
            )}
            {lastAnalysisDate && (
              <p className="text-sm text-foreground/35">
                Market data from {new Date(lastAnalysisDate).toLocaleDateString()}
              </p>
            )}
          </div>
        </div>

        {/* Right: CTAs */}
        <div className="flex shrink-0 flex-col gap-2 sm:items-end">
          <Link href={`/r/${reportShareId}`}>
            <Button size="md">View full report</Button>
          </Link>
          {airbnbListingLabel ? (
            <p className="max-w-[240px] text-right text-xs font-medium text-foreground/40">
              {airbnbListingLabel}
            </p>
          ) : null}
        </div>
      </div>

      {/* ── KPI stats row ── */}
      <div className="grid grid-cols-3 divide-x divide-y divide-border border-t border-border sm:grid-cols-5 sm:divide-y-0">
        {kpiStats.map((stat) => (
          <div key={stat.label} className="px-4 py-3.5">
            <p className="text-xs font-medium uppercase tracking-wide text-foreground/40">
              {stat.label}
            </p>
            <p className="mt-0.5 text-lg font-bold text-foreground">
              {stat.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
