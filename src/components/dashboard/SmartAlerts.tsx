import { Card } from "@/components/Card";
import type {
  ReportSummary,
  CompsSummary,
  PriceDistribution,
} from "@/lib/schemas";

interface Alert {
  id: string;
  color: "emerald" | "amber" | "rose" | "blue";
  title: string;
  description: string;
}

const DOT_COLORS = {
  emerald: "bg-emerald-500",
  amber: "bg-amber-500",
  rose: "bg-rose-500",
  blue: "bg-blue-500",
};

function deriveAlerts(
  summary: ReportSummary,
  comps: CompsSummary | null,
  dist: PriceDistribution | null
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
        color: "emerald",
        title: "Strong weekend demand",
        description: `Weekend rates are ${premiumPct}% higher than weekdays. Consider dynamic pricing to capture this premium.`,
      });
    }
  }

  // 2. Under-market
  const recNightly = summary.recommendedPrice?.nightly;
  if (recNightly && summary.nightlyMedian) {
    const diff = Math.round(summary.nightlyMedian - recNightly);
    if (diff > 5) {
      alerts.push({
        id: "under-market",
        color: "amber",
        title: "Priced below market",
        description: `Your recommended price is $${diff} below the market median. This may attract more bookings but reduce revenue.`,
      });
    } else if (diff < -10) {
      alerts.push({
        id: "above-market",
        color: "rose",
        title: "Priced above market",
        description: `Your recommended price is $${Math.abs(diff)} above the median. Ensure your amenities and reviews justify the premium.`,
      });
    }
  }

  // 3. Comp scarcity
  if (comps && comps.collected < 10 && comps.filterStage !== "mock") {
    alerts.push({
      id: "comp-scarcity",
      color: "amber",
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
        color: "blue",
        title: "Wide price range",
        description: `Prices in your area range from $${dist.min} to $${dist.max}. Position based on your unique selling points.`,
      });
    }
  }

  // 5. Occupancy
  if (summary.occupancyPct && summary.occupancyPct < 60) {
    alerts.push({
      id: "low-occupancy",
      color: "rose",
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
}: {
  summary: ReportSummary;
  compsSummary: CompsSummary | null;
  priceDistribution: PriceDistribution | null;
}) {
  const alerts = deriveAlerts(summary, compsSummary, priceDistribution);

  if (alerts.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted">
          No alerts right now. Your pricing looks good!
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-2">
      {alerts.map((alert) => (
        <Card key={alert.id} className="py-3">
          <div className="flex items-start gap-3">
            <span
              className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${DOT_COLORS[alert.color]}`}
            />
            <div>
              <p className="text-sm font-medium">{alert.title}</p>
              <p className="mt-0.5 text-xs text-muted">{alert.description}</p>
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}
