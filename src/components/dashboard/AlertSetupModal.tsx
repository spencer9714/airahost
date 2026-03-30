"use client";

import { createPortal } from "react-dom";
import { useEffect, useRef, useState } from "react";

interface AlertSetupModalProps {
  listingName: string;
  initialUrl: string;
  initialMinNights: number;
  onClose: () => void;
  /**
   * Called with validated settings when the user clicks "Save and enable alerts".
   * Should persist to the server and throw on failure so the modal can surface
   * the error inline rather than silently closing.
   */
  onSave: (settings: {
    listingUrl: string;
    minimumBookingNights: number;
    pricingAlertsEnabled: true;
  }) => Promise<void>;
}

export function AlertSetupModal({
  listingName,
  initialUrl,
  initialMinNights,
  onClose,
  onSave,
}: AlertSetupModalProps) {
  const [mounted, setMounted] = useState(false);
  const [url, setUrl] = useState(initialUrl);
  const [minNights, setMinNights] = useState(initialMinNights);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const urlInputRef = useRef<HTMLInputElement>(null);

  // Render into document.body to escape any parent stacking context.
  useEffect(() => setMounted(true), []);

  // Focus URL input on mount.
  useEffect(() => {
    const t = setTimeout(() => urlInputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, []);

  // Close on Escape.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const urlTrimmed = url.trim();
  const urlValid = urlTrimmed.includes("airbnb.com/rooms/");
  const canSave = urlValid && !saving;

  async function handleSave() {
    if (!canSave) return;
    setSaving(true);
    setServerError(null);
    try {
      await onSave({
        listingUrl: urlTrimmed,
        minimumBookingNights: minNights,
        pricingAlertsEnabled: true,
      });
      onClose();
    } catch (err) {
      setServerError(
        err instanceof Error ? err.message : "Could not save alert settings."
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
        className="relative z-10 w-full max-w-md rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="alert-modal-title"
      >
        {/* Header */}
        <div className="flex items-start justify-between px-6 pb-0 pt-6">
          <div className="mr-6">
            <h2
              id="alert-modal-title"
              className="text-base font-bold tracking-tight text-foreground"
            >
              Set up pricing alerts
            </h2>
            <p className="mt-1.5 text-sm leading-snug text-foreground/50">
              Get nightly emails when your price drifts meaningfully above or
              below market for{" "}
              <span className="font-medium text-foreground/70">{listingName}</span>.
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
        <div className="space-y-4 px-6 pb-6 pt-5">
          {/* Airbnb listing URL */}
          <div className="space-y-1.5">
            <label
              htmlFor="alert-modal-url"
              className="block text-sm font-medium text-foreground/70"
            >
              Airbnb listing URL{" "}
              <span className="text-rose-400" aria-hidden="true">*</span>
            </label>
            <input
              id="alert-modal-url"
              ref={urlInputRef}
              type="url"
              placeholder="https://airbnb.com/rooms/…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleSave();
                }
              }}
              className="w-full rounded-xl border border-gray-200 bg-gray-50/60 px-3 py-2.5 font-mono text-sm outline-none transition-colors focus:border-blue-400 focus:bg-white"
            />
            {urlTrimmed && !urlValid && (
              <p className="text-xs text-rose-500">
                Must be an airbnb.com/rooms/… URL — copy it from your Airbnb
                listing page.
              </p>
            )}
            {urlValid && (
              <p className="text-xs text-emerald-600">Valid Airbnb listing URL</p>
            )}
            {!urlTrimmed && (
              <p className="text-xs text-foreground/35">
                Found in your Airbnb host dashboard under your listing.
              </p>
            )}
          </div>

          {/* Minimum booking nights */}
          <div className="space-y-1.5">
            <label
              htmlFor="alert-modal-nights"
              className="block text-sm font-medium text-foreground/70"
            >
              Minimum booking nights
            </label>
            <select
              id="alert-modal-nights"
              value={minNights}
              onChange={(e) => setMinNights(Number(e.target.value))}
              className="w-full rounded-xl border border-gray-200 bg-gray-50/60 px-3 py-2.5 text-sm outline-none transition-colors focus:border-blue-400 focus:bg-white"
            >
              {Array.from({ length: 30 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n} {n === 1 ? "night" : "nights"}
                </option>
              ))}
            </select>
            <p className="text-xs text-foreground/35">
              Match your Airbnb minimum-stay setting — used when checking your
              live price each night.
            </p>
          </div>

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
              disabled={!canSave}
              className="w-full rounded-xl bg-emerald-600 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
            >
              {saving ? "Saving…" : "Save and enable alerts"}
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
