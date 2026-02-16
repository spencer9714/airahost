import { Card } from "@/components/Card";
import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  pricingMode: "refundable" | "nonRefundable";
  onModeChange: (mode: "refundable" | "nonRefundable") => void;
}

function priceColor(price: number, min: number, max: number): string {
  if (max === min) return "bg-accent/5 text-foreground";
  const ratio = (price - min) / (max - min);
  if (ratio < 0.33) return "bg-emerald-50 text-emerald-700";
  if (ratio < 0.66) return "bg-amber-50 text-amber-700";
  return "bg-rose-50 text-rose-700";
}

export function PricingHeatmap({ calendar, pricingMode, onModeChange }: Props) {
  // Show first 14 days
  const days = calendar.slice(0, 14);
  if (days.length === 0) return null;

  const prices = days.map((d) => d.basePrice);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);

  return (
    <Card>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold">14-day pricing outlook</h3>
        <div className="flex gap-1 rounded-lg border border-border p-0.5">
          <button
            onClick={() => onModeChange("refundable")}
            className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
              pricingMode === "refundable"
                ? "bg-foreground text-white"
                : "text-muted hover:text-foreground"
            }`}
          >
            Refundable
          </button>
          <button
            onClick={() => onModeChange("nonRefundable")}
            className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
              pricingMode === "nonRefundable"
                ? "bg-foreground text-white"
                : "text-muted hover:text-foreground"
            }`}
          >
            Non-refundable
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <div className="flex gap-1.5" style={{ minWidth: days.length * 60 }}>
          {days.map((day) => {
            const price =
              pricingMode === "refundable"
                ? day.refundablePrice
                : day.nonRefundablePrice;
            const colorClass = priceColor(day.basePrice, minP, maxP);

            // Parse date for display
            const d = new Date(day.date + "T00:00:00");
            const dayNum = d.getDate();
            const monthShort = d.toLocaleString("en-US", { month: "short" });

            return (
              <div
                key={day.date}
                className={`flex min-w-[54px] flex-col items-center rounded-xl border px-2 py-2 ${
                  day.isWeekend ? "border-accent/30" : "border-border/60"
                }`}
              >
                <span className="text-[10px] font-medium text-muted">
                  {day.dayOfWeek}
                </span>
                <span className="text-[10px] text-muted">
                  {monthShort} {dayNum}
                </span>
                <span
                  className={`mt-1 rounded-md px-1.5 py-0.5 text-xs font-semibold ${colorClass}`}
                >
                  ${price}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-center gap-4 text-xs text-muted">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded bg-emerald-50 border border-emerald-200" />
          Lower
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded bg-amber-50 border border-amber-200" />
          Average
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded bg-rose-50 border border-rose-200" />
          Higher
        </span>
      </div>
    </Card>
  );
}
