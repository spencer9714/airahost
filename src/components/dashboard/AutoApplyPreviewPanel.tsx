"use client";

import { createPortal } from "react-dom";
import { useEffect, useState } from "react";
import type { AutoApplyPreviewResult, AdjustmentReason } from "@/lib/autoApplyPreview";

interface AutoApplyPreviewPanelProps {
  listingName: string;
  preview: AutoApplyPreviewResult;
  onClose: () => void;
  onEditSettings: () => void;
  /** True when the preview reflects unsaved drawer draft values. */
  isUnsavedDraft?: boolean;
  /**
   * When provided, shows an "Apply prices" CTA in the footer.
   * Passes the array of selected night dates to the caller.
   * Not shown when isUnsavedDraft is true (must save settings first).
   */
  onApply?: (selectedDates: string[]) => void;
}

function fmtDate(dateStr: string): string {
  try {
    const d = new Date(dateStr + "T00:00:00Z");
    return d.toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  } catch {
    return dateStr;
  }
}

function fmtPrice(n: number | null): string {
  if (n == null) return "—";
  return `$${n}`;
}

function fmtRange(r: { min: number; max: number } | null): string {
  if (!r) return "—";
  if (r.min === r.max) return `$${r.min}`;
  return `$${r.min} – $${r.max}`;
}

interface StatusBadgeProps {
  skipped: boolean;
  skipReason: string | null;
  reason: AdjustmentReason;
}

function StatusBadge({ skipped, skipReason, reason }: StatusBadgeProps) {
  if (skipped) {
    if (skipReason === "notice_window") {
      return (
        <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-foreground/40">
          Notice window
        </span>
      );
    }
    return (
      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-foreground/40">
        No data
      </span>
    );
  }

  if (reason === "floored") {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
        Floored
      </span>
    );
  }
  if (reason === "floored_and_capped") {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
        Floored + capped
      </span>
    );
  }
  if (reason === "capped_increase") {
    return (
      <span className="inline-flex items-center rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-700">
        Cap applied
      </span>
    );
  }
  if (reason === "capped_decrease") {
    return (
      <span className="inline-flex items-center rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-700">
        Cap applied
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
      Will apply
    </span>
  );
}

export function AutoApplyPreviewPanel({
  listingName,
  preview,
  onClose,
  onEditSettings,
  isUnsavedDraft = false,
  onApply,
}: AutoApplyPreviewPanelProps) {
  const [mounted, setMounted] = useState(false);
  const [showAll, setShowAll] = useState(false);

  // Selectable nights = non-skipped nights that have a final price.
  const selectableNights = preview.nights.filter(
    (n) => !n.skipped && n.finalAutoApplyPrice != null
  );

  const [selectedDates, setSelectedDates] = useState<Set<string>>(
    () => new Set(selectableNights.map((n) => n.date))
  );

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!mounted) return null;

  const {
    rangeStart,
    rangeEnd,
    totalWindowNights,
    nightsWithData,
    nightsSkipped,
    nightsIncluded,
    nightsFloored,
    nightsCappedIncrease,
    recommendedPriceRange,
    finalApplyPriceRange,
    nights,
    includedDatesContiguous,
    scopeNote,
    settingsSnapshot,
  } = preview;

  const hasFloor = settingsSnapshot.minPriceFloor != null;
  const floorChanged = nightsFloored > 0;
  const displayedNights = showAll ? nights : nights.slice(0, 30);
  const hasMore = nights.length > 30 && !showAll;

  const allSelected = selectableNights.length > 0 && selectedDates.size === selectableNights.length;
  const noneSelected = selectedDates.size === 0;

  function toggleDate(date: string) {
    setSelectedDates((prev) => {
      const next = new Set(prev);
      if (next.has(date)) next.delete(date);
      else next.add(date);
      return next;
    });
  }

  function selectAll() {
    setSelectedDates(new Set(selectableNights.map((n) => n.date)));
  }

  function selectNone() {
    setSelectedDates(new Set());
  }

  // Describe the included nights date coverage
  let coverageLabel: string;
  if (nightsIncluded === 0) {
    coverageLabel = "No nights to apply";
  } else if (includedDatesContiguous) {
    coverageLabel = `${fmtDate(rangeStart)} – ${fmtDate(rangeEnd)}`;
  } else {
    coverageLabel = `${nightsIncluded} nights (non-contiguous)`;
  }

  const showApplyCta = onApply && !isUnsavedDraft && nightsIncluded > 0;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        className="relative z-10 flex max-h-[90dvh] w-full max-w-lg flex-col rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="preview-title"
      >
        {/* ── Header ── */}
        <div className="flex items-start justify-between border-b border-gray-100 px-6 pt-6 pb-4">
          <div className="mr-6">
            <div className="flex items-center gap-2">
              <h2
                id="preview-title"
                className="text-base font-bold tracking-tight text-foreground"
              >
                Auto-Apply preview
              </h2>
              <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-foreground/40">
                Dry run
              </span>
              {isUnsavedDraft && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
                  Unsaved changes
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-foreground/50">{listingName}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="mt-0.5 shrink-0 rounded-full p-1.5 text-foreground/30 transition-colors hover:bg-gray-100 hover:text-foreground/60"
          >
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
              <path
                d="M1 1l8 8M9 1l-8 8"
                stroke="currentColor"
                strokeWidth="1.75"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {/* ── Scrollable body ── */}
        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">

          {/* ── Summary stats ── */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                Window
              </p>
              <p className="mt-0.5 text-sm font-semibold text-foreground/70">
                {coverageLabel}
              </p>
              <p className="mt-0.5 text-xs text-foreground/40">
                {totalWindowNights} nights · {nightsIncluded} included
                {nightsSkipped > 0 && ` · ${nightsSkipped} skipped`}
              </p>
            </div>

            <div className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                Recommended range
              </p>
              <p className="mt-0.5 text-sm font-semibold text-foreground/70">
                {fmtRange(recommendedPriceRange)}
              </p>
              <p className="mt-0.5 text-xs text-foreground/40">per night</p>
            </div>

            <div
              className={`rounded-xl border px-4 py-3 ${
                floorChanged
                  ? "border-amber-100 bg-amber-50/60"
                  : "border-gray-100 bg-gray-50/60"
              }`}
            >
              <p
                className={`text-[10px] font-semibold uppercase tracking-wider ${
                  floorChanged ? "text-amber-600/70" : "text-foreground/35"
                }`}
              >
                Final apply range
              </p>
              <p
                className={`mt-0.5 text-sm font-semibold ${
                  floorChanged ? "text-amber-800" : "text-foreground/70"
                }`}
              >
                {fmtRange(finalApplyPriceRange)}
              </p>
              <p
                className={`mt-0.5 text-xs ${
                  floorChanged ? "text-amber-600/70" : "text-foreground/40"
                }`}
              >
                {floorChanged
                  ? `${nightsFloored} night${nightsFloored !== 1 ? "s" : ""} floored`
                  : "per night"}
              </p>
            </div>

            <div className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                Data coverage
              </p>
              <p className="mt-0.5 text-sm font-semibold text-foreground/70">
                {nightsWithData} / {totalWindowNights}
              </p>
              <p className="mt-0.5 text-xs text-foreground/40">nights with report data</p>
            </div>
          </div>

          {/* ── Floor guardrail note ── */}
          {hasFloor && (
            <div className="rounded-xl border border-amber-100 bg-amber-50/60 px-4 py-3">
              <p className="text-xs leading-snug text-amber-800">
                <span className="font-semibold">
                  Minimum floor: ${settingsSnapshot.minPriceFloor}/night.
                </span>{" "}
                {floorChanged ? (
                  <>
                    {nightsFloored} night{nightsFloored !== 1 ? "s" : ""}{" "}
                    {nightsFloored === 1 ? "has a" : "have"} recommendation below your
                    minimum — the floor price will be applied instead. Your recommendation
                    stays unchanged in all reports.
                  </>
                ) : (
                  <>
                    No nights in this window fall below your floor. Recommendation is
                    always shown as-is.
                  </>
                )}
              </p>
            </div>
          )}

          {/* ── Caps note ── */}
          {nightsCappedIncrease > 0 && (
            <div className="rounded-xl border border-blue-100 bg-blue-50/50 px-4 py-3">
              <p className="text-xs leading-snug text-blue-800">
                <span className="font-semibold">
                  Increase cap applied to {nightsCappedIncrease} night
                  {nightsCappedIncrease !== 1 ? "s" : ""}.
                </span>{" "}
                The floor pushed the price above your max-increase cap, so the final
                price was reduced to the cap limit.
              </p>
            </div>
          )}

          {/* ── Scope note ── */}
          {scopeNote && (
            <div className="rounded-xl border border-gray-200 bg-gray-50/60 px-4 py-3">
              <p className="text-xs leading-snug text-foreground/55">{scopeNote}</p>
            </div>
          )}

          {/* ── Non-contiguous note ── */}
          {nightsIncluded > 0 && !includedDatesContiguous && (
            <div className="rounded-xl border border-gray-200 bg-gray-50/60 px-4 py-3">
              <p className="text-xs leading-snug text-foreground/55">
                <span className="font-semibold">Non-contiguous nights:</span> some dates in
                the window have no report data. Only the nights shown as "Will apply" or
                guardrail-adjusted below are included.
              </p>
            </div>
          )}

          {/* ── Per-night table ── */}
          {nights.length > 0 ? (
            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wider text-foreground/35">
                  Per-night detail
                </p>
                {showApplyCta && selectableNights.length > 0 && (
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={selectAll}
                      disabled={allSelected}
                      className="text-[11px] font-medium text-foreground/40 transition-colors hover:text-foreground/70 disabled:opacity-30"
                    >
                      All
                    </button>
                    <span className="text-foreground/20">·</span>
                    <button
                      type="button"
                      onClick={selectNone}
                      disabled={noneSelected}
                      className="text-[11px] font-medium text-foreground/40 transition-colors hover:text-foreground/70 disabled:opacity-30"
                    >
                      None
                    </button>
                  </div>
                )}
              </div>
              <div className="overflow-hidden rounded-xl border border-gray-100">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-gray-100 bg-gray-50/80">
                      {showApplyCta && (
                        <th className="w-8 px-3 py-2.5" aria-label="Select" />
                      )}
                      <th className="px-3 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                        Date
                      </th>
                      <th className="px-3 py-2.5 text-right text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                        Rec.
                      </th>
                      <th className="px-3 py-2.5 text-right text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                        Final
                      </th>
                      <th className="px-3 py-2.5 text-right text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {displayedNights.map((night) => {
                      const isSelectable = showApplyCta && !night.skipped && night.finalAutoApplyPrice != null;
                      const isChecked = isSelectable && selectedDates.has(night.date);
                      return (
                        <tr
                          key={night.date}
                          className={`border-b border-gray-50 last:border-0 ${
                            night.skipped ? "opacity-45" : ""
                          } ${isSelectable ? "cursor-pointer hover:bg-gray-50/60" : ""}`}
                          onClick={isSelectable ? () => toggleDate(night.date) : undefined}
                        >
                          {showApplyCta && (
                            <td className="px-3 py-2.5">
                              {isSelectable && (
                                <input
                                  type="checkbox"
                                  checked={isChecked}
                                  onChange={() => toggleDate(night.date)}
                                  onClick={(e) => e.stopPropagation()}
                                  className="h-3.5 w-3.5 cursor-pointer accent-foreground"
                                  aria-label={`Select ${night.date}`}
                                />
                              )}
                            </td>
                          )}
                          <td className="px-3 py-2.5">
                            <span className="text-[12px] font-medium text-foreground/70">
                              {fmtDate(night.date)}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-right text-[12px] text-foreground/50">
                            {fmtPrice(night.recommendedPrice)}
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            <span
                              className={`text-[12px] font-semibold ${
                                night.skipped
                                  ? "text-foreground/30"
                                  : night.adjustmentReason === "floored" ||
                                    night.adjustmentReason === "floored_and_capped"
                                  ? "text-amber-700"
                                  : "text-foreground/70"
                              }`}
                            >
                              {fmtPrice(night.finalAutoApplyPrice)}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            <StatusBadge
                              skipped={night.skipped}
                              skipReason={night.skipReason}
                              reason={night.adjustmentReason}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {hasMore && (
                  <div className="border-t border-gray-100 px-4 py-3 text-center">
                    <button
                      type="button"
                      onClick={() => setShowAll(true)}
                      className="text-xs font-medium text-foreground/40 transition-colors hover:text-foreground/70"
                    >
                      Show all {nights.length} nights ↓
                    </button>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-gray-200 py-8 text-center">
              <p className="text-sm font-medium text-foreground/40">
                No calendar data available
              </p>
              <p className="mt-1 text-xs text-foreground/30">
                Run a pricing report to see the Auto-Apply preview.
              </p>
            </div>
          )}

          {/* ── Preview footer note ── */}
          <p className="text-center text-[11px] text-foreground/30">
            Preview only — no prices are changed until execution is enabled.
            Booked and unavailable nights are excluded at runtime.
          </p>
        </div>

        {/* ── Footer actions ── */}
        <div className="flex items-center justify-between border-t border-gray-100 px-6 py-4">
          <button
            type="button"
            onClick={onEditSettings}
            className="text-sm font-medium text-foreground/40 transition-colors hover:text-foreground/70"
          >
            Edit settings
          </button>
          <div className="flex items-center gap-3">
            {showApplyCta && (
              <button
                type="button"
                disabled={selectedDates.size === 0}
                onClick={() => onApply!(Array.from(selectedDates))}
                className="rounded-xl bg-foreground px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-foreground/85 disabled:opacity-40"
              >
                {selectedDates.size === nightsIncluded
                  ? `Apply ${nightsIncluded} nights →`
                  : `Apply ${selectedDates.size} of ${nightsIncluded} nights →`}
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className={`text-sm font-medium transition-colors ${
                showApplyCta
                  ? "text-foreground/35 hover:text-foreground/60"
                  : "rounded-xl bg-foreground px-5 py-2.5 font-semibold text-white hover:bg-foreground/85"
              }`}
            >
              {showApplyCta ? "Close" : "Done"}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
