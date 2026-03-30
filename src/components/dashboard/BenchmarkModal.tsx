"use client";

import { createPortal } from "react-dom";
import { useEffect, useState } from "react";

export interface BenchmarkComp {
  listingUrl: string;
  name?: string;
  note?: string;
  enabled?: boolean;
}

interface BenchmarkModalProps {
  listing: { id: string; name: string };
  initialComps: BenchmarkComp[];
  onClose: () => void;
  onSave: (comps: BenchmarkComp[]) => Promise<void>;
}

function normalizeAirbnbUrl(url: string): string {
  const match = url.match(/airbnb\.com\/rooms\/(\d+)/);
  return match ? `https://www.airbnb.com/rooms/${match[1]}` : url;
}

function shortenUrl(raw: string): string {
  try {
    const u = new URL(raw);
    const path = u.pathname.replace(/\/$/, "");
    const full = u.hostname + path;
    return full.length > 40 ? full.slice(0, 40) + "…" : full;
  } catch {
    return raw.length > 40 ? raw.slice(0, 40) + "…" : raw;
  }
}

export function BenchmarkModal({

  initialComps,
  onClose,
  onSave,
}: BenchmarkModalProps) {
  const [mounted, setMounted] = useState(false);

  // Draft state: always keep at least one row
  const [drafts, setDrafts] = useState<BenchmarkComp[]>(() =>
    initialComps.length > 0 ? initialComps : [{ listingUrl: "", enabled: true }]
  );

  // Which row is in URL-edit mode
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  // Ephemeral URL being typed before commit
  const [editingUrl, setEditingUrl] = useState("");

  // Which rows are currently fetching their title
  const [fetchingTitles, setFetchingTitles] = useState<Set<number>>(new Set());

  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => setMounted(true), []);

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        if (editingIdx !== null) {
          setEditingIdx(null);
        } else {
          onClose();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, editingIdx]);

  async function fetchTitle(url: string, idx: number) {
    setFetchingTitles((prev) => new Set([...prev, idx]));
    try {
      const res = await fetch(
        `/api/benchmark-title?url=${encodeURIComponent(url)}`
      );
      if (res.ok) {
        const data = (await res.json()) as { title: string | null };
        setDrafts((prev) =>
          prev.map((d, i) =>
            i === idx
              ? { ...d, name: data.title ?? undefined }
              : d
          )
        );
      }
    } finally {
      setFetchingTitles((prev) => {
        const next = new Set(prev);
        next.delete(idx);
        return next;
      });
    }
  }

  function startEdit(idx: number) {
    setEditingIdx(idx);
    setEditingUrl(drafts[idx]?.listingUrl ?? "");
  }

  function cancelEdit() {
    setEditingIdx(null);
    setEditingUrl("");
  }

  async function commitUrlEdit(idx: number) {
    const normalized = normalizeAirbnbUrl(editingUrl.trim());
    const isValid = normalized.includes("airbnb.com/rooms/");

    setDrafts((prev) =>
      prev.map((d, i) =>
        i === idx
          ? {
              ...d,
              listingUrl: normalized,
              // Clear name when URL changes so stale title isn't kept
              name: isValid ? undefined : d.name,
              enabled: true,
            }
          : d
      )
    );
    setEditingIdx(null);
    setEditingUrl("");

    if (isValid) {
      await fetchTitle(normalized, idx);
    }
  }

  function addBenchmark() {
    const newIdx = drafts.length;
    setDrafts((prev) => [...prev, { listingUrl: "", enabled: true }]);
    setEditingIdx(newIdx);
    setEditingUrl("");
  }

  function removeBenchmark(idx: number) {
    setDrafts((prev) => {
      const next = prev.filter((_, i) => i !== idx);
      return next.length > 0 ? next : [{ listingUrl: "", enabled: true }];
    });
    if (editingIdx === idx) {
      setEditingIdx(null);
    } else if (editingIdx !== null && editingIdx > idx) {
      setEditingIdx(editingIdx - 1);
    }
  }

  function makePrimary(idx: number) {
    setDrafts((prev) => {
      const next = [...prev];
      const [picked] = next.splice(idx, 1);
      next.unshift(picked);
      return next;
    });
  }

  async function handleSave() {
    if (saving) return;
    setSaving(true);
    setServerError(null);
    try {
      // Only persist comps that have a valid Airbnb URL
      const valid = drafts.filter((d) =>
        d.listingUrl.includes("airbnb.com/rooms/")
      );
      await onSave(valid);
      onClose();
    } catch (err) {
      setServerError(
        err instanceof Error ? err.message : "Could not save benchmarks."
      );
    } finally {
      setSaving(false);
    }
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

      {/* Modal card */}
      <div
        className="relative z-10 w-full max-w-lg rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="benchmark-modal-title"
      >
        {/* Header */}
        <div className="flex items-start justify-between px-6 pb-0 pt-6">
          <div className="mr-6">
            <h2
              id="benchmark-modal-title"
              className="text-base font-bold tracking-tight text-foreground"
            >
              Benchmark listings
            </h2>
            <p className="mt-1 text-sm leading-snug text-foreground/50">
              Compare your pricing against these Airbnb listings each night.
            </p>
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

        {/* Body */}
        <div className="space-y-3 px-6 pb-6 pt-5">
          {/* Benchmark rows */}
          <div className="space-y-2">
            {drafts.map((comp, idx) => {
              const isEditing = editingIdx === idx;
              const hasValidUrl = comp.listingUrl.includes("airbnb.com/rooms/");
              const isFetching = fetchingTitles.has(idx);
              const displayName = comp.name
                ? comp.name
                : isFetching
                ? "Fetching title…"
                : hasValidUrl
                ? "Airbnb listing"
                : comp.listingUrl.trim()
                ? "Invalid URL"
                : "New benchmark";
              const isPrimary = idx === 0 && hasValidUrl;

              // URL-edit inline validation
              const editNormalized = normalizeAirbnbUrl(editingUrl.trim());
              const editValid = editNormalized.includes("airbnb.com/rooms/");

              return (
                <div
                  key={idx}
                  className={`rounded-xl border transition-colors ${
                    isEditing
                      ? "border-gray-300 bg-white shadow-sm"
                      : "border-gray-200/70 bg-gray-50/60"
                  }`}
                >
                  {/* Row header */}
                  <div className="flex items-start gap-3 px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p
                          className={`text-sm font-semibold ${
                            hasValidUrl
                              ? "text-foreground/80"
                              : "text-foreground/35"
                          }`}
                        >
                          {displayName}
                        </p>
                        {isPrimary && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                            Primary
                          </span>
                        )}
                      </div>
                      {hasValidUrl && !isEditing && (
                        <div className="mt-0.5 flex items-center gap-1.5">
                          <p className="font-mono text-xs text-foreground/35 truncate max-w-xs">
                            {shortenUrl(comp.listingUrl)}
                          </p>
                          <a
                            href={comp.listingUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="shrink-0 text-xs text-blue-400 hover:text-blue-600 transition-colors"
                            onClick={(e) => e.stopPropagation()}
                          >
                            Open ↗
                          </a>
                        </div>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex shrink-0 items-center gap-3 pt-0.5">
                      {idx > 0 && hasValidUrl && !isEditing && (
                        <button
                          type="button"
                          onClick={() => makePrimary(idx)}
                          className="text-xs text-foreground/35 transition-colors hover:text-foreground/65"
                        >
                          Set primary
                        </button>
                      )}
                      {!isEditing && (
                        <button
                          type="button"
                          onClick={() => startEdit(idx)}
                          className="text-xs font-medium text-foreground/40 transition-colors hover:text-foreground/70"
                        >
                          Edit
                        </button>
                      )}
                      <button
                        type="button"
                        aria-label="Remove benchmark"
                        onClick={() => removeBenchmark(idx)}
                        className="text-sm leading-none text-foreground/20 transition-colors hover:text-rose-400"
                      >
                        ×
                      </button>
                    </div>
                  </div>

                  {/* Inline URL editor */}
                  {isEditing && (
                    <div className="border-t border-gray-100 px-4 pb-4 pt-3 space-y-2">
                      <label className="block text-xs font-medium text-foreground/50">
                        Airbnb URL
                      </label>
                      <div className="flex items-center gap-1.5">
                        <input
                          type="url"
                          autoFocus
                          placeholder="https://airbnb.com/rooms/…"
                          value={editingUrl}
                          onChange={(e) => setEditingUrl(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              void commitUrlEdit(idx);
                            }
                            if (e.key === "Escape") {
                              e.stopPropagation();
                              cancelEdit();
                            }
                          }}
                          className="flex-1 rounded-lg border border-gray-200 bg-white px-2.5 py-2 font-mono text-xs outline-none focus:border-blue-400"
                        />
                        {editValid && (
                          <a
                            href={editNormalized}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="shrink-0 rounded-lg border border-gray-200/60 bg-white px-2 py-2 text-xs text-blue-400 transition-colors hover:bg-blue-50/60"
                            title="Open listing"
                          >
                            ↗
                          </a>
                        )}
                      </div>
                      {editingUrl.trim() && !editValid && (
                        <p className="text-xs text-rose-500">
                          Must be an airbnb.com/rooms/… URL.
                        </p>
                      )}
                      <div className="flex gap-2 pt-1">
                        <button
                          type="button"
                          onClick={() => void commitUrlEdit(idx)}
                          className="rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-gray-800"
                        >
                          {isFetching ? "Fetching title…" : "Save URL"}
                        </button>
                        <button
                          type="button"
                          onClick={cancelEdit}
                          className="rounded-lg px-3 py-1.5 text-xs text-foreground/40 transition-colors hover:text-foreground/65"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Add benchmark */}
          {drafts.length < 10 && (
            <button
              type="button"
              onClick={addBenchmark}
              className="w-full rounded-xl border border-dashed border-gray-200 py-2.5 text-sm font-medium text-foreground/40 transition-colors hover:border-gray-300 hover:text-foreground/65"
            >
              + Add benchmark
            </button>
          )}

          {/* Server error */}
          {serverError && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3">
              <p className="text-sm text-rose-600">{serverError}</p>
            </div>
          )}

          {/* Actions */}
          <div className="space-y-2 pt-1">
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className="w-full rounded-xl bg-gray-900 py-3 text-sm font-semibold text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
            >
              {saving ? "Saving…" : "Save"}
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
      </div>
    </div>,
    document.body
  );
}
