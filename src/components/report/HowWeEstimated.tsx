import type { BenchmarkInfo, PricingReport } from "@/lib/schemas";
import { TargetSpecCard } from "./TargetSpecCard";
import { QueryCriteriaCard } from "./QueryCriteriaCard";
import { CompsDistributionCard } from "./CompsDistributionCard";
import { ComparableListingsSection } from "./ComparableListingsSection";

// ── Benchmark transparency block ─────────────────────────────────

function BenchmarkBlock({ info }: { info: BenchmarkInfo }) {
  const statusUsed = info.benchmarkUsed && info.benchmarkFetchStatus !== "failed";
  const statusColor = statusUsed
    ? "border-emerald-200 bg-emerald-50"
    : "border-amber-200 bg-amber-50";
  const badgeColor = statusUsed
    ? "bg-emerald-100 text-emerald-800"
    : "bg-amber-100 text-amber-800";

  const fetchLabel =
    info.benchmarkFetchStatus === "search_hit"
      ? "Found in search results"
      : info.benchmarkFetchStatus === "direct_page"
        ? "Fetched from listing page"
        : "Fetch failed";

  const adjSign =
    info.marketAdjustmentPct != null && info.marketAdjustmentPct > 0 ? "+" : "";

  return (
    <div className={`mb-4 rounded-xl border p-4 ${statusColor}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-gray-900">
            Your benchmark listing
          </p>
          {info.benchmarkUrl && (
            <a
              href={info.benchmarkUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-0.5 block max-w-xs truncate text-xs text-accent hover:underline"
            >
              {info.benchmarkUrl}
            </a>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <span className="rounded-full bg-gray-900 px-2.5 py-1 text-[10px] font-semibold text-white">
            Pinned by you
          </span>
          <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${badgeColor}`}>
            {statusUsed ? "Used as primary benchmark" : "Benchmark fetch failed — fallback to market comps"}
          </span>
        </div>
      </div>

      {statusUsed && (
        <div className="mt-3 grid grid-cols-2 gap-3 border-t border-emerald-200 pt-3 sm:grid-cols-4">
          {info.avgBenchmarkPrice != null && (
            <div>
              <p className="text-[10px] text-gray-500">Benchmark avg</p>
              <p className="text-sm font-semibold">${info.avgBenchmarkPrice}/night</p>
            </div>
          )}
          {info.avgMarketPrice != null && (
            <div>
              <p className="text-[10px] text-gray-500">Market avg</p>
              <p className="text-sm font-semibold">${info.avgMarketPrice}/night</p>
            </div>
          )}
          {info.marketAdjustmentPct != null && (
            <div>
              <p className="text-[10px] text-gray-500">Market offset</p>
              <p className="text-sm font-semibold">
                {adjSign}{info.marketAdjustmentPct}%
              </p>
            </div>
          )}
          <div>
            <p className="text-[10px] text-gray-500">Fetch method</p>
            <p className="text-sm font-semibold">{fetchLabel}</p>
          </div>
        </div>
      )}

      {!statusUsed && info.fallbackReason && (
        <p className="mt-2 text-xs text-amber-700">
          Fallback reason: {info.fallbackReason.replace(/_/g, " ")}. Market comps were used instead.
        </p>
      )}

      <p className="mt-2 text-[10px] text-gray-500">
        Market adjustment applied at {Math.round((info.appliedMarketWeight ?? 0.3) * 100)}% weight,
        capped at ±{Math.round((info.maxAdjCap ?? 0.25) * 100)}%.
        Benchmark price stays dominant.
      </p>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────

export function HowWeEstimated({ report }: { report: PricingReport }) {
  const target = report.targetSpec ?? report.resultSummary?.targetSpec;
  const criteria = report.queryCriteria ?? report.resultSummary?.queryCriteria;
  const comps = report.compsSummary ?? report.resultSummary?.compsSummary;
  const dist =
    report.priceDistribution ?? report.resultSummary?.priceDistribution;
  const comparableListings =
    report.comparableListings ?? report.resultSummary?.comparableListings;
  const benchmarkInfo =
    report.benchmarkInfo ?? report.resultSummary?.benchmarkInfo ?? null;

  const usedForPricing = comps?.usedForPricing ?? 0;
  const availableComparableCount = comparableListings?.length ?? 0;
  const comparableCountLabel = availableComparableCount > 0
    ? `${availableComparableCount} available`
    : usedForPricing > 0
      ? `${usedForPricing} used in pricing`
      : "No comparable details";

  // Nothing to show for old reports without transparency data
  if (!target && !criteria && !comps && !comparableListings && !benchmarkInfo) {
    return null;
  }

  // Pinned comp URLs (from report input) to mark in comparable list
  const pinnedUrls: string[] = (() => {
    const compsArr = report.inputAttributes?.preferredComps;
    if (!Array.isArray(compsArr)) return [];
    return compsArr
      .filter((c) => c.enabled !== false && c.listingUrl)
      .map((c) => c.listingUrl);
  })();

  return (
    <section className="mb-8">
      <h2 className="mb-4 text-lg font-semibold">How we estimated your price</h2>

      {/* Benchmark block — shown first when present */}
      {benchmarkInfo && <BenchmarkBlock info={benchmarkInfo} />}

      <div className="mb-4 grid gap-4 md:grid-cols-2">
        {target && <TargetSpecCard spec={target} />}
        {criteria && <QueryCriteriaCard criteria={criteria} />}
      </div>

      {comps && dist && (
        <CompsDistributionCard comps={comps} distribution={dist} />
      )}

      {(availableComparableCount > 0 || usedForPricing > 0) && (
        <details className="group mt-4 overflow-hidden rounded-xl border border-gray-200 bg-white">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-gray-900">
                {benchmarkInfo?.benchmarkUsed
                  ? "Market validation comps"
                  : "Comparable listings"}
              </p>
              <p className="text-xs text-gray-500">
                {benchmarkInfo?.benchmarkUsed
                  ? "These market comps were used to validate and adjust your benchmark price."
                  : "Expand to view the listings used to estimate your price."}
              </p>
            </div>
            <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
              {comparableCountLabel}
            </span>
          </summary>
          <div className="border-t border-gray-100 px-4 py-4">
            <ComparableListingsSection
              listings={comparableListings ?? null}
              comps={comps ?? null}
              pinnedUrls={pinnedUrls}
              embedded
            />
          </div>
        </details>
      )}

      {report.recommendedPrice?.notes &&
        report.recommendedPrice.notes !== "" && (
          <p className="mt-3 text-xs text-muted italic">
            {report.recommendedPrice.notes}
          </p>
        )}
    </section>
  );
}
