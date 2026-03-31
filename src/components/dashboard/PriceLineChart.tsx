"use client";

import { useMemo } from "react";
import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  pricingMode: "refundable" | "nonRefundable";
  /** Host's observed live price on Airbnb (report start-date basis). Single value, not a series. */
  observedListingPrice?: number | null;
}

// Canvas constants
const W = 560;
const H = 186;
const PAD_L = 46;
const PAD_R = 16;
const PAD_T = 10;
const PAD_B = 26;
const CHART_W = W - PAD_L - PAD_R;
const CHART_H = H - PAD_T - PAD_B;

// Color palette — aligned with the product's Airbnb-adjacent design
const C_MARKET    = "#c6cdd8";  // muted cool gray — clearly secondary
const C_SUGGESTED = "#374151";  // charcoal — deliberate, premium primary
const C_LIVE      = "#ff385c";  // product accent — brand signal for live price
const C_GRID      = "#f0f1f3";  // barely-there grid
const C_AXIS      = "#aab0bc";  // quiet axis labels
const C_BASELINE  = "#e4e7eb";  // x-axis rule

function roundTick(value: number): number {
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  return Math.round(value / (magnitude / 2)) * (magnitude / 2);
}

function avg(values: number[]): number {
  if (values.length === 0) return 0;
  return Math.round(values.reduce((a, b) => a + b, 0) / values.length);
}

export function PriceLineChart({ calendar, pricingMode, observedListingPrice }: Props) {
  const days = useMemo(
    () => calendar.filter((d) => d.basePrice != null),
    [calendar]
  );

  if (days.length < 2) return null;

  // ── Field mapping ──────────────────────────────────────────────
  // Market    : baseDailyPrice ?? basePrice (market-driven base, post-LM)
  // Suggested : effectiveDailyPrice* (all adjustments) with legacy fallback
  // Live      : observedListingPrice — single observed value, NOT a series
  const marketPrices    = days.map((d) => d.baseDailyPrice ?? d.basePrice);
  const suggestedPrices = days.map((d) =>
    pricingMode === "refundable"
      ? (d.effectiveDailyPriceRefundable    ?? d.refundablePrice)
      : (d.effectiveDailyPriceNonRefundable ?? d.nonRefundablePrice)
  );

  // ── Scaling — include live price so marker stays in-frame ──────
  const livePrice = observedListingPrice ?? null;
  const allPrices = [
    ...marketPrices,
    ...suggestedPrices,
    ...(livePrice != null ? [livePrice] : []),
  ];
  const rawMin = Math.min(...allPrices);
  const rawMax = Math.max(...allPrices);
  const padding = (rawMax - rawMin) * 0.14 || 12;
  const minY = Math.max(0, rawMin - padding);
  const maxY = rawMax + padding;
  const rangeY = maxY - minY;

  function toX(i: number): number {
    return PAD_L + (i / (days.length - 1)) * CHART_W;
  }
  function toY(price: number): number {
    return PAD_T + CHART_H - ((price - minY) / rangeY) * CHART_H;
  }

  // ── Polyline strings ───────────────────────────────────────────
  const marketPoints    = days.map((_, i) => `${toX(i).toFixed(1)},${toY(marketPrices[i]).toFixed(1)}`).join(" ");
  const suggestedPoints = days.map((_, i) => `${toX(i).toFixed(1)},${toY(suggestedPrices[i]).toFixed(1)}`).join(" ");

  // ── Area fill path under suggested line ───────────────────────
  const chartBottom = (PAD_T + CHART_H).toFixed(1);
  const suggestedAreaPath = [
    `M ${toX(0).toFixed(1)},${chartBottom}`,
    ...days.map((_, i) => `L ${toX(i).toFixed(1)},${toY(suggestedPrices[i]).toFixed(1)}`),
    `L ${toX(days.length - 1).toFixed(1)},${chartBottom}`,
    "Z",
  ].join(" ");

  // ── Y-axis ticks (3 — fewer lines = more breathing room) ──────
  const yTicks = Array.from({ length: 3 }, (_, i) => {
    const raw = minY + (rangeY * (i + 0.5)) / 3;
    const val = roundTick(raw);
    return { val, y: toY(val) };
  });

  // ── X-axis labels ──────────────────────────────────────────────
  const xLabels: { i: number; label: string }[] = [];
  for (let i = 0; i < days.length; i++) {
    if (i === 0 || i % 7 === 0 || i === days.length - 1) {
      const d = new Date(days[i].date + "T00:00:00");
      const label = d.toLocaleString("en-US", { month: "short", day: "numeric" });
      if (xLabels.length === 0 || xLabels[xLabels.length - 1].i !== i) {
        xLabels.push({ i, label });
      }
    }
  }

  // ── Summary stats ──────────────────────────────────────────────
  const avgMarket    = avg(marketPrices);
  const avgSuggested = avg(suggestedPrices);
  const avgGap       = avgSuggested - avgMarket;

  // ── Live marker position (day 0 = report start date) ──────────
  const liveDotX   = toX(0);
  const liveDotY   = livePrice != null ? toY(livePrice) : null;
  const hasLive    = livePrice != null && liveDotY != null;

  // Label floats above or below the dot depending on available space
  const labelText  = hasLive ? `$${livePrice}` : "";
  const labelW     = Math.max(28, labelText.length * 6.5 + 10);
  const labelAbove = liveDotY != null && liveDotY > PAD_T + 22;
  const labelY     = liveDotY != null ? (labelAbove ? liveDotY - 15 : liveDotY + 10) : 0;
  const labelX     = liveDotX + 10;

  return (
    <div className="rounded-2xl border border-border bg-white">

      {/* ── Header ── */}
      <div className="flex items-center justify-between gap-3 px-5 pt-5 sm:px-6 sm:pt-5">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-foreground/30">
          Market vs Suggested
        </p>
        <span className="text-[10px] text-foreground/30">
          {pricingMode === "refundable" ? "Refundable" : "Non-refundable"} · {days.length} days
        </span>
      </div>

      {/* ── SVG chart ── */}
      <div className="mt-2 w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="xMidYMid meet"
          className="w-full"
          style={{ height: "auto", minHeight: 110, maxHeight: 196 }}
          aria-label="Market vs Suggested Price chart"
          role="img"
        >
          {/* Y-axis grid lines + labels */}
          {yTicks.map(({ val, y }) => (
            <g key={val}>
              <line x1={PAD_L} y1={y} x2={W - PAD_R} y2={y} stroke={C_GRID} strokeWidth="1" />
              <text
                x={PAD_L - 6}
                y={y}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize="9"
                fill={C_AXIS}
              >
                ${val}
              </text>
            </g>
          ))}

          {/* X-axis baseline */}
          <line
            x1={PAD_L}
            y1={PAD_T + CHART_H}
            x2={W - PAD_R}
            y2={PAD_T + CHART_H}
            stroke={C_BASELINE}
            strokeWidth="1"
          />

          {/* X-axis labels */}
          {xLabels.map(({ i, label }) => (
            <text
              key={i}
              x={toX(i)}
              y={H - 4}
              textAnchor="middle"
              fontSize="9"
              fill={C_AXIS}
            >
              {label}
            </text>
          ))}

          {/* Suggested area fill — very subtle, adds depth without noise */}
          <path d={suggestedAreaPath} fill={C_SUGGESTED} opacity="0.05" />

          {/* Market line — light, receding */}
          <polyline
            points={marketPoints}
            fill="none"
            stroke={C_MARKET}
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Suggested line — charcoal, visual primary */}
          <polyline
            points={suggestedPoints}
            fill="none"
            stroke={C_SUGGESTED}
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Data point dots — short ranges only */}
          {days.length <= 14 &&
            days.map((_, i) => (
              <circle key={`m${i}`} cx={toX(i)} cy={toY(marketPrices[i])} r="2" fill={C_MARKET} />
            ))}
          {days.length <= 14 &&
            days.map((_, i) => (
              <circle key={`s${i}`} cx={toX(i)} cy={toY(suggestedPrices[i])} r="2.75" fill={C_SUGGESTED} />
            ))}

          {/* ── Live price marker ───────────────────────────────────────
              Single observed value at start-date (day 0).
              Uses brand accent — NOT a trend line.
          ──────────────────────────────────────────────────────────── */}
          {hasLive && liveDotY != null && (
            <g aria-label={`Current live price: $${livePrice}`}>
              {/* Outer halo */}
              <circle cx={liveDotX} cy={liveDotY} r={8} fill={C_LIVE} opacity="0.13" />
              {/* Core dot */}
              <circle cx={liveDotX} cy={liveDotY} r={4} fill={C_LIVE} />
              {/* Price label */}
              <rect
                x={labelX}
                y={labelY}
                width={labelW}
                height={13}
                rx={3}
                fill="white"
                stroke={C_LIVE}
                strokeWidth="0.75"
              />
              <text
                x={labelX + labelW / 2}
                y={labelY + 9}
                textAnchor="middle"
                fontSize="8.5"
                fontWeight="600"
                fill={C_LIVE}
              >
                {labelText}
              </text>
            </g>
          )}
        </svg>
      </div>

      {/* ── Stats + legend ── */}
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-t border-border px-5 py-4 sm:px-6">

        {/* Stat pills */}
        <div className="flex flex-wrap gap-x-5 gap-y-2.5">
          <div>
            <p className="text-[9.5px] font-semibold uppercase tracking-wider text-foreground/30">
              Market avg
            </p>
            <p className="mt-0.5 text-sm font-medium text-foreground/50">${avgMarket}</p>
          </div>
          <div>
            <p className="text-[9.5px] font-semibold uppercase tracking-wider text-foreground/30">
              Suggested avg
            </p>
            <p className="mt-0.5 text-sm font-semibold text-foreground/80">${avgSuggested}</p>
          </div>
          <div>
            <p className="text-[9.5px] font-semibold uppercase tracking-wider text-foreground/30">
              Gap
            </p>
            <p className={`mt-0.5 text-sm font-medium ${avgGap === 0 ? "text-foreground/35" : "text-foreground/55"}`}>
              {avgGap > 0 ? "+" : ""}{avgGap}
            </p>
          </div>
          {hasLive && (
            <div>
              <p className="text-[9.5px] font-semibold uppercase tracking-wider text-foreground/30">
                Your live
              </p>
              <p className="mt-0.5 text-sm font-semibold" style={{ color: C_LIVE }}>
                ${livePrice}
              </p>
            </div>
          )}
        </div>

        {/* Legend */}
        <div className="flex items-center gap-4 text-[11px] text-foreground/35">
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block rounded-full"
              style={{ width: 14, height: 1.75, background: C_MARKET }}
            />
            Market
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block rounded-full"
              style={{ width: 14, height: 2.5, background: C_SUGGESTED }}
            />
            Suggested
          </span>
          {hasLive && (
            <span className="flex items-center gap-1.5">
              <span
                className="inline-block rounded-full"
                style={{ width: 7, height: 7, background: C_LIVE }}
              />
              Live
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
