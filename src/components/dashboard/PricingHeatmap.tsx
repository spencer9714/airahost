"use client";

import { useState } from "react";
import type { CalendarDay } from "@/lib/schemas";

interface Props {
  calendar: CalendarDay[];
  selectable?: boolean;
  /**
   * true  = configured but co-host not yet verified (show "Add co-host" banner)
   * false = verified (calendar is fully interactive)
   */
  applyGated?: boolean;
  /**
   * true = co-host invite was sent and we are waiting for Airbnb to confirm.
   * When true the banner changes from "Add" to "Verifying..." instead of
   * prompting the user to do something they've already done.
   */
  cohostVerifying?: boolean;
  onApplyDates?: (selectedDates: string[]) => void;
  onSetupCohost?: () => void;
  onManageCohost?: () => void;
}

const TODAY = new Date().toISOString().split("T")[0];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function PricingHeatmap({
  calendar,
  selectable = false,
  applyGated = false,
  cohostVerifying = false,
  onApplyDates,
  onSetupCohost,
  onManageCohost,
}: Props) {
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

  // Offset so the first date lands on the correct weekday column
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
        {/* Empty offset cells */}
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
                <p className="mt-3 text-sm font-medium text-foreground/70">
                  ${price}
                </p>
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

      {/* ── Co-host gating banner ── */}
      {applyGated && (
        <div className="mx-4 mb-2 overflow-hidden rounded-2xl border border-gray-200 bg-gray-50">
          <div className="flex items-start gap-4 px-5 py-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-gray-200">
              {cohostVerifying ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="text-blue-400" aria-hidden="true">
                  <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z" />
                  <path d="M12 6v6l4 2" />
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="text-foreground/60" aria-hidden="true">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                  <line x1="19" y1="8" x2="19" y2="14" />
                  <line x1="22" y1="11" x2="16" y2="11" />
                </svg>
              )}
            </div>
            <div className="min-w-0 flex-1">
              {cohostVerifying ? (
                <>
                  <p className="text-sm font-semibold text-foreground/80">Verifying co-host access</p>
                  <p className="mt-0.5 text-xs leading-snug text-foreground/45">
                    We&apos;re confirming your co-host status with Airbnb. This usually takes a few minutes.
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm font-semibold text-foreground/80">Add Airahost as co-host</p>
                  <p className="mt-0.5 text-xs leading-snug text-foreground/45">
                    Grant co-host access in Airbnb so we can apply your pricing recommendations automatically.
                  </p>
                  {onSetupCohost && (
                    <button
                      type="button"
                      onClick={onSetupCohost}
                      className="mt-3 inline-flex items-center gap-1.5 rounded-xl bg-foreground px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-foreground/80"
                    >
                      Set up in Airbnb
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M5 12h14M12 5l7 7-7 7" />
                      </svg>
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Footer ── */}
      {(selectable && !applyGated) && (
        <div className="border-t border-gray-100 px-6 py-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <p className="text-xs text-foreground/35">
                {selectedCount === totalCount
                  ? `All ${totalCount} nights`
                  : `${selectedCount} of ${totalCount} nights`}
                {" selected"}
              </p>
              {onManageCohost && (
                <button
                  type="button"
                  onClick={onManageCohost}
                  className="text-[11px] text-foreground/30 underline-offset-2 transition-colors hover:text-foreground/55"
                >
                  Manage co-host
                </button>
              )}
            </div>
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
