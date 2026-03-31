import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  pricingMode: "refundable" | "nonRefundable";
  onModeChange: (mode: "refundable" | "nonRefundable") => void;
}


function tileBg(_ratio: number, isWeekend: boolean): string {
  return isWeekend ? "bg-gray-50/70 border-gray-100/80" : "bg-white border-gray-100/60";
}

function tilePrice(ratio: number): string {
  if (ratio < 0.33) return "text-emerald-700/80";
  if (ratio < 0.66) return "text-foreground/70";
  return "text-indigo-600/85";
}

export function PricingHeatmap({ calendar, pricingMode, onModeChange }: Props) {
  const days = calendar;
  if (days.length === 0) return null;

  const basePrices = days.map((d) => d.basePrice);
  const minP = Math.min(...basePrices);
  const maxP = Math.max(...basePrices);

  const displayPrices = days.map((d) =>
    pricingMode === "refundable" ? d.refundablePrice : d.nonRefundablePrice
  );

  return (
    <div className="rounded-2xl border border-border bg-white p-5 sm:p-6">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h3 className="text-base font-bold tracking-tight">30-Day Market Board</h3>
        </div>
        <div className="inline-flex gap-0.5 rounded-lg border border-border bg-gray-100/80 p-0.5">
          <button
            onClick={() => onModeChange("refundable")}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${
              pricingMode === "refundable"
                ? "bg-white text-foreground shadow-sm"
                : "text-foreground/55 hover:text-foreground"
            }`}
          >
            Refundable
          </button>
          <button
            onClick={() => onModeChange("nonRefundable")}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${
              pricingMode === "nonRefundable"
                ? "bg-white text-foreground shadow-sm"
                : "text-foreground/55 hover:text-foreground"
            }`}
          >
            Non-refund.
          </button>
        </div>
      </div>

      {/* Tile grid: 2 cols mobile → 7 cols on lg (weekly rows) */}
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4 lg:grid-cols-7">
        {days.map((day, i) => {
          const price = displayPrices[i];
          const ratio = maxP === minP ? 0.5 : (day.basePrice - minP) / (maxP - minP);
          const d = new Date(day.date + "T00:00:00");
          const dayName = d.toLocaleString("en-US", { weekday: "short" });
          const monthDay = d.toLocaleString("en-US", { month: "short", day: "numeric" });

          return (
            <div
              key={day.date}
              className={`rounded-xl border p-2 ${tileBg(ratio, day.isWeekend)}`}
            >
              <p className="text-xs font-medium text-foreground/50">{dayName}</p>
              <p className="text-[10px] text-foreground/35">{monthDay}</p>
              <p className={`mt-1.5 text-sm font-bold leading-tight ${tilePrice(ratio)}`}>
                ${price}
              </p>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="mt-4 flex items-center gap-5 text-xs text-foreground/40">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-emerald-500/60" />
          Lower
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-gray-400/60" />
          Mid
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-indigo-500/60" />
          Higher
        </span>
        <span className="ml-auto flex items-center gap-1.5 text-foreground/30">
          <span className="h-2 w-2 rounded-sm bg-gray-200" />
          Weekend
        </span>
      </div>
    </div>
  );
}
