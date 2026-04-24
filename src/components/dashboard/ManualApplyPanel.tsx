"use client";

import { createPortal } from "react-dom";
import { useEffect, useState } from "react";
import type { AutoApplyPreviewResult } from "@/lib/autoApplyPreview";

// â”€â”€ Types â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

interface NightApplyResult {
  date: string;
  applyStatus: "simulated_success" | "simulated_failure" | "skipped";
  finalAppliedPrice: number | null;
  errorMessage: string | null;
}

interface ManualApplyResponse {
  runId: string;
  executionMode?: "stub" | "live";
  executionModeNote?: string;
  nightsTotal?: number;
  nightsSimulatedSuccess?: number;
  nightsSimulatedFailed?: number;
  nightsSkipped?: number;
  nightsQueued?: number;
  nightsFloored: number;
  nightsCapped: number;
  rangeStart: string;
  rangeEnd: string;
  completedAt?: string;
  queuedAt?: string;
  status?: string;
  nights?: NightApplyResult[];
}

type Phase = "confirm" | "applying" | "result";

interface ManualApplyPanelProps {
  listingId: string;
  listingName: string;
  sourceReportId?: string | null;
  /** Preview that was shown to the user before they clicked Apply. */
  preview: AutoApplyPreviewResult;
  /** Subset of night dates the user selected in the preview panel. */
  selectedDates?: string[];
  onClose: () => void;
  /** Navigate back to the preview panel. */
  onBack: () => void;
}

// â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
  if (n == null) return "â€”";
  return `$${n}`;
}

// â”€â”€ Sub-components â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function StubBanner({ note }: { note: string }) {
  return (
    <div className="rounded-xl border border-amber-100 bg-amber-50/70 px-4 py-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0 text-amber-500" aria-hidden="true">
          {/* warning icon */}
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <path
              d="M6.5 1.5L12 11.5H1L6.5 1.5Z"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinejoin="round"
            />
            <path d="M6.5 5v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            <circle cx="6.5" cy="9.5" r="0.6" fill="currentColor" />
          </svg>
        </span>
        <p className="text-xs leading-snug text-amber-800">{note}</p>
      </div>
    </div>
  );
}

function ConfirmPhase({
  preview,
  selectedDates,
  applying,
  onConfirm,
  onBack,
}: {
  preview: AutoApplyPreviewResult;
  selectedDates?: string[];
  applying: boolean;
  onConfirm: () => void;
  onBack: () => void;
}) {
  const selectedSet = selectedDates ? new Set(selectedDates) : null;
  const includedNights = preview.nights.filter(
    (n) => !n.skipped && (selectedSet === null || selectedSet.has(n.date))
  );

  return (
    <>
      {/* â”€â”€ Summary stats â”€â”€ */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
            Nights to apply
          </p>
          <p className="mt-0.5 text-sm font-semibold text-foreground/70">
            {includedNights.length}
          </p>
          <p className="mt-0.5 text-xs text-foreground/40">
            {preview.nightsIncluded > includedNights.length
              ? `${preview.nightsIncluded - includedNights.length} deselected`
              : preview.nightsSkipped > 0
              ? `${preview.nightsSkipped} skipped`
              : "all included"}
          </p>
        </div>

        <div
          className={`rounded-xl border px-4 py-3 ${
            preview.nightsFloored > 0
              ? "border-amber-100 bg-amber-50/60"
              : "border-gray-100 bg-gray-50/60"
          }`}
        >
          <p
            className={`text-[10px] font-semibold uppercase tracking-wider ${
              preview.nightsFloored > 0 ? "text-amber-600/70" : "text-foreground/35"
            }`}
          >
            Price range
          </p>
          <p
            className={`mt-0.5 text-sm font-semibold ${
              preview.nightsFloored > 0 ? "text-amber-800" : "text-foreground/70"
            }`}
          >
            {preview.finalApplyPriceRange
              ? preview.finalApplyPriceRange.min === preview.finalApplyPriceRange.max
                ? fmtPrice(preview.finalApplyPriceRange.min)
                : `${fmtPrice(preview.finalApplyPriceRange.min)} â€“ ${fmtPrice(
                    preview.finalApplyPriceRange.max
                  )}`
              : "â€”"}
          </p>
          {preview.nightsFloored > 0 && (
            <p className="mt-0.5 text-xs text-amber-600/70">
              {preview.nightsFloored} floored to minimum
            </p>
          )}
        </div>
      </div>

      {/* â”€â”€ Stub mode banner â”€â”€ */}
      <StubBanner note="Airbnb sync is not yet enabled. Clicking 'Apply' will prepare a plan and record the run â€” no prices will actually change on your listing." />

      {/* â”€â”€ Included nights table â”€â”€ */}
      {includedNights.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground/35">
            Would apply
          </p>
          <div className="overflow-hidden rounded-xl border border-gray-100">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50/80">
                  <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                    Date
                  </th>
                  <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                    Rec.
                  </th>
                  <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                    Would apply
                  </th>
                </tr>
              </thead>
              <tbody>
                {includedNights.map((n) => (
                  <tr
                    key={n.date}
                    className="border-b border-gray-50 last:border-0"
                  >
                    <td className="px-3 py-2 text-[12px] font-medium text-foreground/70">
                      {fmtDate(n.date)}
                    </td>
                    <td className="px-3 py-2 text-right text-[12px] text-foreground/40">
                      {fmtPrice(n.recommendedPrice)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span
                        className={`text-[12px] font-semibold ${
                          n.adjustmentReason === "floored" ||
                          n.adjustmentReason === "floored_and_capped"
                            ? "text-amber-700"
                            : "text-foreground/70"
                        }`}
                      >
                        {fmtPrice(n.finalAutoApplyPrice)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* â”€â”€ Footer â”€â”€ */}
      <div className="flex items-center justify-between border-t border-gray-100 pt-4">
        <button
          type="button"
          onClick={onBack}
          disabled={applying}
          className="text-sm font-medium text-foreground/40 transition-colors hover:text-foreground/70 disabled:opacity-40"
        >
          â† Back to preview
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={applying || includedNights.length === 0}
          className="rounded-xl bg-foreground px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-foreground/85 disabled:opacity-40"
        >
          {applying ? "Preparingâ€¦" : `Apply ${includedNights.length} nights`}
        </button>
      </div>
    </>
  );
}

function ResultPhase({
  result,
  onClose,
}: {
  result: ManualApplyResponse;
  onClose: () => void;
}) {
  const nights = result.nights ?? [];
  const successNights = nights.filter(
    (n) => n.applyStatus === "simulated_success"
  );
  const preparedCount = result.nightsSimulatedSuccess ?? result.nightsQueued ?? successNights.length;
  const skippedCount = result.nightsSkipped ?? 0;
  const executionModeNote =
    result.executionModeNote ??
    "Run queued. The worker will apply prices asynchronously.";

  return (
    <>
      {/* â”€â”€ Summary stats â”€â”€ */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
            Nights prepared
          </p>
          <p className="mt-0.5 text-sm font-semibold text-foreground/70">
            {preparedCount}
          </p>
          <p className="mt-0.5 text-xs text-foreground/40">would be applied</p>
        </div>

        <div className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
            Skipped
          </p>
          <p className="mt-0.5 text-sm font-semibold text-foreground/70">
            {skippedCount}
          </p>
          <p className="mt-0.5 text-xs text-foreground/40">
            {result.nightsFloored > 0
              ? `${result.nightsFloored} floored to minimum`
              : "no data or notice window"}
          </p>
        </div>

        {result.nightsFloored > 0 && (
          <div className="rounded-xl border border-amber-100 bg-amber-50/60 px-4 py-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-600/70">
              Floored
            </p>
            <p className="mt-0.5 text-sm font-semibold text-amber-800">
              {result.nightsFloored}
            </p>
            <p className="mt-0.5 text-xs text-amber-600/70">applied at minimum price</p>
          </div>
        )}

        {result.nightsCapped > 0 && (
          <div className="rounded-xl border border-blue-100 bg-blue-50/60 px-4 py-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-blue-600/70">
              Cap applied
            </p>
            <p className="mt-0.5 text-sm font-semibold text-blue-800">
              {result.nightsCapped}
            </p>
            <p className="mt-0.5 text-xs text-blue-600/70">within increase/decrease cap</p>
          </div>
        )}
      </div>

      {/* â”€â”€ Stub mode banner â”€â”€ */}
      <StubBanner note={executionModeNote} />

      {/* â”€â”€ Execution mode badge â”€â”€ */}
      <div className="flex items-center gap-2 rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-2.5">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
          <p className="text-xs text-foreground/50">
            <span className="font-semibold">Execution mode:</span>{" "}
            {result.executionMode === "live"
              ? "live"
              : result.executionMode === "stub"
              ? "preview-only stub"
              : "queued job"}
            {" Â· "}
            <span className="font-mono text-[10px] text-foreground/35">{result.runId.slice(0, 8)}</span>
          </p>
        </div>

      {/* â”€â”€ Per-night results â”€â”€ */}
      {successNights.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground/35">
            Per-night results
          </p>
          <div className="overflow-hidden rounded-xl border border-gray-100">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50/80">
                  <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                    Date
                  </th>
                  <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                    Would apply
                  </th>
                  <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider text-foreground/35">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {successNights.map((n) => (
                  <tr key={n.date} className="border-b border-gray-50 last:border-0">
                    <td className="px-3 py-2 text-[12px] font-medium text-foreground/70">
                      {fmtDate(n.date)}
                    </td>
                    <td className="px-3 py-2 text-right text-[12px] font-semibold text-foreground/70">
                      {fmtPrice(n.finalAppliedPrice)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-foreground/40">
                        Simulated
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* â”€â”€ Footer â”€â”€ */}
      <div className="flex justify-end border-t border-gray-100 pt-4">
        <button
          type="button"
          onClick={onClose}
          className="rounded-xl bg-foreground px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-foreground/85"
        >
          Close
        </button>
      </div>
    </>
  );
}

// â”€â”€ Main panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export function ManualApplyPanel({
  listingId,
  listingName,
  sourceReportId = null,
  preview,
  selectedDates,
  onClose,
  onBack,
}: ManualApplyPanelProps) {
  const [mounted, setMounted] = useState(false);
  const [phase, setPhase] = useState<Phase>("confirm");
  const [result, setResult] = useState<ManualApplyResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && phase !== "applying") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, phase]);

  async function handleConfirm() {
    setPhase("applying");
    setErrorMessage(null);
    try {
      const res = await fetch(`/api/listings/${listingId}/manual-apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selectedDates: selectedDates ?? null,
          sourceReportId,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Apply failed.");
      }
      const data: ManualApplyResponse = await res.json();
      setResult(data);
      setPhase("result");
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "An unexpected error occurred.");
      setPhase("confirm");
    }
  }

  if (!mounted) return null;

  const phaseTitle: Record<Phase, string> = {
    confirm: "Apply prices",
    applying: "Applyingâ€¦",
    result: "Run complete",
  };

  const phaseSubtitle: Record<Phase, string> = {
    confirm: "Manual apply Â· preview-only stub",
    applying: "Preparing execution planâ€¦",
    result: "Airbnb sync not enabled â€” no prices changed",
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40"
        onClick={phase !== "applying" ? onClose : undefined}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        className="relative z-10 flex max-h-[90dvh] w-full max-w-lg flex-col rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ma-title"
      >
        {/* â”€â”€ Header â”€â”€ */}
        <div className="flex items-start justify-between border-b border-gray-100 px-6 pt-6 pb-4">
          <div className="mr-6">
            <div className="flex items-center gap-2">
              <h2
                id="ma-title"
                className="text-base font-bold tracking-tight text-foreground"
              >
                {phaseTitle[phase]}
              </h2>
              {phase === "result" && (
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-foreground/40">
                  Stub
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-foreground/50">{listingName}</p>
            <p className="text-xs text-foreground/35">{phaseSubtitle[phase]}</p>
          </div>
          {phase !== "applying" && (
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
          )}
        </div>

        {/* â”€â”€ Body â”€â”€ */}
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
          {/* Error message (shown when execution fails) */}
          {errorMessage && (
            <div className="rounded-xl border border-rose-100 bg-rose-50/60 px-4 py-3">
              <p className="text-xs font-medium text-rose-700">{errorMessage}</p>
            </div>
          )}

          {phase === "applying" ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-gray-100 bg-gray-50/70 py-14">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-200 border-t-foreground/60" />
              <p className="mt-4 text-sm font-medium text-foreground/70">Assigning prices...</p>
              <p className="mt-1 text-xs text-foreground/45">
                Please keep this window open until the run completes.
              </p>
            </div>
          ) : phase === "result" && result ? (
            <ResultPhase result={result} onClose={onClose} />
          ) : (
            <ConfirmPhase
              preview={preview}
              selectedDates={selectedDates}
              applying={false}
              onConfirm={handleConfirm}
              onBack={onBack}
            />
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}

