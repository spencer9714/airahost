"use client";

/**
 * Lightweight "replace benchmark" picker.
 *
 * Used when the user clicks "Use as benchmark" on a comparable card while
 * preferredComps is already at the 10-item cap.  Lists the 10 existing
 * benchmarks; clicking one swaps it for the new candidate.
 *
 * Intentionally lighter than `BenchmarkModal` (which is a full editor) — the
 * task here is "pick one to replace", not "edit benchmarks".
 */

import { useEffect } from "react";
import { createPortal } from "react-dom";

import type { ComparableListing, PreferredComp } from "@/lib/schemas";

interface Props {
  open: boolean;
  /** Existing benchmarks (must be exactly 10 when this sheet shows). */
  benchmarks: PreferredComp[];
  /** The comp the user just chose to promote. */
  candidate: ComparableListing | null;
  /** User picked which existing entry to swap.  index = position in benchmarks. */
  onReplace: (replaceIndex: number) => void;
  onClose: () => void;
}

function extractRoomId(url: string | null | undefined): string | null {
  if (!url) return null;
  const m = url.match(/\/rooms\/(\d+)/);
  return m ? m[1] : null;
}

function labelForBenchmark(pc: PreferredComp): string {
  if (pc.name?.trim()) return pc.name.trim();
  const rid = extractRoomId(pc.listingUrl);
  return rid ? `Room ${rid}` : "Airbnb listing";
}

export function ReplaceBenchmarkSheet({
  open,
  benchmarks,
  candidate,
  onReplace,
  onClose,
}: Props) {
  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;

  const candidateLabel =
    candidate?.title?.trim() ||
    (candidate?.url ? `Room ${extractRoomId(candidate.url) ?? ""}` : "the new comp");

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="replace-benchmark-title"
      data-testid="replace-benchmark-sheet"
      className="fixed inset-0 z-[90] flex items-center justify-center px-4"
    >
      <div
        className="absolute inset-0 bg-gray-900/40"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl bg-white shadow-xl ring-1 ring-gray-200">
        <div className="border-b border-gray-100 px-5 py-3">
          <h2
            id="replace-benchmark-title"
            className="text-sm font-semibold text-gray-900"
          >
            Replace a benchmark
          </h2>
          <p className="mt-0.5 text-xs text-foreground/55">
            Your benchmarks list is full (10 / 10).  Pick one to swap with{" "}
            <span className="font-medium text-foreground/75">{candidateLabel}</span>.
          </p>
        </div>
        <ul className="max-h-80 divide-y divide-gray-100 overflow-y-auto">
          {benchmarks.map((pc, idx) => (
            <li key={pc.listingUrl}>
              <button
                type="button"
                data-testid={`replace-benchmark-row-${idx}`}
                onClick={() => onReplace(idx)}
                className="flex w-full items-center gap-3 px-5 py-2.5 text-left transition hover:bg-amber-50"
              >
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    idx === 0 ? "bg-amber-400" : "bg-gray-300"
                  }`}
                  aria-hidden="true"
                />
                <span className="flex-1 truncate text-sm text-foreground/75">
                  {labelForBenchmark(pc)}
                </span>
                {idx === 0 && (
                  <span className="shrink-0 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-amber-200">
                    Primary
                  </span>
                )}
                {pc.enabled === false && (
                  <span className="shrink-0 text-[10px] uppercase tracking-wide text-foreground/35">
                    Off
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
        <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
          <button
            type="button"
            data-testid="replace-benchmark-cancel"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-xs font-medium text-foreground/55 transition hover:bg-gray-100"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
