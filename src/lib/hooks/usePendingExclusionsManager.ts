"use client";

/**
 * Dashboard-level pending operation manager for comp Exclude / Promote actions.
 *
 * Why this exists:
 *   - Comp card actions use a delayed-PATCH model (6s undo window).
 *   - Pending operations cannot live in ComparableListingsSection state — switching
 *     listings or navigating away would silently drop them.
 *   - Browser `sendBeacon` is the only reliable flush path on `pagehide`.
 *
 * Behaviors:
 *   - Per-listing single batch.  Multiple Exclude/Promote actions within the 6s
 *     window merge into one toast and one PATCH (deduped by roomId).
 *   - Undo cancels the timer + rollbacks optimistic state — no PATCH fires.
 *   - Network failure → rollback + Retry toast.
 *   - 400 + conflictingIds → rollback + "Refresh to continue" toast.
 *   - `flushListing(id)`: cancel timer + send PATCH now (used before listing switch).
 *   - On `pagehide` / `visibilitychange='hidden'` / `beforeunload` →
 *     `navigator.sendBeacon('/api/listings/:id/flush-exclusions', body)` for
 *     every active batch, then clear timers.
 */

import { useCallback, useEffect, useRef } from "react";

import type {
  ComparableListing,
  ExcludedComp,
  PreferredComp,
} from "@/lib/schemas";
import { dismissToast, toast } from "@/components/ui/Toaster";

const FLUSH_DELAY_MS = 6000;

export interface ListingSnapshot {
  excludedComps: ExcludedComp[];
  preferredComps: PreferredComp[];
}

export interface PendingBatchContext {
  /**
   * Read the *current* listing state.  Used by the manager only at flush
   * time (timer expiry, navigation flush) — should be backed by a ref so
   * stale closures don't return outdated state 6 s after queue time.
   */
  getSnapshot: () => ListingSnapshot;
  /**
   * Optimistically apply a transform to local listings state.
   *
   * MUST be implemented as a functional setState so that two synchronous
   * `applyOptimistic` calls in the same tick both see the freshest prev
   * (otherwise the second click would overwrite the first).
   */
  applyOptimistic: (transform: (current: ListingSnapshot) => ListingSnapshot) => void;
  /** Roll back optimistic state to the given snapshot. */
  rollback: (original: ListingSnapshot) => void;
}

interface ActiveBatch {
  listingId: string;
  ctx: PendingBatchContext;
  /** Snapshot at the moment the batch first started — restored on rollback. */
  original: ListingSnapshot;
  /** Pending added exclusions (ExcludedComp objects). */
  excludeAdds: ExcludedComp[];
  /** Pending promoted comps to append to preferredComps. */
  promoteAdds: PreferredComp[];
  /** RoomIds removed from excluded list when promoting from excluded. */
  promoteUnexcludeRoomIds: Set<string>;
  /** setTimeout handle for the 6s flush. */
  timer: ReturnType<typeof setTimeout> | null;
  /** Active toast id (gets dismissed/replaced when the batch updates). */
  toastId: number | null;
  /** True once the timer has fired and PATCH is in flight — prevents double-flush. */
  flushing: boolean;
}

function extractRoomId(url: string): string | null {
  const m = url.match(/\/rooms\/(\d+)/);
  return m ? m[1] : null;
}

function buildPatchBody(batch: ActiveBatch): Record<string, unknown> {
  const snap = batch.ctx.getSnapshot();
  const body: Record<string, unknown> = {};
  if (batch.excludeAdds.length > 0 || batch.promoteUnexcludeRoomIds.size > 0) {
    body.excludedComps = snap.excludedComps;
  }
  if (batch.promoteAdds.length > 0) {
    body.preferredComps = snap.preferredComps;
  }
  return body;
}

function batchSummaryTitle(batch: ActiveBatch): string {
  const xn = batch.excludeAdds.length;
  const pn = batch.promoteAdds.length;
  if (xn > 0 && pn === 0) {
    return xn === 1
      ? `Hidden 1 comparable · Undo`
      : `${xn} comparables hidden · Undo`;
  }
  if (pn > 0 && xn === 0) {
    return pn === 1
      ? `Added 1 benchmark · Undo`
      : `${pn} benchmarks added · Undo`;
  }
  return `${xn + pn} changes pending · Undo`;
}

export interface UsePendingExclusionsManagerReturn {
  /** Queue an Exclude action.  Optimistic state is applied immediately. */
  queueExclude: (
    listingId: string,
    comp: ComparableListing,
    ctx: PendingBatchContext
  ) => void;
  /** Queue a Promote (Use as benchmark) action. */
  queuePromote: (
    listingId: string,
    comp: ComparableListing,
    ctx: PendingBatchContext,
    opts?: { unexcludeRoomId?: string }
  ) => void;
  /** Cancel the listing's pending batch, rollback optimistic state. */
  undoBatch: (listingId: string) => void;
  /**
   * Force-flush a listing's pending batch immediately (before navigation).
   * Returns a promise that resolves when PATCH completes.
   */
  flushListing: (listingId: string) => Promise<void>;
  /** Synchronous flush via sendBeacon for all batches.  Used in pagehide. */
  flushAllSync: () => void;
  /** True if any listing has a pending batch.  Used to show "saving" UI. */
  hasPending: () => boolean;
}

export function usePendingExclusionsManager(): UsePendingExclusionsManagerReturn {
  const batchesRef = useRef<Map<string, ActiveBatch>>(new Map());

  const dismissBatchToast = useCallback((batch: ActiveBatch) => {
    if (batch.toastId !== null) {
      dismissToast(batch.toastId);
      batch.toastId = null;
    }
  }, []);

  const undoBatch = useCallback(
    (listingId: string) => {
      const batch = batchesRef.current.get(listingId);
      if (!batch || batch.flushing) return;
      if (batch.timer) clearTimeout(batch.timer);
      dismissBatchToast(batch);
      batch.ctx.rollback(batch.original);
      batchesRef.current.delete(listingId);
    },
    [dismissBatchToast]
  );

  const fireFlush = useCallback(
    async (listingId: string): Promise<void> => {
      const batch = batchesRef.current.get(listingId);
      if (!batch) return;
      if (batch.flushing) return;
      if (batch.timer) {
        clearTimeout(batch.timer);
        batch.timer = null;
      }
      batch.flushing = true;
      dismissBatchToast(batch);

      const body = buildPatchBody(batch);
      // Capture optimistic snapshot now so Retry can re-apply if rollback happened.
      const optimisticSnap = batch.ctx.getSnapshot();

      try {
        const res = await fetch(`/api/listings/${listingId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (res.status === 400) {
          const errBody = await res.json().catch(() => ({}));
          if (
            errBody &&
            Array.isArray((errBody as { conflictingIds?: unknown }).conflictingIds)
          ) {
            batch.ctx.rollback(batch.original);
            batchesRef.current.delete(listingId);
            toast({
              title: "This listing changed elsewhere. Refresh to continue.",
              variant: "error",
              durationMs: 0,
              action: {
                label: "Refresh",
                onClick: () => {
                  if (typeof window !== "undefined") window.location.reload();
                },
                testId: "toast-refresh",
              },
            });
            return;
          }
        }
        if (!res.ok) {
          throw new Error(`PATCH /api/listings/${listingId} → ${res.status}`);
        }
        // Success: pending batch resolved, drop from map.
        batchesRef.current.delete(listingId);
      } catch {
        // Rollback optimistic state and offer Retry.
        batch.ctx.rollback(batch.original);
        batchesRef.current.delete(listingId);
        toast({
          title: "Connection failed. Changes not saved.",
          variant: "error",
          durationMs: 0,
          action: {
            label: "Retry",
            onClick: () => {
              // Re-apply the optimistic snapshot via a transform — the
              // caller's setState semantics still apply.
              batch.ctx.applyOptimistic(() => optimisticSnap);
              // Re-insert as a flushing batch (no timer — fire now).
              const retryBatch: ActiveBatch = {
                ...batch,
                timer: null,
                toastId: null,
                flushing: false,
              };
              batchesRef.current.set(listingId, retryBatch);
              fireFlush(listingId);
            },
            testId: "toast-retry",
          },
        });
      }
    },
    [dismissBatchToast]
  );

  const ensureBatch = useCallback(
    (listingId: string, ctx: PendingBatchContext): ActiveBatch => {
      let batch = batchesRef.current.get(listingId);
      if (batch && !batch.flushing) return batch;
      batch = {
        listingId,
        ctx,
        original: ctx.getSnapshot(),
        excludeAdds: [],
        promoteAdds: [],
        promoteUnexcludeRoomIds: new Set(),
        timer: null,
        toastId: null,
        flushing: false,
      };
      batchesRef.current.set(listingId, batch);
      return batch;
    },
    []
  );

  const refreshBatchTimerAndToast = useCallback(
    (batch: ActiveBatch) => {
      if (batch.timer) clearTimeout(batch.timer);
      batch.timer = setTimeout(() => {
        fireFlush(batch.listingId);
      }, FLUSH_DELAY_MS);
      // Replace the toast with the latest summary — "1 hidden" → "2 hidden" → ...
      if (batch.toastId !== null) dismissToast(batch.toastId);
      batch.toastId = toast({
        title: batchSummaryTitle(batch),
        durationMs: FLUSH_DELAY_MS,
        action: {
          label: "Undo",
          onClick: () => undoBatch(batch.listingId),
          testId: "toast-undo",
        },
      });
    },
    [fireFlush, undoBatch]
  );

  const queueExclude = useCallback(
    (listingId: string, comp: ComparableListing, ctx: PendingBatchContext) => {
      // Stable roomId is required.  Caller (UI) should already gate the
      // action button on this — but defense in depth.
      const idField = comp.id;
      const roomId =
        typeof idField === "string" && /^\d+$/.test(idField)
          ? idField
          : extractRoomId(comp.url ?? "");
      if (!roomId) return;
      const batch = ensureBatch(listingId, ctx);
      // Dedup by roomId.
      if (batch.excludeAdds.some((e) => e.roomId === roomId)) return;
      const entry: ExcludedComp = {
        roomId,
        listingUrl: comp.url ?? undefined,
        title: comp.title ?? undefined,
        excludedAt: new Date().toISOString(),
      };
      batch.excludeAdds.push(entry);
      // Apply optimistically via a transform — multiple rapid clicks in the
      // same tick must each see the freshest prev (functional setState).
      ctx.applyOptimistic((cur) => ({
        excludedComps: [...cur.excludedComps, entry],
        preferredComps: cur.preferredComps,
      }));
      refreshBatchTimerAndToast(batch);
    },
    [ensureBatch, refreshBatchTimerAndToast]
  );

  const queuePromote = useCallback(
    (
      listingId: string,
      comp: ComparableListing,
      ctx: PendingBatchContext,
      opts?: { unexcludeRoomId?: string }
    ) => {
      const url = comp.url ?? "";
      if (!url) return;
      const batch = ensureBatch(listingId, ctx);
      // Dedup by listingUrl.
      if (batch.promoteAdds.some((p) => p.listingUrl === url)) return;
      const entry: PreferredComp = {
        listingUrl: url,
        name: comp.title ?? undefined,
        enabled: true,
      };
      batch.promoteAdds.push(entry);
      if (opts?.unexcludeRoomId) {
        batch.promoteUnexcludeRoomIds.add(opts.unexcludeRoomId);
      }
      ctx.applyOptimistic((cur) => ({
        excludedComps: opts?.unexcludeRoomId
          ? cur.excludedComps.filter((e) => e.roomId !== opts.unexcludeRoomId)
          : cur.excludedComps,
        preferredComps: [...cur.preferredComps, entry],
      }));
      refreshBatchTimerAndToast(batch);
    },
    [ensureBatch, refreshBatchTimerAndToast]
  );

  const flushListing = useCallback(
    async (listingId: string): Promise<void> => {
      const batch = batchesRef.current.get(listingId);
      if (!batch || batch.flushing) return;
      await fireFlush(listingId);
    },
    [fireFlush]
  );

  const flushAllSync = useCallback(() => {
    if (typeof navigator === "undefined") return;
    for (const [listingId, batch] of batchesRef.current.entries()) {
      if (batch.flushing) continue;
      if (batch.timer) {
        clearTimeout(batch.timer);
        batch.timer = null;
      }
      // Send DELTA, not the full merged arrays.  The server reads the
      // current DB state and applies the delta there, so concurrent edits
      // from other tabs don't get clobbered by a stale full-array overwrite.
      const deltaBody = {
        excludeAdds: batch.excludeAdds,
        promoteAdds: batch.promoteAdds,
        promoteUnexcludeRoomIds: Array.from(batch.promoteUnexcludeRoomIds),
      };
      try {
        const blob = new Blob([JSON.stringify(deltaBody)], {
          type: "application/json",
        });
        navigator.sendBeacon(
          `/api/listings/${listingId}/flush-exclusions`,
          blob
        );
      } catch {
        // sendBeacon failures are non-fatal — user already left the page.
      }
      // Mark as flushed locally so a subsequent flushAllSync doesn't re-fire.
      batch.flushing = true;
    }
  }, []);

  const hasPending = useCallback(() => {
    for (const batch of batchesRef.current.values()) {
      if (!batch.flushing) return true;
    }
    return false;
  }, []);

  // Page-hide / unload listeners — three layers of defense in depth.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flushAllSync();
    };
    const onPageHide = () => {
      flushAllSync();
    };
    const onBeforeUnload = () => {
      flushAllSync();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [flushAllSync]);

  // On unmount: flush all (last-resort).  React guarantees this runs.
  useEffect(() => {
    const batches = batchesRef.current;
    return () => {
      for (const [, batch] of batches.entries()) {
        if (batch.timer) clearTimeout(batch.timer);
      }
      flushAllSync();
    };
  }, [flushAllSync]);

  return {
    queueExclude,
    queuePromote,
    undoBatch,
    flushListing,
    flushAllSync,
    hasPending,
  };
}
