"use client";

import { useMemo } from "react";
import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  pricingMode: "refundable" | "nonRefundable";
  /** Host's observed live price on Airbnb (report start-date basis). Single value, not a series. */
  observedListingPrice?: number | null;
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
  // Market    : baseDailyPrice (post-LM-adjusted base) with fallback to legacy basePrice
  // Suggested : effectiveDailyPrice* (all adjustments incl. discount) with legacy fallback
  // Live      : observedListingPrice — single observed value, NOT a series
  const marketPrices = days.map((d) => d.baseDailyPrice ?? d.basePrice);
  const suggestedPrices = days.map((d) => {
    if (pricingMode === "refundable") {
      return d.effectiveDailyPriceRefundable ?? d.refundablePrice;
    }
    return d.effectiveDailyPriceNonRefundable ?? d.nonRefundablePrice;
  });

  // ── Scaling — include live price so marker stays in-frame ──────
  const livePrice = observedListingPrice ?? null;
  const allPrices = [
    ...marketPrices,
    ...suggestedPrices,
    ...(livePrice != null ? [livePrice] : []),
  ];
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
  const suggestedPoints = days
    .map((_, i) => `${toX(i).toFixed(1)},${toY(suggestedPrices[i]).toFixed(1)}`)
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
      if (xLabels.length === 0 || xLabels[xLabels.length - 1].i !== i) {
        xLabels.push({ i, label });
      }
    }
  }

  // ── Summary stats ──────────────────────────────────────────────
  const avgMarket = avg(marketPrices);
  const avgSuggested = avg(suggestedPrices);
  const avgGap = avgSuggested - avgMarket;

  // ── Live price marker position ─────────────────────────────────
  // Rendered at the first day (index 0) — live price is always observed on the report start date.
  const liveDotX = toX(0);
  const liveDotY = livePrice != null ? toY(livePrice) : null;

  // Label goes right of the dot; nudge up slightly for readability.
  // If dot is very close to top edge, push label down instead.
  const labelAboveDot = liveDotY != null && liveDotY > PAD_T + 18;
  const labelY = liveDotY != null
    ? (labelAboveDot ? liveDotY - 12 : liveDotY + 20)
    : 0;
  const labelX = liveDotX + 11;
  // Approximate label width based on text content
  const labelText = livePrice != null ? `Live $${livePrice}` : "";
  const labelW = 8 * labelText.length + 8; // rough char estimate

  const hasLiveMarker = livePrice != null && liveDotY != null;

  return (
    <div className="rounded-2xl border border-border bg-white p-5 sm:p-6">
      {/* ── Header + summary ── */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div>
          <h3 className="text-base font-bold tracking-tight">Market vs Suggested Price</h3>
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
            <span className="text-foreground/40">Avg suggested </span>
            <span className="font-semibold text-indigo-600">${avgSuggested}</span>
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
          {hasLiveMarker && (
            <span>
              <span className="text-foreground/40">Live </span>
              <span className="font-semibold text-amber-600">${livePrice}</span>
            </span>
          )}
        </div>
      </div>

      {/* ── SVG chart ── */}
      <div className="w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="xMidYMid meet"
          className="w-full"
          style={{ height: "auto", minHeight: 130, maxHeight: 210 }}
          aria-label="Market vs Suggested Price line chart"
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

          {/* Suggested price line (indigo) — visual primary */}
          <polyline
            points={suggestedPoints}
            fill="none"
            stroke="#6366f1"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Data point dots — only for shorter ranges to avoid clutter */}
          {days.length <= 14 &&
            days.map((_, i) => (
              <circle key={`m${i}`} cx={toX(i)} cy={toY(marketPrices[i])} r="2.5" fill="#94a3b8" />
            ))}
          {days.length <= 14 &&
            days.map((_, i) => (
              <circle key={`s${i}`} cx={toX(i)} cy={toY(suggestedPrices[i])} r="3" fill="#6366f1" />
            ))}

          {/* ── Current live price marker ───────────────────────────────
              Single observed value at start-date position.
              Rendered as a distinct amber marker — NOT a trend line.
          ────────────────────────────────────────────────────────────── */}
          {hasLiveMarker && liveDotY != null && (
            <g aria-label={`Current live price: $${livePrice}`}>
              {/* Outer pulse ring */}
              <circle
                cx={liveDotX}
                cy={liveDotY}
                r={9}
                fill="none"
                stroke="#f59e0b"
                strokeWidth="1.5"
                opacity="0.35"
              />
              {/* Inner solid dot */}
              <circle cx={liveDotX} cy={liveDotY} r={4.5} fill="#f59e0b" />
              {/* Label bubble */}
              <rect
                x={labelX}
                y={labelY - 6}
                width={labelW}
                height={14}
                rx={3.5}
                fill="#fef3c7"
                stroke="#fcd34d"
                strokeWidth="0.75"
              />
              <text
                x={labelX + labelW / 2}
                y={labelY + 4.5}
                textAnchor="middle"
                fontSize="9"
                fontWeight="600"
                fill="#92400e"
              >
                {labelText}
              </text>
            </g>
          )}
        </svg>
      </div>

      {/* ── Legend ── */}
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-foreground/50">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full bg-slate-400"
            style={{ width: 16, height: 2 }}
          />
          Market
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full bg-indigo-500"
            style={{ width: 16, height: 2.5 }}
          />
          Suggested
        </span>
        {hasLiveMarker && (
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block rounded-full bg-amber-400"
              style={{ width: 8, height: 8 }}
            />
            Current live
          </span>
        )}
      </div>
    </div>
  );
}
