import type {
  ReportSummary,
  CompsSummary,
  PriceDistribution,
} from "@/lib/schemas";

interface Alert {
  id: string;
  severity: "info" | "warning" | "danger" | "positive";
  title: string;
  description: string;
}

const SEVERITY_STYLES: Record<Alert["severity"], {
  card: string;
  icon: string;
  title: string;
  iconSymbol: string;
}> = {
  positive: {
    card: "bg-emerald-50 border-emerald-200",
    icon: "bg-emerald-500 text-white",
    title: "text-emerald-900",
    iconSymbol: "↑",
  },
  info: {
    card: "bg-blue-50 border-blue-200",
    icon: "bg-blue-500 text-white",
    title: "text-blue-900",
    iconSymbol: "i",
  },
  warning: {
    card: "bg-amber-50 border-amber-200",
    icon: "bg-amber-500 text-white",
    title: "text-amber-900",
    iconSymbol: "!",
  },
  danger: {
    card: "bg-rose-50 border-rose-200",
    icon: "bg-rose-500 text-white",
    title: "text-rose-900",
    iconSymbol: "↑",
  },
};

/**
 * Derive dashboard alerts.
 *
 * @param summary              Report summary from the pricing worker.
 * @param comps                Comparable listings summary.
 * @param dist                 Market price distribution.
 * @param observedListingPrice Real listing price captured by the background
 *   worker from the user's live Airbnb listing.  When present, market-position
 *   alerts are based on the observed price rather than the recommendation.
 *   null/undefined = worker has not yet observed the price; fall back to
 *   recommendation-vs-market wording.
 */
function deriveAlerts(
  summary: ReportSummary,
  comps: CompsSummary | null,
  dist: PriceDistribution | null,
  observedListingPrice: number | null | undefined
): Alert[] {
  const alerts: Alert[] = [];

  // 1. Weekend premium
  if (summary.weekendAvg && summary.weekdayAvg) {
    const premiumPct = Math.round(
      ((summary.weekendAvg - summary.weekdayAvg) / summary.weekdayAvg) * 100
    );
    if (premiumPct > 10) {
      alerts.push({
        id: "weekend-premium",
        severity: "positive",
        title: "Strong weekend demand",
        description: `Weekend rates are ${premiumPct}% higher than weekdays. Consider dynamic pricing to capture this premium.`,
      });
    }
  }

  // 2. Market-position alert.
  //
  //    Priority:
  //      a) observedListingPrice (worker-captured real price) — most accurate
  //      b) recommendedPrice.nightly (our suggestion)        — fallback
  //
  //    When (a) is available, messaging is about the user's actual listing.
  //    When only (b) is available, messaging is explicitly about the recommendation.
  if (summary.nightlyMedian) {
    const median = summary.nightlyMedian;

    if (observedListingPrice != null) {
      // Path A: observed listing price from worker
      const diff = Math.round(median - observedListingPrice);
      if (diff > 5) {
        alerts.push({
          id: "listing-under-market",
          severity: "warning",
          title: "Your listing is below market",
          description: `Your live listing price is $${diff} below the market median. You may be leaving revenue on the table.`,
        });
      } else if (diff < -10) {
        alerts.push({
          id: "listing-above-market",
          severity: "danger",
          title: "Your listing is above market",
          description: `Your live listing price is $${Math.abs(diff)} above the median. Ensure your amenities and reviews justify the premium.`,
        });
      }
    } else {
      // Path B: no observed price — compare recommendation vs market
      const recNightly = summary.recommendedPrice?.nightly;
      if (recNightly) {
        const diff = Math.round(median - recNightly);
        if (diff > 5) {
          alerts.push({
            id: "recommended-under-market",
            severity: "warning",
            title: "Recommended below market",
            description: `The suggested price is $${diff} below the market median. This may attract more bookings but reduce revenue.`,
          });
        } else if (diff < -10) {
          alerts.push({
            id: "recommended-above-market",
            severity: "danger",
            title: "Recommended above market",
            description: `The suggested price is $${Math.abs(diff)} above the median. Ensure your amenities and reviews justify the premium.`,
          });
        }
      }
    }
  }

  // 3. Comp scarcity
  if (comps && comps.collected < 10 && comps.filterStage !== "mock") {
    alerts.push({
      id: "comp-scarcity",
      severity: "warning",
      title: "Limited comparable data",
      description: `Only ${comps.collected} comparable listings were found. Results may be less accurate in this area.`,
    });
  }

  // 4. Price spread
  if (dist && dist.min != null && dist.max != null && dist.median) {
    const spread = (dist.max - dist.min) / dist.median;
    if (spread > 0.5) {
      alerts.push({
        id: "price-spread",
        severity: "info",
        title: "Wide price range",
        description: `Prices in your area range from $${dist.min} to $${dist.max}. Position based on your unique selling points.`,
      });
    }
  }

  // 5. Occupancy
  if (summary.occupancyPct && summary.occupancyPct < 60) {
    alerts.push({
      id: "low-occupancy",
      severity: "danger",
      title: "Lower occupancy expected",
      description: `Estimated occupancy is ${summary.occupancyPct}%. Consider lowering prices or improving listing quality.`,
    });
  }

  return alerts;
}

export function SmartAlerts({
  summary,
  compsSummary,
  priceDistribution,
  observedListingPrice,
}: {
  summary: ReportSummary;
  compsSummary: CompsSummary | null;
  priceDistribution: PriceDistribution | null;
  /**
   * Real listing price captured by the background worker from the user's
   * live Airbnb listing.  Drives market-position alerts when present.
   * Omit (or pass null) when not yet available.
   */
  observedListingPrice?: number | null;
}) {
  const alerts = deriveAlerts(summary, compsSummary, priceDistribution, observedListingPrice);

  return (
    <div className="rounded-2xl border border-border bg-white p-5 sm:p-6">
      <p className="mb-4 text-xs font-semibold uppercase tracking-widest text-foreground/35">
        Alerts
      </p>

      {alerts.length === 0 ? (
        <div className="flex items-center gap-3 rounded-xl bg-emerald-50 border border-emerald-200 px-4 py-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-xs font-bold text-white">
            ✓
          </span>
          <p className="text-sm font-medium text-emerald-900">
            No alerts — your pricing looks good!
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {alerts.map((alert) => {
            const s = SEVERITY_STYLES[alert.severity];
            return (
              <div
                key={alert.id}
                className={`flex items-start gap-3 rounded-xl border px-4 py-3.5 ${s.card}`}
              >
                <span
                  className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${s.icon}`}
                  aria-hidden="true"
                >
                  {s.iconSymbol}
                </span>
                <div>
                  <p className={`text-sm font-semibold ${s.title}`}>{alert.title}</p>
                  <p className="mt-0.5 text-sm text-foreground/60">{alert.description}</p>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
