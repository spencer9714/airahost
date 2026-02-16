import type { PricingReport } from "@/lib/schemas";
import { TargetSpecCard } from "./TargetSpecCard";
import { QueryCriteriaCard } from "./QueryCriteriaCard";
import { CompsDistributionCard } from "./CompsDistributionCard";

export function HowWeEstimated({ report }: { report: PricingReport }) {
  const target = report.targetSpec ?? report.resultSummary?.targetSpec;
  const criteria = report.queryCriteria ?? report.resultSummary?.queryCriteria;
  const comps = report.compsSummary ?? report.resultSummary?.compsSummary;
  const dist =
    report.priceDistribution ?? report.resultSummary?.priceDistribution;

  // Nothing to show for old reports without transparency data
  if (!target && !criteria && !comps) {
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

      {report.recommendedPrice?.notes &&
        report.recommendedPrice.notes !== "" && (
          <p className="mt-3 text-xs text-muted italic">
            {report.recommendedPrice.notes}
          </p>
        )}
    </section>
  );
}
