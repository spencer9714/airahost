"use client";

/**
 * Lightweight toast primitive.
 *
 * Usage:
 *   - Mount `<Toaster />` once at the root of the app (src/app/layout.tsx).
 *   - From any component, call `toast({ title, action?, durationMs? })`.
 *
 * Style: minimal, single queue, portal-based, auto-dismiss. Optional Undo /
 * Retry / Refresh action button. No npm dependency.
 */

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

interface ToastOptions {
  /** Main toast text. */
  title: string;
  /** Optional secondary action (Undo, Retry, Refresh, …). */
  action?: {
    label: string;
    onClick: () => void;
    /** Optional testid for E2E (e.g. "toast-undo"). */
    testId?: string;
  };
  /** Auto-dismiss duration in ms. Default 6000. Pass 0 for sticky. */
  durationMs?: number;
  /** Visual variant. */
  variant?: "default" | "error";
}

interface ToastEntry extends ToastOptions {
  id: number;
}

type Listener = (toasts: ToastEntry[]) => void;

let nextId = 1;
let toasts: ToastEntry[] = [];
const listeners = new Set<Listener>();

function notify() {
  for (const fn of listeners) fn([...toasts]);
}

export function toast(opts: ToastOptions): number {
  const id = nextId++;
  const entry: ToastEntry = { id, ...opts };
  toasts = [...toasts, entry];
  notify();
  const dur = opts.durationMs ?? 6000;
  if (dur > 0) {
    setTimeout(() => dismissToast(id), dur);
  }
  return id;
}

export function dismissToast(id: number) {
  const before = toasts.length;
  toasts = toasts.filter((t) => t.id !== id);
  if (toasts.length !== before) notify();
}

export function Toaster() {
  const [items, setItems] = useState<ToastEntry[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    const listener: Listener = (next) => setItems(next);
    listeners.add(listener);
    listener([...toasts]);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  if (!mounted) return null;

  return createPortal(
    <div
      data-testid="toaster"
      className="pointer-events-none fixed bottom-4 left-1/2 z-[100] flex w-full max-w-sm -translate-x-1/2 flex-col gap-2 px-4 sm:bottom-6"
    >
      {items.map((t) => (
        <div
          key={t.id}
          data-testid={`toast-${t.id}`}
          className={`pointer-events-auto flex items-center justify-between gap-3 rounded-lg px-4 py-2.5 text-sm shadow-md ring-1 transition ${
            t.variant === "error"
              ? "bg-rose-50 text-rose-900 ring-rose-200"
              : "bg-gray-900 text-white ring-gray-800"
          }`}
        >
          <span className="truncate">{t.title}</span>
          <div className="flex shrink-0 items-center gap-2">
            {t.action && (
              <button
                type="button"
                data-testid={t.action.testId}
                onClick={() => {
                  t.action!.onClick();
                  dismissToast(t.id);
                }}
                className={`rounded px-2 py-0.5 text-xs font-medium transition ${
                  t.variant === "error"
                    ? "bg-rose-100 text-rose-900 hover:bg-rose-200"
                    : "bg-white/10 text-white hover:bg-white/20"
                }`}
              >
                {t.action.label}
              </button>
            )}
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismissToast(t.id)}
              className={`rounded px-1 text-xs opacity-60 transition hover:opacity-100`}
            >
              ✕
            </button>
          </div>
        </div>
      ))}
    </div>,
    document.body
  );
}
