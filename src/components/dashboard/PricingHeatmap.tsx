"use client";

import { useState } from "react";
import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  /** When true, dates are selectable and an apply footer is shown. */
  selectable?: boolean;
  onApplyDates?: (selectedDates: string[]) => void;
}

const TODAY = new Date().toISOString().split("T")[0];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function PricingHeatmap({ calendar, selectable = false, onApplyDates }: Props) {
  const days = calendar;

  const [selectedDates, setSelectedDates] = useState<Set<string>>(
    () => new Set(days.map((d) => d.date))
  );
  const [view, setView] = useState<"7" | "30">("30");

  if (days.length === 0) return null;

  const displayPrices = days.map((d) => d.recommendedDailyPrice ?? d.basePrice);
  const visibleDays = view === "7" ? days.slice(0, 7) : days;
  const selectedCount = selectedDates.size;
  const totalCount = days.length;

  const startOffset = view === "30"
    ? new Date(visibleDays[0].date + "T00:00:00").getDay()
    : 0;

  function toggleDate(date: string) {
    setSelectedDates((prev) => {
      const next = new Set(prev);
      if (next.has(date)) next.delete(date);
      else next.add(date);
      return next;
    });
  }

  function selectAll() { setSelectedDates(new Set(days.map((d) => d.date))); }
  function selectNone() { setSelectedDates(new Set()); }

  return (
    <div className="rounded-2xl border border-border bg-white">

      {/* ── Header ── */}
      <div className="flex items-center justify-between gap-3 px-6 py-4">
        <h3 className="text-sm font-semibold text-foreground/80">30-Day Pricing Plan</h3>
        <div className="flex items-center gap-3">
          <div className="flex items-center rounded-lg bg-gray-100/70 p-0.5">
            {(["7", "30"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium transition-all ${
                  view === v
                    ? "bg-white text-foreground/75 shadow-sm"
                    : "text-foreground/35 hover:text-foreground/55"
                }`}
              >
                {v === "7" ? "7 days" : "30 days"}
              </button>
            ))}
          </div>
          {selectable && (
            <div className="flex items-center gap-1.5 text-xs text-foreground/30">
              <button type="button" onClick={selectAll} className="transition-colors hover:text-foreground/55">All</button>
              <span>·</span>
              <button type="button" onClick={selectNone} className="transition-colors hover:text-foreground/55">None</button>
            </div>
          )}
        </div>
      </div>

      {/* ── Weekday headers ── */}
      <div className="grid grid-cols-7 border-t border-gray-100 px-4">
        {WEEKDAYS.map((wd) => (
          <div key={wd} className="py-2 text-center text-xs font-normal text-foreground/40">
            {wd}
          </div>
        ))}
      </div>

      {/* ── Calendar grid ── */}
      <div className="grid grid-cols-7 gap-1.5 px-4 pb-4">
        {Array.from({ length: startOffset }).map((_, i) => (
          <div key={`empty-${i}`} />
        ))}

        {visibleDays.map((day, i) => {
          const globalIdx = days.indexOf(day);
          const price = displayPrices[globalIdx >= 0 ? globalIdx : i];
          const d = new Date(day.date + "T00:00:00");
          const dateNum = d.getDate();
          const isToday = day.date === TODAY;
          const isPast = day.date < TODAY;
          const isSelected = selectedDates.has(day.date);

          const tileCls = isSelected && selectable
            ? "bg-white border border-gray-800/30 ring-1 ring-gray-800/20"
            : "bg-white border border-gray-200/80";

          const dateNumEl = isToday ? (
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-rose-500 text-xs font-semibold text-white">
              {dateNum}
            </span>
          ) : (
            <span className={`text-sm font-medium ${isPast ? "text-foreground/25" : "text-foreground/80"}`}>
              {dateNum}
            </span>
          );

          const cell = (
            <div className={`rounded-2xl p-2.5 transition-colors ${tileCls} ${selectable && !isPast ? "cursor-pointer hover:border-gray-400/50" : ""}`}>
              {dateNumEl}
              {!isPast && (
                <p className="mt-3 text-sm font-medium text-foreground/70">${price}</p>
              )}
            </div>
          );

          if (selectable && !isPast) {
            return (
              <button key={day.date} type="button" onClick={() => toggleDate(day.date)} className="text-left">
                {cell}
              </button>
            );
          }
          return <div key={day.date}>{cell}</div>;
        })}
      </div>

      {/* ── Apply footer ── */}
      {selectable && (
        <div className="border-t border-gray-100 px-6 py-4">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-foreground/35">
              {selectedCount === totalCount ? `All ${totalCount} nights` : `${selectedCount} of ${totalCount} nights`}
              {" selected"}
            </p>
            <button
              type="button"
              disabled={selectedCount === 0}
              onClick={() => onApplyDates?.(Array.from(selectedDates))}
              className="rounded-xl bg-gray-900 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-gray-700 disabled:opacity-40"
            >
              Apply {selectedCount} nights →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
