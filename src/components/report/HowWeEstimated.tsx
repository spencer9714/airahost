import type { PricingReport } from "@/lib/schemas";
import { TargetSpecCard } from "./TargetSpecCard";
import { QueryCriteriaCard } from "./QueryCriteriaCard";
import { CompsDistributionCard } from "./CompsDistributionCard";
import { ComparableListingsSection } from "./ComparableListingsSection";

export function HowWeEstimated({ report }: { report: PricingReport }) {
  const target = report.targetSpec ?? report.resultSummary?.targetSpec;
  const criteria = report.queryCriteria ?? report.resultSummary?.queryCriteria;
  const comps = report.compsSummary ?? report.resultSummary?.compsSummary;
  const dist =
    report.priceDistribution ?? report.resultSummary?.priceDistribution;
  const comparableListings =
    report.comparableListings ?? report.resultSummary?.comparableListings;
  const usedForPricing = comps?.usedForPricing ?? 0;
  const availableComparableCount = comparableListings?.length ?? 0;
  const comparableCountLabel = availableComparableCount > 0
    ? `${availableComparableCount} available`
    : usedForPricing > 0
      ? `${usedForPricing} used in pricing`
      : "No comparable details";

  // Nothing to show for old reports without transparency data
  if (!target && !criteria && !comps && !comparableListings) {
    return null;
  }

  return (
    <section className="mb-8">
      <h2 className="mb-4 text-lg font-semibold">How we estimated your price</h2>

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
                Comparable listings
              </p>
              <p className="text-xs text-gray-500">
                Expand to view the listings used to estimate your price.
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
