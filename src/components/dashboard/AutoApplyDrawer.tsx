"use client";

import { createPortal } from "react-dom";
import { useEffect, useMemo, useState } from "react";
import { computeAutoApplyPreview } from "@/lib/autoApplyPreview";
import type { AutoApplyPreviewResult } from "@/lib/autoApplyPreview";
import type { CalendarDay } from "@/lib/schemas";

export interface AutoApplySettings {
  enabled: boolean;
  /** How many days ahead to look — always starts from today (day 0). */
  windowEndDays: number;
  /** "actionable": only mispriced nights. "all_sellable": every available night. */
  applyScope: "actionable" | "all_sellable";
  /**
   * Minimum nightly price guardrail.
   * finalAutoApplyPrice = max(recommendedDailyPrice, minPriceFloor)
   * Does NOT modify recommendedDailyPrice — it is an execution guardrail only.
   */
  minPriceFloor: number | null;
  /** Skip nights whose check-in is fewer than N days away. */
  minNoticeDays: number;
  /** Cap on price increase above recommendation (%). null = no cap. */
  maxIncreasePct: number | null;
  /** Cap on price decrease below recommendation (%). null = no cap. */
  maxDecreasePct: number | null;
  /** Skip booked or blocked nights. */
  skipUnavailableNights: boolean;
  /** ISO timestamp of last save — null if never configured. */
  lastUpdatedAt: string | null;
}

interface AutoApplyDrawerProps {
  listingName: string;
  settings: AutoApplySettings;
  calendar?: CalendarDay[];
  onClose: () => void;
  onSave: (patch: Omit<AutoApplySettings, "enabled" | "lastUpdatedAt">) => void;
  onDisable: () => void;
  /** Called with the current live draft preview so the parent can display it without recomputing. */
  onViewPreview?: (preview: AutoApplyPreviewResult) => void;
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors focus:outline-none ${
        checked ? "bg-emerald-500" : "bg-gray-200"
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-4" : "translate-x-1"
        }`}
      />
    </button>
  );
}

function NullableNumberInput({
  id,
  value,
  onChange,
  placeholder,
  min,
  max,
  suffix,
}: {
  id: string;
  value: number | null;
  onChange: (v: number | null) => void;
  placeholder?: string;
  min?: number;
  max?: number;
  suffix?: string;
}) {
  const [raw, setRaw] = useState(value != null ? String(value) : "");
  const trimmed = raw.trim();
  const parsed = trimmed !== "" ? Number(trimmed) : null;
  const invalid =
    parsed !== null &&
    (!Number.isFinite(parsed) ||
      (min !== undefined && parsed < min) ||
      (max !== undefined && parsed > max));

  function commit(s: string) {
    const t = s.trim();
    const n = t !== "" ? Number(t) : null;
    if (n === null || (Number.isFinite(n) && (min === undefined || n >= min) && (max === undefined || n <= max))) {
      onChange(n);
    }
  }

  return (
    <div>
      <div className="relative">
        <input
          id={id}
          type="number"
          min={min}
          max={max}
          placeholder={placeholder ?? "No cap"}
          value={raw}
          onChange={(e) => {
            setRaw(e.target.value);
            commit(e.target.value);
          }}
          className="w-full rounded-xl border border-gray-200 bg-gray-50/60 py-2.5 pl-3 pr-8 text-sm outline-none transition-colors focus:border-blue-400 focus:bg-white"
        />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-foreground/35">
            {suffix}
          </span>
        )}
      </div>
      {invalid && (
        <p className="mt-1 text-xs text-rose-500">
          Must be between {min} and {max}.
        </p>
      )}
    </div>
  );
}

export function AutoApplyDrawer({
  listingName,
  settings,
  calendar,
  onClose,
  onSave,
  onDisable,
  onViewPreview,
}: AutoApplyDrawerProps) {
  const [mounted, setMounted] = useState(false);

  // Form state
  const [windowEndDays, setWindowEndDays] = useState(settings.windowEndDays);
  const [applyScope, setApplyScope] = useState<AutoApplySettings["applyScope"]>(settings.applyScope);
  const [floorRaw, setFloorRaw] = useState(
    settings.minPriceFloor != null ? String(settings.minPriceFloor) : ""
  );
  const [minNoticeDays, setMinNoticeDays] = useState(settings.minNoticeDays);
  const [maxIncreasePct, setMaxIncreasePct] = useState<number | null>(settings.maxIncreasePct);
  const [maxDecreasePct, setMaxDecreasePct] = useState<number | null>(settings.maxDecreasePct);
  const [skipUnavailable, setSkipUnavailable] = useState(settings.skipUnavailableNights);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const trimmedFloor = floorRaw.trim();
  const parsedFloor = trimmedFloor !== "" ? Number(trimmedFloor) : null;
  const floorInvalid =
    parsedFloor !== null && (!Number.isFinite(parsedFloor) || parsedFloor <= 0);
  const canSave = !floorInvalid;

  // Live preview — recomputed whenever form state or calendar changes
  const livePreview = useMemo(
    () =>
      computeAutoApplyPreview(
        calendar ?? [],
        {
          enabled: true,
          windowEndDays,
          applyScope,
          minPriceFloor: parsedFloor,
          minNoticeDays,
          maxIncreasePct,
          maxDecreasePct,
          skipUnavailableNights: skipUnavailable,
          lastUpdatedAt: null,
        }
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [calendar, windowEndDays, applyScope, parsedFloor, minNoticeDays, maxIncreasePct, maxDecreasePct, skipUnavailable]
  );

  function handleSave() {
    if (!canSave) return;
    onSave({
      windowEndDays,
      applyScope,
      minPriceFloor: parsedFloor,
      minNoticeDays,
      maxIncreasePct,
      maxDecreasePct,
      skipUnavailableNights: skipUnavailable,
    });
  }


  if (!mounted) return null;

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
        className="relative z-10 flex max-h-[90dvh] w-full max-w-md flex-col rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="aa-title"
      >
        {/* ── Header ── */}
        <div className="flex items-start justify-between px-6 pt-6 pb-4 border-b border-gray-100">
          <div className="mr-6">
            <h2
              id="aa-title"
              className="text-base font-bold tracking-tight text-foreground"
            >
              Auto-Apply settings
            </h2>
            <p className="mt-1 text-sm leading-snug text-foreground/50">
              {listingName}
            </p>
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            {settings.enabled && (
              <button
                type="button"
                onClick={() => { onDisable(); onClose(); }}
                className="text-xs font-medium text-foreground/35 transition-colors hover:text-rose-500"
              >
                Turn off
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="shrink-0 rounded-full p-1.5 text-foreground/30 transition-colors hover:bg-gray-100 hover:text-foreground/60"
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
        </div>

        {/* ── Scrollable body ── */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">

          {/* ── Section 1: Apply window ── */}
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-foreground/35">
              Apply window
            </p>

            <div className="space-y-1.5">
              <label htmlFor="aa-window" className="block text-sm font-medium text-foreground/70">
                Look ahead
              </label>
              <select
                id="aa-window"
                value={windowEndDays}
                onChange={(e) => setWindowEndDays(Number(e.target.value))}
                className="w-full rounded-xl border border-gray-200 bg-gray-50/60 px-3 py-2.5 text-sm outline-none transition-colors focus:border-blue-400 focus:bg-white"
              >
                <option value={7}>Next 7 nights</option>
                <option value={14}>Next 14 nights</option>
                <option value={30}>Next 30 nights</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="aa-notice" className="block text-sm font-medium text-foreground/70">
                Minimum notice
              </label>
              <select
                id="aa-notice"
                value={minNoticeDays}
                onChange={(e) => setMinNoticeDays(Number(e.target.value))}
                className="w-full rounded-xl border border-gray-200 bg-gray-50/60 px-3 py-2.5 text-sm outline-none transition-colors focus:border-blue-400 focus:bg-white"
              >
                <option value={0}>No minimum — apply to any night</option>
                <option value={1}>At least 1 day before check-in</option>
                <option value={2}>At least 2 days before check-in</option>
                <option value={3}>At least 3 days before check-in</option>
                <option value={7}>At least 7 days before check-in</option>
              </select>
              <p className="text-xs text-foreground/35">
                Prevents last-minute price changes before an imminent check-in.
              </p>
            </div>
          </div>

          {/* ── Section 2: Price rules ── */}
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-foreground/35">
              Price rules
            </p>

            {/* Min price floor — the key guardrail */}
            <div className="space-y-1.5">
              <label htmlFor="aa-floor" className="block text-sm font-medium text-foreground/70">
                Minimum nightly price{" "}
                <span className="font-normal text-foreground/35">(optional)</span>
              </label>
              <div className="relative">
                <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-foreground/40">
                  $
                </span>
                <input
                  id="aa-floor"
                  type="number"
                  min={1}
                  placeholder="e.g. 80"
                  value={floorRaw}
                  onChange={(e) => setFloorRaw(e.target.value)}
                  className="w-full rounded-xl border border-gray-200 bg-gray-50/60 py-2.5 pl-7 pr-3 text-sm outline-none transition-colors focus:border-blue-400 focus:bg-white"
                />
              </div>
              {trimmedFloor !== "" && floorInvalid && (
                <p className="text-xs text-rose-500">Must be a positive number.</p>
              )}
              {/* Critical UX copy — floor vs recommendation distinction */}
              <div className="rounded-lg border border-amber-100 bg-amber-50/60 px-3 py-2.5">
                <p className="text-xs leading-snug text-amber-800">
                  <span className="font-semibold">Guardrail, not override:</span> if our
                  recommendation falls below your minimum, we apply your minimum instead.
                  Your recommendation stays unchanged in all reports.
                </p>
              </div>
            </div>

            {/* Increase / decrease caps — two columns */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label htmlFor="aa-increase" className="block text-sm font-medium text-foreground/70">
                  Max increase
                </label>
                <NullableNumberInput
                  id="aa-increase"
                  value={maxIncreasePct}
                  onChange={setMaxIncreasePct}
                  placeholder="No cap"
                  min={1}
                  max={200}
                  suffix="%"
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="aa-decrease" className="block text-sm font-medium text-foreground/70">
                  Max decrease
                </label>
                <NullableNumberInput
                  id="aa-decrease"
                  value={maxDecreasePct}
                  onChange={setMaxDecreasePct}
                  placeholder="No cap"
                  min={1}
                  max={100}
                  suffix="%"
                />
              </div>
            </div>
            <p className="text-xs text-foreground/35 -mt-1">
              Maximum % above or below the recommendation. Leave blank for no cap.
            </p>
          </div>

          {/* ── Section 3: Scope ── */}
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-foreground/35">
              Scope
            </p>

            <div className="space-y-2">
              {(
                [
                  {
                    value: "actionable",
                    label: "Actionable nights only",
                    description:
                      "Only nights where your price is meaningfully above or below market.",
                  },
                  {
                    value: "all_sellable",
                    label: "All sellable nights",
                    description:
                      "Every available night in the window, whether or not it breaches a threshold.",
                  },
                ] as const
              ).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setApplyScope(opt.value)}
                  className={`flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
                    applyScope === opt.value
                      ? "border-blue-200 bg-blue-50/60"
                      : "border-gray-100 bg-gray-50/40 hover:bg-gray-100/60"
                  }`}
                >
                  <span
                    className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 transition-colors ${
                      applyScope === opt.value
                        ? "border-blue-500"
                        : "border-gray-300"
                    }`}
                  >
                    {applyScope === opt.value && (
                      <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
                    )}
                  </span>
                  <div>
                    <p className="text-sm font-medium text-foreground/80">{opt.label}</p>
                    <p className="mt-0.5 text-xs leading-snug text-foreground/45">
                      {opt.description}
                    </p>
                  </div>
                </button>
              ))}
            </div>

            {/* Skip unavailable — outer is a div to avoid button-in-button; Toggle is the interactive element */}
            <div
              onClick={() => setSkipUnavailable((v) => !v)}
              className="flex w-full cursor-pointer items-center justify-between rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3 transition-colors hover:bg-gray-100/60"
            >
              <div className="min-w-0 text-left">
                <p className="text-sm font-medium text-foreground/80">
                  Skip booked or unavailable nights
                </p>
                <p className="mt-0.5 text-xs leading-snug text-foreground/40">
                  Never suggest a price change for a night that cannot be sold.
                </p>
              </div>
              <Toggle
                checked={skipUnavailable}
                onChange={setSkipUnavailable}
                label="Skip unavailable nights"
              />
            </div>
          </div>

          {/* ── Live preview summary ── */}
          <div className="rounded-xl border border-gray-200 bg-gray-50/60 px-4 py-4 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wider text-foreground/35">
                Preview
              </p>
              {onViewPreview && livePreview.nightsIncluded > 0 && (
                <button
                  type="button"
                  onClick={() => onViewPreview(livePreview)}
                  className="text-[11px] font-medium text-foreground/40 transition-colors hover:text-foreground/70"
                >
                  View per-night detail →
                </button>
              )}
            </div>

            {livePreview.nightsIncluded === 0 ? (
              <p className="text-xs text-foreground/40">
                {livePreview.nightsWithData === 0
                  ? "No calendar data — run a pricing report first."
                  : "No nights to apply in this window."}
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/30">
                    Nights
                  </p>
                  <p className="mt-0.5 text-sm font-semibold text-foreground/70">
                    {livePreview.nightsIncluded}
                    <span className="text-xs font-normal text-foreground/35">
                      {" "}/ {livePreview.totalWindowNights}
                    </span>
                  </p>
                  {livePreview.nightsSkipped > 0 && (
                    <p className="text-[10px] text-foreground/35">
                      {livePreview.nightsSkipped} skipped
                    </p>
                  )}
                </div>
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/30">
                    Rec. range
                  </p>
                  <p className="mt-0.5 text-sm font-semibold text-foreground/70">
                    {livePreview.recommendedPriceRange
                      ? livePreview.recommendedPriceRange.min === livePreview.recommendedPriceRange.max
                        ? `$${livePreview.recommendedPriceRange.min}`
                        : `$${livePreview.recommendedPriceRange.min}–$${livePreview.recommendedPriceRange.max}`
                      : "—"}
                  </p>
                </div>
                <div>
                  <p
                    className={`text-[10px] font-semibold uppercase tracking-wider ${
                      livePreview.nightsFloored > 0 ? "text-amber-600/70" : "text-foreground/30"
                    }`}
                  >
                    Final range
                  </p>
                  <p
                    className={`mt-0.5 text-sm font-semibold ${
                      livePreview.nightsFloored > 0 ? "text-amber-800" : "text-foreground/70"
                    }`}
                  >
                    {livePreview.finalApplyPriceRange
                      ? livePreview.finalApplyPriceRange.min === livePreview.finalApplyPriceRange.max
                        ? `$${livePreview.finalApplyPriceRange.min}`
                        : `$${livePreview.finalApplyPriceRange.min}–$${livePreview.finalApplyPriceRange.max}`
                      : "—"}
                  </p>
                  {livePreview.nightsFloored > 0 && (
                    <p className="text-[10px] text-amber-600/70">
                      {livePreview.nightsFloored} floored
                    </p>
                  )}
                </div>
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/30">
                    Data coverage
                  </p>
                  <p className="mt-0.5 text-sm font-semibold text-foreground/70">
                    {livePreview.nightsWithData}
                    <span className="text-xs font-normal text-foreground/35">
                      {" "}/ {livePreview.totalWindowNights}
                    </span>
                  </p>
                </div>
              </div>
            )}

            <p className="text-[11px] text-foreground/30 border-t border-gray-100 pt-2">
              Preview only — no prices are changed until execution is enabled.
            </p>
          </div>
        </div>

        {/* ── Footer actions ── */}
        <div className="border-t border-gray-100 px-6 py-4 space-y-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="w-full rounded-xl bg-foreground py-3 text-sm font-semibold text-white transition-colors hover:bg-foreground/85 disabled:opacity-40"
          >
            Save settings
          </button>
          <button
            type="button"
            onClick={onClose}
            className="w-full rounded-xl py-2 text-sm text-foreground/35 transition-colors hover:text-foreground/60"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
