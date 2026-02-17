import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  pricingMode: "refundable" | "nonRefundable";
  onModeChange: (mode: "refundable" | "nonRefundable") => void;
}

function priceColor(price: number, min: number, max: number): string {
  if (max === min) return "text-foreground";
  const ratio = (price - min) / (max - min);
  if (ratio < 0.33) return "text-emerald-700";
  if (ratio < 0.66) return "text-amber-700";
  return "text-foreground font-bold";
}

export function PricingHeatmap({ calendar, pricingMode, onModeChange }: Props) {
  // Show first 14 days
  const days = calendar.slice(0, 14);
  if (days.length === 0) return null;

  const prices = days.map((d) => d.basePrice);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);

  return (
    <div className="rounded-2xl border border-border bg-white p-6 sm:p-8">
      <div className="mb-6 flex items-center justify-between">
        <h3 className="text-xl font-bold tracking-tight">14-day outlook</h3>
        <div className="inline-flex gap-1 rounded-xl border border-border bg-gray-100/80 p-1">
          <button
            onClick={() => onModeChange("refundable")}
            className={`rounded-lg px-4 py-2 text-sm font-semibold transition-all ${
              pricingMode === "refundable"
                ? "bg-white text-foreground shadow-sm"
                : "text-foreground/60 hover:text-foreground"
            }`}
          >
            Refundable
          </button>
          <button
            onClick={() => onModeChange("nonRefundable")}
            className={`rounded-lg px-4 py-2 text-sm font-semibold transition-all ${
              pricingMode === "nonRefundable"
                ? "bg-white text-foreground shadow-sm"
                : "text-foreground/60 hover:text-foreground"
            }`}
          >
            Non-refundable
          </button>
        </div>
      </div>

      <div className="space-y-1">
        {days.map((day) => {
          const price =
            pricingMode === "refundable"
              ? day.refundablePrice
              : day.nonRefundablePrice;
          const colorClass = priceColor(day.basePrice, minP, maxP);

          const d = new Date(day.date + "T00:00:00");
          const dayName = d.toLocaleString("en-US", { weekday: "short" });
          const monthDay = d.toLocaleString("en-US", {
            month: "short",
            day: "numeric",
          });

          return (
            <div
              key={day.date}
              className={`flex items-center justify-between rounded-xl px-4 py-3 ${
                day.isWeekend ? "bg-gray-50" : ""
              }`}
            >
              <div className="flex items-center gap-4">
                <span className="w-10 text-sm font-semibold text-foreground">
                  {dayName}
                </span>
                <span className="text-sm text-foreground/60">{monthDay}</span>
              </div>
              <span className={`text-lg font-bold tracking-tight ${colorClass}`}>
                ${price}
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-6 flex items-center justify-center gap-6 text-sm text-foreground/60">
        <span className="flex items-center gap-2">
          <span className="inline-block h-3 w-3 rounded-full bg-emerald-600" />
          Lower
        </span>
        <span className="flex items-center gap-2">
          <span className="inline-block h-3 w-3 rounded-full bg-amber-600" />
          Average
        </span>
        <span className="flex items-center gap-2">
          <span className="inline-block h-3 w-3 rounded-full bg-gray-400" />
          Higher
        </span>
      </div>
    </div>
  );
}
