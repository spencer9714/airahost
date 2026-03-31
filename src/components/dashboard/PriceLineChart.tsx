"use client";

import { useMemo } from "react";
import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  pricingMode: "refundable" | "nonRefundable";
}

// Chart canvas constants
const W = 560;
const H = 200;
const PAD_L = 52; // room for Y-axis labels
const PAD_R = 12;
const PAD_T = 14;
const PAD_B = 28; // room for X-axis labels
const CHART_W = W - PAD_L - PAD_R;
const CHART_H = H - PAD_T - PAD_B;

function roundTick(value: number): number {
  // Round to a clean number for Y-axis labels
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  return Math.round(value / (magnitude / 2)) * (magnitude / 2);
}

function avg(values: number[]): number {
  if (values.length === 0) return 0;
  return Math.round(values.reduce((a, b) => a + b, 0) / values.length);
}

export function PriceLineChart({ calendar, pricingMode }: Props) {
  const days = useMemo(
    () => calendar.filter((d) => d.basePrice != null),
    [calendar]
  );

  if (days.length < 2) return null;

  // ── Field mapping ──────────────────────────────────────────────
  // Market price  : basePrice — the raw market-driven base price, always present.
  // Our price     : effectiveDailyPrice* (newer, includes all adjustments) with
  //                 fallback to legacy refundablePrice / nonRefundablePrice.
  const marketPrices = days.map((d) => d.basePrice);
  const ourPrices = days.map((d) => {
    if (pricingMode === "refundable") {
      return d.effectiveDailyPriceRefundable ?? d.refundablePrice;
    }
    return d.effectiveDailyPriceNonRefundable ?? d.nonRefundablePrice;
  });

  // ── Scaling ────────────────────────────────────────────────────
  const allPrices = [...marketPrices, ...ourPrices];
  const rawMin = Math.min(...allPrices);
  const rawMax = Math.max(...allPrices);
  const padding = (rawMax - rawMin) * 0.12 || 10;
  const minY = Math.max(0, rawMin - padding);
  const maxY = rawMax + padding;
  const rangeY = maxY - minY;

  function toX(i: number): number {
    return PAD_L + (i / (days.length - 1)) * CHART_W;
  }
  function toY(price: number): number {
    return PAD_T + CHART_H - ((price - minY) / rangeY) * CHART_H;
  }

  // ── Polyline point strings ─────────────────────────────────────
  const marketPoints = days
    .map((_, i) => `${toX(i).toFixed(1)},${toY(marketPrices[i]).toFixed(1)}`)
    .join(" ");
  const ourPoints = days
    .map((_, i) => `${toX(i).toFixed(1)},${toY(ourPrices[i]).toFixed(1)}`)
    .join(" ");

  // ── Y-axis ticks (4 levels) ────────────────────────────────────
  const yTicks = Array.from({ length: 4 }, (_, i) => {
    const raw = minY + (rangeY * i) / 3;
    const val = roundTick(raw);
    return { val, y: toY(val) };
  });

  // ── X-axis labels (first, every 7th, last) ─────────────────────
  const xLabels: { i: number; label: string }[] = [];
  for (let i = 0; i < days.length; i++) {
    if (i === 0 || i % 7 === 0 || i === days.length - 1) {
      const d = new Date(days[i].date + "T00:00:00");
      const label = d.toLocaleString("en-US", { month: "short", day: "numeric" });
      // Avoid duplicate last label if it falls on a 7-multiple
      if (xLabels.length === 0 || xLabels[xLabels.length - 1].i !== i) {
        xLabels.push({ i, label });
      }
    }
  }

  // ── Summary stats ──────────────────────────────────────────────
  const avgMarket = avg(marketPrices);
  const avgOurs = avg(ourPrices);
  const avgGap = avgOurs - avgMarket;

  return (
    <div className="rounded-2xl border border-border bg-white p-5 sm:p-6">
      {/* ── Header + summary ── */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div>
          <h3 className="text-base font-bold tracking-tight">Market vs Your Price</h3>
          <p className="mt-0.5 text-xs text-foreground/40">
            {pricingMode === "refundable" ? "Refundable" : "Non-refundable"} · {days.length} days
          </p>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <span>
            <span className="text-foreground/40">Avg market </span>
            <span className="font-semibold text-foreground/65">${avgMarket}</span>
          </span>
          <span>
            <span className="text-foreground/40">Avg yours </span>
            <span className="font-semibold text-indigo-600">${avgOurs}</span>
          </span>
          <span>
            <span className="text-foreground/40">Gap </span>
            <span
              className={`font-semibold ${
                avgGap > 0
                  ? "text-indigo-500"
                  : avgGap < 0
                  ? "text-emerald-600"
                  : "text-foreground/50"
              }`}
            >
              {avgGap > 0 ? "+" : ""}{avgGap}
            </span>
          </span>
        </div>
      </div>

      {/* ── SVG chart ── */}
      <div className="w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="xMidYMid meet"
          className="w-full"
          style={{ height: "auto", minHeight: 130, maxHeight: 210 }}
          aria-label="Market vs Your Price line chart"
          role="img"
        >
          {/* Y-axis grid lines + labels */}
          {yTicks.map(({ val, y }) => (
            <g key={val}>
              <line
                x1={PAD_L}
                y1={y}
                x2={W - PAD_R}
                y2={y}
                stroke="#f3f4f6"
                strokeWidth="1"
              />
              <text
                x={PAD_L - 7}
                y={y}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize="10"
                fill="#9ca3af"
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
            stroke="#e5e7eb"
            strokeWidth="1"
          />

          {/* X-axis labels */}
          {xLabels.map(({ i, label }) => (
            <text
              key={i}
              x={toX(i)}
              y={H - 6}
              textAnchor="middle"
              fontSize="10"
              fill="#9ca3af"
            >
              {label}
            </text>
          ))}

          {/* Market price line (slate) */}
          <polyline
            points={marketPoints}
            fill="none"
            stroke="#94a3b8"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Our price line (indigo) */}
          <polyline
            points={ourPoints}
            fill="none"
            stroke="#6366f1"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Data points — only for shorter ranges to avoid clutter */}
          {days.length <= 14 &&
            days.map((_, i) => (
              <circle key={`m${i}`} cx={toX(i)} cy={toY(marketPrices[i])} r="2.5" fill="#94a3b8" />
            ))}
          {days.length <= 14 &&
            days.map((_, i) => (
              <circle key={`o${i}`} cx={toX(i)} cy={toY(ourPrices[i])} r="3" fill="#6366f1" />
            ))}
        </svg>
      </div>

      {/* ── Legend ── */}
      <div className="mt-3 flex items-center gap-5 text-xs text-foreground/50">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full bg-slate-400"
            style={{ width: 16, height: 2 }}
          />
          Market price
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full bg-indigo-500"
            style={{ width: 16, height: 2.5 }}
          />
          Your price
        </span>
      </div>
    </div>
  );
}
