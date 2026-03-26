import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  pricingMode: "refundable" | "nonRefundable";
  onModeChange: (mode: "refundable" | "nonRefundable") => void;
}

function Sparkline({ values, min, max }: { values: number[]; min: number; max: number }) {
  if (values.length < 2) return null;
  const W = 80;
  const H = 24;
  const range = max - min || 1;
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * W;
      const y = H - ((v - min) / range) * H;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      className="text-accent/70 overflow-visible"
      aria-hidden="true"
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function tileBg(ratio: number, isWeekend: boolean): string {
  if (ratio < 0.33) return isWeekend ? "bg-emerald-100/80 border-emerald-200/70" : "bg-emerald-50/70 border-emerald-100/80";
  if (ratio < 0.66) return isWeekend ? "bg-sky-100/80 border-sky-200/70" : "bg-sky-50/70 border-sky-100/80";
  return isWeekend ? "bg-indigo-100/80 border-indigo-200/70" : "bg-indigo-50/70 border-indigo-100/80";
}

function tilePrice(ratio: number): string {
  if (ratio < 0.33) return "text-emerald-700";
  if (ratio < 0.66) return "text-sky-700";
  return "text-indigo-700";
}

export function PricingHeatmap({ calendar, pricingMode, onModeChange }: Props) {
  const days = calendar.slice(0, 14);
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
          <h3 className="text-base font-bold tracking-tight">14-day outlook</h3>
          <Sparkline values={basePrices} min={minP} max={maxP} />
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

      {/* Tile grid: 2 cols mobile → 7 cols on lg (2 perfect weeks) */}
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
              className={`rounded-xl border p-2.5 ${tileBg(ratio, day.isWeekend)}`}
            >
              <p className="text-[11px] font-semibold text-foreground/60">{dayName}</p>
              <p className="text-[10px] text-foreground/40">{monthDay}</p>
              <p className={`mt-1.5 text-sm font-bold ${tilePrice(ratio)}`}>
                ${price}
              </p>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="mt-4 flex items-center gap-5 text-xs text-foreground/50">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/80" />
          Lower
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-sky-400/80" />
          Mid
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-indigo-400/80" />
          Higher
        </span>
        <span className="ml-auto flex items-center gap-1.5 text-foreground/35">
          <span className="h-2 w-2 rounded-sm bg-current opacity-50" />
          Weekend
        </span>
      </div>
    </div>
  );
}
