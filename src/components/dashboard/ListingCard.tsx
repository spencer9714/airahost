import { useEffect, useRef, useState } from "react";
import type {
  CalendarDay,
  ExcludedComp,
  RecommendedPrice,
} from "@/lib/schemas";
import { computeFreshness, resolveMarketCapturedAt } from "@/lib/freshness";
import { dismissToast, toast } from "@/components/ui/Toaster";
import { AutoApplyFeature } from "./AutoApplyFeature";
import { AlertSetupModal } from "./AlertSetupModal";

type LatestReport = {
  id: string;
  share_id: string;
  status: "ready";
  report_type?: "live_analysis" | "forecast_snapshot" | string;
  source_report_id?: string | null;
  created_at: string;
  completed_at?: string | null;
  market_captured_at?: string | null;
  input_date_start: string;
  input_date_end: string;
  result_summary: {
    nightlyMin?: number;
    nightlyMedian?: number;
    nightlyMax?: number;
    recommendedPrice?: RecommendedPrice;
  } | null;
  result_calendar?: CalendarDay[];
} | null;

type ActiveJob = {
  status: "queued" | "running" | "error";
  linkedAt: string;
  shareId: string | null;
} | null;

type ListingData = {
  id: string;
  name: string;
  input_address: string;
  input_attributes: {
    propertyType?: string;
    bedrooms?: number;
    bathrooms?: number;
    maxGuests?: number;
    beds?: number;
    listingUrl?: string | null;
    preferredComps?: Array<{ listingUrl: string; name?: string; note?: string; enabled?: boolean }> | null;
    excludedComps?: ExcludedComp[] | null;
  };
  latestReport: LatestReport;
  latestLinkedAt: string | null;
  latestTrigger?: "scheduled" | "manual" | "rerun" | null;
  activeJob: ActiveJob;
  // Pricing alert fields (migration 014)
  pricing_alerts_enabled?: boolean;
  last_alert_sent_at?: string | null;
  last_alert_direction?: string | null;
  last_live_price_status?: string | null;
  // Alert v2 fields (migration 015)
  minimum_booking_nights?: number;
  listing_url_validation_status?: string | null;
  // Auto-Apply settings (migration 017)
  auto_apply_enabled?: boolean;
  auto_apply_window_end_days?: number;
  auto_apply_scope?: "actionable" | "all_sellable";
  auto_apply_min_price_floor?: number | null;
  auto_apply_min_notice_days?: number;
  auto_apply_max_increase_pct?: number | null;
  auto_apply_max_decrease_pct?: number | null;
  auto_apply_skip_unavailable?: boolean;
  auto_apply_last_updated_at?: string | null;
  // Co-host verification model (migration 020)
  auto_apply_cohost_status?: string;
  auto_apply_cohost_confirmed_at?: string | null;
  auto_apply_cohost_verified_at?: string | null;
  auto_apply_cohost_verification_error?: string | null;
  auto_apply_cohost_verification_method?: string | null;
};

interface AlertSettings {
  listingUrl?: string | null;
  minimumBookingNights?: number;
  pricingAlertsEnabled?: boolean;
}

interface Props {
  listing: ListingData;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onViewHistory: () => void;
  onRename: (listingId: string, nextName: string) => Promise<void>;
  onSavePreferredComps: (
    listingId: string,
    preferredComps: Array<{ listingUrl: string; name?: string; note?: string; enabled?: boolean }> | null
  ) => Promise<void>;
  /** Optional: persist updated excludedComps from the Excluded panel. */
  onSaveExcludedComps?: (
    listingId: string,
    excludedComps: ExcludedComp[] | null
  ) => Promise<void>;
  onSaveAlertSettings: (listingId: string, settings: AlertSettings) => Promise<void>;
  onSaveAutoApply: (
    listingId: string,
    patch: Partial<{
      autoApplyEnabled: boolean;
      autoApplyCohostInviteOpened: boolean;
      autoApplyWindowEndDays: number;
      autoApplyScope: "actionable" | "all_sellable";
      autoApplyMinPriceFloor: number | null;
      autoApplyMinNoticeDays: number;
      autoApplyMaxIncreasePct: number | null;
      autoApplyMaxDecreasePct: number | null;
      autoApplySkipUnavailable: boolean;
    }>
  ) => Promise<void>;
  onTriggerCohostVerification: (listingId: string) => Promise<void>;
}

const PROPERTY_TYPE_SHORT: Record<string, string> = {
  entire_home: "Entire home",
  private_room: "Private room",
  shared_room: "Shared room",
  hotel_room: "Hotel room",
};

function normalizeAirbnbUrl(url: string): string {
  const match = url.match(/airbnb\.com\/rooms\/(\d+)/);
  return match ? `https://www.airbnb.com/rooms/${match[1]}` : url;
}

function cleanTitle(raw: string): string {
  return raw
    .replace(/^Airbnb\s+/i, "")
    .replace(/^Listing\s+#(\d+)$/i, "#$1")
    .trim();
}


export function ListingCard({
  listing,
  isActive,
  onSelect,
  onDelete,
  onViewHistory,
  onRename,
  onSavePreferredComps,
  onSaveAlertSettings,
  onSaveAutoApply,
  onSaveExcludedComps,
  onTriggerCohostVerification,
}: Props) {
  const [editOpen, setEditOpen] = useState(false);
  const [draftName, setDraftName] = useState("");
  // Per-row benchmark draft.  `enabled` defaults to true; `name` is the
  // og:title fetched on URL blur (or read from saved state on sync).
  // `draftId` is a stable per-row key — async title fetches resolve by
  // draftId, not by array index, so reorder/remove during the await won't
  // write the title into the wrong row.
  const [benchmarkDrafts, setBenchmarkDrafts] = useState<
    Array<{
      draftId: string;
      listingUrl: string;
      note: string;
      enabled: boolean;
      name: string;
    }>
  >([]);
  const [expandedBenchmarkIdx, setExpandedBenchmarkIdx] = useState<number | null>(null);
  // Stable draftId of the row currently fetching its og:title — drives a
  // tiny spinner in the row header.  Null when no fetch is in flight.
  const [fetchingDraftId, setFetchingDraftId] = useState<string | null>(null);
  // Index of the row whose ••• overflow menu is open.  Single open menu.
  const [openMenuIdx, setOpenMenuIdx] = useState<number | null>(null);
  // Counter ref for minting draftIds.  Restarts on remount; that's fine
  // because draftIds are local-only (never sent to server).
  const draftIdCounterRef = useRef(0);
  const mintDraftId = () => {
    draftIdCounterRef.current += 1;
    return `draft-${draftIdCounterRef.current}`;
  };
  // Excluded comps accordion open/closed state.
  const [excludedOpen, setExcludedOpen] = useState(false);
  // Draft excluded comps mirroring the staged-save model.  Persisted via
  // `handleSaveAll` together with benchmarks.
  const [excludedDrafts, setExcludedDrafts] = useState<ExcludedComp[]>([]);

  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Alert settings draft state for the edit panel — committed via unified Save button.
  const [draftListingUrl, setDraftListingUrl] = useState(() => normalizeAirbnbUrl(listing.input_attributes.listingUrl ?? ""));
  const [draftMinNights, setDraftMinNights] = useState(listing.minimum_booking_nights ?? 1);
  const [draftAlertsEnabled, setDraftAlertsEnabled] = useState(listing.pricing_alerts_enabled ?? false);

  // Card-level alert toggle state (direct enable/disable, no edit panel needed).
  const [alertsTogglingDirect, setAlertsTogglingDirect] = useState(false);
  const [alertModalOpen, setAlertModalOpen] = useState(false);

  const inputRef = useRef<HTMLInputElement | null>(null);

  const displayTitle = listing.name?.trim() || listing.input_address || "Listing";
  const latest = listing.latestReport;
  const { activeJob } = listing;
  const attrs = listing.input_attributes;

  const freshness = computeFreshness(
    resolveMarketCapturedAt(latest, listing.latestLinkedAt)
  );

  const statusColor =
    activeJob?.status === "running" || activeJob?.status === "queued"
      ? "bg-amber-400 animate-pulse"
      : activeJob?.status === "error"
      ? "bg-rose-400"
      : freshness.dotClass;

  const typeLabel = attrs.propertyType
    ? (PROPERTY_TYPE_SHORT[attrs.propertyType] ?? attrs.propertyType)
    : null;

  const factsLine = [
    typeLabel,
    attrs.bedrooms != null ? `${attrs.bedrooms}bd` : null,
    attrs.bathrooms != null ? `${attrs.bathrooms}ba` : null,
    attrs.maxGuests ? `${attrs.maxGuests} guests` : null,
  ]
    .filter(Boolean)
    .join(" · ");


  // A listing is eligible for direct alert toggle if it already has a valid Airbnb room URL.
  const isEligible = !!(attrs.listingUrl?.includes("airbnb.com/rooms/"));

  const alertsEnabled = listing.pricing_alerts_enabled ?? false;

  // ── Alert body note ───────────────────────────────────────────────
  // Only shown in the card body when an alert was actually sent today.
  const alertBodyNote: string | null = (() => {
    if (!alertsEnabled) return null;
    const sentAt = listing.last_alert_sent_at;
    if (!sentAt) return null;
    const sentDate = sentAt.slice(0, 10);
    const todayDate = new Date().toISOString().slice(0, 10);
    if (sentDate !== todayDate) return null;
    const dir = listing.last_alert_direction;
    return dir === "PRICED_HIGH"
      ? "Alert sent — priced above market"
      : dir === "PRICED_LOW"
      ? "Alert sent — priced below market"
      : "Alert sent today";
  })();

  // ── Alert row description ─────────────────────────────────────────
  // Compact status shown in the card-level pricing alerts row.
  const alertRowStatus: string = (() => {
    if (alertsEnabled) {
      const status = listing.last_live_price_status;
      if (status === "unavailable_or_booked") return "Unavailable last check";
      return "On";
    }
    if (!isEligible) return "Needs setup";
    return "Off";
  })();

  // Focus the rename input when the edit panel opens.
  useEffect(() => {
    if (!editOpen) return;
    const t = setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 0);
    return () => clearTimeout(t);
  }, [editOpen]);

  // Auto-clear save feedback after 2.5 s.
  useEffect(() => {
    if (!saveMessage) return;
    const t = setTimeout(() => setSaveMessage(null), 2500);
    return () => clearTimeout(t);
  }, [saveMessage]);

  // Sync alert draft states when listing prop changes.
  useEffect(() => {
    setDraftListingUrl(listing.input_attributes.listingUrl ?? "");
  }, [listing.input_attributes.listingUrl]);

  useEffect(() => {
    setDraftMinNights(listing.minimum_booking_nights ?? 1);
  }, [listing.minimum_booking_nights]);

  useEffect(() => {
    setDraftAlertsEnabled(listing.pricing_alerts_enabled ?? false);
  }, [listing.pricing_alerts_enabled]);

  // Sync benchmark drafts.  Keep disabled rows so the toggle UI surfaces
  // them; only filter blanks.  Carry `enabled` and `name` through so the
  // edit panel reflects the saved state truthfully.
  useEffect(() => {
    const next = (listing.input_attributes.preferredComps ?? [])
      .filter((c) => c.listingUrl)
      .map((c) => ({
        draftId: mintDraftId(),
        listingUrl: c.listingUrl,
        note: c.note ?? "",
        enabled: c.enabled !== false,
        name: c.name ?? "",
      }));
    setBenchmarkDrafts(
      next.length > 0
        ? next
        : [
            {
              draftId: mintDraftId(),
              listingUrl: "",
              note: "",
              enabled: true,
              name: "",
            },
          ]
    );
    setExpandedBenchmarkIdx(null);
    setOpenMenuIdx(null);
    setFetchingDraftId(null);
    // mintDraftId is stable (just bumps a ref), excluded from deps to avoid
    // re-syncing on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listing.input_attributes.preferredComps]);

  // Sync excluded drafts.
  useEffect(() => {
    const next = listing.input_attributes.excludedComps ?? [];
    setExcludedDrafts(Array.isArray(next) ? next : []);
  }, [listing.input_attributes.excludedComps]);

  // ── Card-level alert toggle ───────────────────────────────────────
  // Turning OFF: always direct.
  // Turning ON: direct if eligible, modal if not.
  async function handleAlertToggle() {
    if (alertsTogglingDirect) return;

    if (alertsEnabled) {
      // Turning OFF — direct, no modal.
      setAlertsTogglingDirect(true);
      try {
        await onSaveAlertSettings(listing.id, { pricingAlertsEnabled: false });
      } finally {
        setAlertsTogglingDirect(false);
      }
    } else if (isEligible) {
      // Turning ON — eligible, enable directly.
      setAlertsTogglingDirect(true);
      try {
        await onSaveAlertSettings(listing.id, { pricingAlertsEnabled: true });
      } catch {
        // Server rejected despite looking eligible — fall back to modal
        // so the user can inspect / correct their settings.
        setAlertModalOpen(true);
      } finally {
        setAlertsTogglingDirect(false);
      }
    } else {
      // Turning ON — missing setup, open the modal.
      setAlertModalOpen(true);
    }
  }

  // Called by the modal on successful save.
  async function handleModalSave(settings: {
    listingUrl: string;
    minimumBookingNights: number;
    pricingAlertsEnabled: true;
  }) {
    await onSaveAlertSettings(listing.id, settings);
    // onClose is called by the modal after this resolves without throwing.
  }

  // ── Unified edit-panel save ───────────────────────────────────────
  async function handleSaveAll() {
    if (isSaving) return;
    setIsSaving(true);
    setSaveMessage(null);
    // Dismiss any outstanding Remove undo toasts so a click after Save
    // can't resurrect a deleted row into local state.  The toast onClick
    // also re-checks pendingRemoveToastIdsRef and bails if not present —
    // belt-and-suspenders against the toast already being mid-animation.
    for (const tid of pendingRemoveToastIdsRef.current) {
      dismissToast(tid);
    }
    pendingRemoveToastIdsRef.current.clear();
    try {
      // 1. Rename if changed.
      const nextName = draftName.trim();
      if (nextName && nextName !== displayTitle) {
        await onRename(listing.id, nextName);
      }
      // 2. Benchmarks.  Preserve `enabled: false` rows so the user can pause
      // a benchmark without losing its note.  Filter only invalid blanks.
      const valid = benchmarkDrafts
        .map((item) => ({
          listingUrl: item.listingUrl.trim(),
          note: item.note.trim(),
          name: item.name.trim(),
          enabled: item.enabled,
        }))
        .filter((item) => item.listingUrl.includes("airbnb.com/rooms/"));
      await onSavePreferredComps(
        listing.id,
        valid.length > 0
          ? valid.map((item) => ({
              listingUrl: item.listingUrl,
              name: item.name || undefined,
              note: item.note || undefined,
              enabled: item.enabled,
            }))
          : null
      );
      // 2b. Excluded comps (only if a callback is wired).
      if (
        onSaveExcludedComps &&
        // Compare against current saved state to avoid no-op PATCHes.
        JSON.stringify(excludedDrafts) !==
          JSON.stringify(listing.input_attributes.excludedComps ?? [])
      ) {
        await onSaveExcludedComps(
          listing.id,
          excludedDrafts.length > 0 ? excludedDrafts : null
        );
      }
      // 3. Alert settings — only send fields that changed.
      const alertPayload: AlertSettings = {};
      const savedUrl = listing.input_attributes.listingUrl ?? "";
      const trimmedDraftUrl = draftListingUrl.trim();
      if (trimmedDraftUrl !== savedUrl) {
        alertPayload.listingUrl = trimmedDraftUrl || null;
      }
      if (draftMinNights !== (listing.minimum_booking_nights ?? 1)) {
        alertPayload.minimumBookingNights = draftMinNights;
      }
      if (draftAlertsEnabled !== (listing.pricing_alerts_enabled ?? false)) {
        alertPayload.pricingAlertsEnabled = draftAlertsEnabled;
      }
      if (Object.keys(alertPayload).length > 0) {
        await onSaveAlertSettings(listing.id, alertPayload);
      }
      setSaveMessage({ type: "success", text: "Saved" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Could not save changes.";
      setSaveMessage({ type: "error", text: msg });
    } finally {
      setIsSaving(false);
    }
  }

  const hasBenchmarkValidationError = benchmarkDrafts.some(
    (comp) =>
      comp.listingUrl.trim().length > 0 &&
      !comp.listingUrl.includes("airbnb.com/rooms/")
  );

  // ── Benchmark row helpers ─────────────────────────────────────────
  // All operations stage to local draft only — outer Save persists.

  // Async: normalize URL on blur, fetch og:title, fill name, auto-collapse.
  // Resolves by *draftId* — if the user reorders or removes the row during
  // the fetch, the title won't be written into the wrong row (or any row
  // if the source row was removed).
  async function commitBenchmarkUrlOnBlur(draftId: string) {
    const row = benchmarkDrafts.find((c) => c.draftId === draftId);
    if (!row) return;
    const raw = row.listingUrl.trim();
    if (!raw) return;
    const normalized = normalizeAirbnbUrl(raw);
    const isValid = normalized.includes("airbnb.com/rooms/");
    if (!isValid) return;
    // Persist normalized URL into the draft (functional setState reads
    // freshest prev — handles concurrent edits in the same row).
    setBenchmarkDrafts((prev) =>
      prev.map((c) => (c.draftId === draftId ? { ...c, listingUrl: normalized } : c))
    );
    // Capture the row's current array index for auto-collapse target.  If
    // the row moves during the await, we'll detect that below.
    const startingIdx = benchmarkDrafts.findIndex((c) => c.draftId === draftId);
    setFetchingDraftId(draftId);
    try {
      const res = await fetch(
        `/api/benchmark-title?url=${encodeURIComponent(normalized)}`,
        { signal: AbortSignal.timeout(5000) }
      );
      if (res.ok) {
        const body = (await res.json().catch(() => null)) as { title?: string } | null;
        const title = body?.title?.trim();
        if (title) {
          setBenchmarkDrafts((prev) =>
            prev.map((c) => (c.draftId === draftId ? { ...c, name: title } : c))
          );
        }
      }
    } catch {
      // Silent — title is decorative.
    } finally {
      setFetchingDraftId((cur) => (cur === draftId ? null : cur));
    }
    // Auto-collapse only if the user is still focused on this same row.
    // If they moved/removed it mid-fetch, leave their current expanded
    // state alone.
    setExpandedBenchmarkIdx((cur) => (cur === startingIdx ? null : cur));
  }

  function moveBenchmark(idx: number, dir: "up" | "down") {
    setBenchmarkDrafts((prev) => {
      const next = [...prev];
      const target = dir === "up" ? idx - 1 : idx + 1;
      if (target < 0 || target >= next.length) return prev;
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });
    setOpenMenuIdx(null);
  }

  function toggleBenchmarkEnabled(idx: number) {
    setBenchmarkDrafts((prev) =>
      prev.map((c, i) => (i === idx ? { ...c, enabled: !c.enabled } : c))
    );
  }

  // Track outstanding remove-undo toast IDs.  Cleared (and dismissed) on
  // Save so an Undo click after Save can't resurrect already-committed
  // deletes into the local draft, which would put UI and server out of sync.
  const pendingRemoveToastIdsRef = useRef<Set<number>>(new Set());

  // Remove with 6 s undo toast.  Pure local-draft op; no API call.  If the
  // user hits Save before pressing Undo, `handleSaveAll` dismisses every
  // outstanding remove toast — Undo becomes a no-op.
  function removeBenchmarkWithUndo(idx: number) {
    const removed = benchmarkDrafts[idx];
    if (!removed) return;
    const removedDraftId = removed.draftId;
    // Optimistic remove.
    setBenchmarkDrafts((prev) =>
      prev.length > 1
        ? prev.filter((c) => c.draftId !== removedDraftId)
        : [
            {
              draftId: mintDraftId(),
              listingUrl: "",
              note: "",
              enabled: true,
              name: "",
            },
          ]
    );
    setOpenMenuIdx(null);
    const label =
      removed.name?.trim() ||
      (removed.listingUrl.match(/\/rooms\/(\d+)/)?.[1]
        ? `Room ${removed.listingUrl.match(/\/rooms\/(\d+)/)![1]}`
        : "benchmark");
    let tid = 0;
    tid = toast({
      title: `Removed ${label} · Undo`,
      durationMs: 6000,
      action: {
        label: "Undo",
        onClick: () => {
          // No-op if Save already cleared this toast — pendingRemoveToastIds
          // would have been emptied and tid removed.  We re-check to make
          // sure we don't resurrect post-Save state.
          if (!pendingRemoveToastIdsRef.current.has(tid)) {
            return;
          }
          pendingRemoveToastIdsRef.current.delete(tid);
          setBenchmarkDrafts((prev) => {
            // Re-insert at the original index, dropping the placeholder if any.
            const cleaned = prev.filter(
              (c) =>
                !(c.listingUrl === "" && c.note === "" && c.name === "")
            );
            const next = [...cleaned];
            next.splice(idx, 0, removed);
            return next;
          });
          dismissToast(tid);
        },
        testId: "toast-undo",
      },
    });
    pendingRemoveToastIdsRef.current.add(tid);
    // Auto-clear from the tracking set when the toast naturally expires —
    // dismissToast on a stale id is a no-op so this is purely cleanup.
    setTimeout(() => {
      pendingRemoveToastIdsRef.current.delete(tid);
    }, 6500);
  }

  function restoreExcluded(roomId: string) {
    setExcludedDrafts((prev) => prev.filter((ec) => ec.roomId !== roomId));
  }

  return (
    <>
      {/* ── Setup modal (renders into document.body via portal) ── */}
      {alertModalOpen && (
        <AlertSetupModal
          listingName={cleanTitle(displayTitle)}
          initialUrl={attrs.listingUrl ?? ""}
          initialMinNights={listing.minimum_booking_nights ?? 1}
          onClose={() => setAlertModalOpen(false)}
          onSave={handleModalSave}
        />
      )}

      <div
        className={`relative rounded-2xl transition-all duration-150 ${
          editOpen
            ? "bg-white shadow-[0_6px_24px_rgba(0,0,0,0.11),0_0_0_1px_rgba(0,0,0,0.09)]"
            : isActive
            ? "bg-white shadow-[0_2px_8px_rgba(0,0,0,0.07),0_0_0_1px_rgba(0,0,0,0.06)]"
            : "hover:bg-white/80 hover:shadow-[0_1px_4px_rgba(0,0,0,0.05),0_0_0_1px_rgba(0,0,0,0.04)]"
        }`}
      >
        {/* Active left accent bar */}
        {isActive && (
          <div className="absolute inset-y-0 left-0 w-0.75 rounded-l-2xl rounded-r-sm bg-blue-500/75" />
        )}

        {/* ── Selectable body ── */}
        <div
          data-testid={`listing-nav-${listing.id}`}
          className={`cursor-pointer px-4 transition-all ${
            isActive || editOpen ? "pb-2.5 pt-4" : "py-3"
          }`}
          onClick={onSelect}
        >
          <div className="flex min-w-0 items-center gap-2">
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusColor}`}
              title={activeJob ? activeJob.status : latest ? "ready" : "no report"}
            />
            <p className={`truncate font-semibold tracking-tight ${isActive ? "text-base text-foreground" : "text-sm text-foreground/80"}`}>
              {cleanTitle(displayTitle)}
            </p>
          </div>
          {factsLine && (
            <p className="mt-1 truncate pl-3.5 text-xs text-foreground/35">
              {factsLine}
            </p>
          )}
          {alertBodyNote && isActive && (
            <p className="mt-0.5 truncate pl-3.5 text-[11px] text-foreground/35">
              {alertBodyNote}
            </p>
          )}
        </div>

        {/* Controls — only rendered for the active listing (or when edit panel is open) */}
        {(isActive || editOpen) && (
          <>
            {/* ── Auto-Apply ── */}
            <AutoApplyFeature
              listing={listing}
              calendar={listing.latestReport?.result_calendar ?? []}
              onSaveAutoApply={onSaveAutoApply}
              onTriggerCohostVerification={onTriggerCohostVerification}
            />

            {/* ── Pricing alerts row ── */}
            {!editOpen && (
              <div
                className={`flex items-center justify-between px-4 py-2 transition-colors ${
                  alertsEnabled
                    ? "border-t border-gray-100/80"
                    : "border-t border-accent/10 bg-accent/5"
                }`}
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span className={`text-sm font-medium ${alertsEnabled ? "text-foreground/50" : "text-accent"}`}>
                    {alertsEnabled ? "Pricing alerts" : "Enable alerts"}
                  </span>
                  <span
                    className={`text-[11px] ${
                      alertsEnabled
                        ? alertRowStatus === "Unavailable last check"
                          ? "text-amber-500/70"
                          : "text-emerald-600/60"
                        : "text-accent/50"
                    }`}
                  >
                    {alertsEnabled ? alertRowStatus : !isEligible ? "Needs setup" : "Off"}
                  </span>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={alertsEnabled}
                  aria-label={
                    alertsEnabled
                      ? "Disable pricing alerts"
                      : isEligible
                      ? "Enable pricing alerts"
                      : "Set up pricing alerts"
                  }
                  disabled={alertsTogglingDirect}
                  onClick={() => void handleAlertToggle()}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
                    alertsEnabled ? "bg-emerald-500" : "bg-accent"
                  }`}
                >
                  <span
                    className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
                      alertsEnabled ? "translate-x-4" : "translate-x-1"
                    }`}
                  />
                </button>
              </div>
            )}

            {/* ── Footer: Edit/Close ── */}
            <div className="flex items-center justify-between border-t border-gray-100/80 px-4 py-2">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onViewHistory();
                }}
                className="text-xs font-medium text-foreground/40 transition-colors hover:text-foreground/70"
              >
                Details
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditOpen((v) => {
                    const next = !v;
                    if (next) {
                      setDraftName(displayTitle);
                      setExpandedBenchmarkIdx(null);
                      setSaveMessage(null);
                    }
                    return next;
                  });
                }}
                className={`text-xs font-medium transition-colors ${
                  editOpen
                    ? "text-foreground/65"
                    : "text-foreground/40 hover:text-foreground/70"
                }`}
              >
                {editOpen ? "Close" : "Edit"}
              </button>
            </div>
          </>
        )}

        {/* ── Edit panel ───────────────────────────────────────────────
            Two sections: Property (read-only) + Name + Benchmarks.
            One Save button commits everything together.
            All clicks stop propagation so nothing leaks to onSelect.
        ─────────────────────────────────────────────────────────────── */}
        {editOpen && (
          <div
            className="border-t border-gray-200/70 bg-gray-50/60 px-5 pb-6 pt-4 rounded-b-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Mode header */}
            <div className="mb-4 flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase tracking-widest text-foreground/30">
                Editing
              </span>
              <button
                type="button"
                onClick={() => setEditOpen(false)}
                aria-label="Close editor"
                className="flex h-5 w-5 items-center justify-center rounded-full text-foreground/25 transition-colors hover:bg-gray-200/70 hover:text-foreground/55"
              >
                <svg width="8" height="8" viewBox="0 0 8 8" fill="none" aria-hidden="true">
                  <path d="M1 1l6 6M7 1l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>

            <div className="space-y-5">

              {/* ── § 0 Airbnb URL ── */}
              <div className="space-y-1.5">
                <label className="block text-sm font-semibold text-foreground/55">
                  Airbnb listing URL
                </label>
                <div className="flex items-center gap-1.5">
                  <input
                    type="url"
                    placeholder="https://www.airbnb.com/rooms/…"
                    value={draftListingUrl}
                    onChange={(e) => {
                      const normalized = normalizeAirbnbUrl(e.target.value);
                      setDraftListingUrl(normalized);
                      if (!normalized.includes("airbnb.com/rooms/")) {
                        setDraftAlertsEnabled(false);
                      }
                    }}
                    className="w-full rounded-lg border border-gray-200 bg-gray-50/60 px-2.5 py-2 font-mono text-xs outline-none focus:border-gray-300 focus:bg-white"
                  />
                  {draftListingUrl.includes("airbnb.com/rooms/") && (
                    <a
                      href={draftListingUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="shrink-0 rounded-lg border border-gray-200/60 bg-white/80 px-2 py-2 text-xs text-blue-400 transition-colors hover:bg-blue-50/60"
                      title="Open listing"
                    >
                      ↗
                    </a>
                  )}
                </div>
                {draftListingUrl.trim() && !draftListingUrl.includes("airbnb.com/rooms/") && (
                  <p className="text-xs text-rose-500">Must be an airbnb.com/rooms/… URL.</p>
                )}
                {!draftListingUrl.trim() && !listing.input_attributes.listingUrl && (
                  <p className="text-xs text-foreground/35">Paste your Airbnb listing link here to enable pricing alerts.</p>
                )}
              </div>

              <div className="h-px bg-gray-200/60" />

              {/* ── § 1 Name ── */}
              <div className="space-y-2">
                <label className="block text-sm font-semibold text-foreground/55">
                  Name
                </label>
                <input
                  ref={inputRef}
                  type="text"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); void handleSaveAll(); }
                  }}
                  aria-label="Listing name"
                  placeholder="Listing name"
                  className="w-full rounded-lg border border-gray-200/70 bg-white/90 px-3 py-2.5 text-sm font-semibold outline-none transition-colors placeholder:font-normal placeholder:text-foreground/25 focus:border-gray-300 focus:bg-white"
                />
              </div>

              <div className="h-px bg-gray-200/60" />

              {/* ── § 2 Benchmarks ── */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-foreground/55">
                    Benchmarks
                  </p>
                  {benchmarkDrafts.length < 10 && (
                    <button
                      type="button"
                      onClick={() => {
                        const newIdx = benchmarkDrafts.length;
                        setBenchmarkDrafts((prev) => [
                          ...prev,
                          {
                            draftId: mintDraftId(),
                            listingUrl: "",
                            note: "",
                            enabled: true,
                            name: "",
                          },
                        ]);
                        setExpandedBenchmarkIdx(newIdx);
                      }}
                      className="text-xs font-medium text-foreground/40 transition-colors hover:text-foreground/70"
                    >
                      + Add
                    </button>
                  )}
                </div>

                <div className="space-y-2">
                  {benchmarkDrafts.map((comp, idx) => {
                    const isExpanded = expandedBenchmarkIdx === idx;
                    const isMenuOpen = openMenuIdx === idx;
                    const hasUrl = comp.listingUrl.trim().length > 0;
                    const isValid = comp.listingUrl.includes("airbnb.com/rooms/");
                    const roomMatch = comp.listingUrl.match(/\/rooms\/(\d+)/);
                    const roomLabel = comp.name?.trim()
                      ? comp.name
                      : roomMatch
                      ? `Room ${roomMatch[1]}`
                      : hasUrl
                      ? "Airbnb listing"
                      : "New benchmark";
                    const isFetchingTitle = fetchingDraftId === comp.draftId;

                    return (
                      <div
                        key={comp.draftId}
                        data-testid="benchmark-row"
                        data-row-idx={idx}
                        className={`rounded-xl border transition-colors ${
                          isExpanded
                            ? "border-gray-200 bg-white"
                            : "border-gray-200/60 bg-white/70"
                        } ${comp.enabled ? "" : "opacity-60"}`}
                      >
                        {/* Collapsed header: toggle · primary dot · label · primary pill · ••• */}
                        <div
                          className="flex items-center gap-2.5 px-3 py-2.5"
                          onClick={(e) => {
                            // Click in the empty area expands the row, but
                            // not when the user clicked any control.
                            if ((e.target as HTMLElement).closest("button, input, [role='menu'], label")) return;
                            setExpandedBenchmarkIdx(isExpanded ? null : idx);
                          }}
                        >
                          {/* enable/disable toggle (always visible) */}
                          <button
                            type="button"
                            role="switch"
                            aria-checked={comp.enabled}
                            aria-label={comp.enabled ? "Disable benchmark" : "Enable benchmark"}
                            data-testid="benchmark-enabled-toggle"
                            data-row-idx={idx}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleBenchmarkEnabled(idx);
                            }}
                            className={`relative h-4 w-7 shrink-0 rounded-full transition-colors ${
                              comp.enabled ? "bg-blue-400" : "bg-gray-200"
                            }`}
                          >
                            <span
                              className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow-sm transition-transform ${
                                comp.enabled ? "translate-x-3.5" : "translate-x-0.5"
                              }`}
                            />
                          </button>

                          {/* Primary dot — amber when index 0 + enabled, otherwise gray */}
                          <span
                            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                              idx === 0 && comp.enabled ? "bg-amber-400" : "bg-gray-300"
                            }`}
                            aria-hidden="true"
                          />

                          {/* Label + optional Primary pill + spinner */}
                          <span
                            className={`flex-1 truncate text-sm font-medium ${
                              hasUrl ? "text-foreground/70" : "text-foreground/30"
                            } ${comp.enabled ? "" : "line-through"}`}
                          >
                            {roomLabel}
                          </span>
                          {idx === 0 && hasUrl && (
                            <span className="shrink-0 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-amber-200">
                              Primary
                            </span>
                          )}
                          {isFetchingTitle && (
                            <svg
                              className="h-3 w-3 shrink-0 animate-spin text-foreground/40"
                              viewBox="0 0 24 24"
                              fill="none"
                              aria-label="Fetching title"
                            >
                              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
                              <path d="M21 12a9 9 0 0 1-9 9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                            </svg>
                          )}

                          {/* ••• overflow menu */}
                          <div className="relative shrink-0">
                            <button
                              type="button"
                              data-testid="benchmark-row-menu"
                              data-row-idx={idx}
                              aria-label="More actions"
                              aria-haspopup="menu"
                              aria-expanded={isMenuOpen}
                              onClick={(e) => {
                                e.stopPropagation();
                                setOpenMenuIdx(isMenuOpen ? null : idx);
                              }}
                              className="rounded p-1 text-foreground/40 transition-colors hover:bg-gray-100 hover:text-foreground/70"
                            >
                              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                                <circle cx="3" cy="8" r="1.5" />
                                <circle cx="8" cy="8" r="1.5" />
                                <circle cx="13" cy="8" r="1.5" />
                              </svg>
                            </button>
                            {isMenuOpen && (
                              <div
                                role="menu"
                                className="absolute right-0 top-7 z-10 w-36 overflow-hidden rounded-md bg-white text-xs shadow-lg ring-1 ring-gray-200"
                              >
                                <button
                                  type="button"
                                  role="menuitem"
                                  data-testid={`benchmark-move-up-${idx}`}
                                  disabled={idx === 0}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    moveBenchmark(idx, "up");
                                  }}
                                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-foreground/70 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-foreground/25"
                                >
                                  <span aria-hidden="true">↑</span> Move up
                                </button>
                                <button
                                  type="button"
                                  role="menuitem"
                                  data-testid={`benchmark-move-down-${idx}`}
                                  disabled={idx === benchmarkDrafts.length - 1}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    moveBenchmark(idx, "down");
                                  }}
                                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-foreground/70 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-foreground/25"
                                >
                                  <span aria-hidden="true">↓</span> Move down
                                </button>
                                <button
                                  type="button"
                                  role="menuitem"
                                  data-testid={`benchmark-remove-${idx}`}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    removeBenchmarkWithUndo(idx);
                                  }}
                                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-rose-600 hover:bg-rose-50"
                                >
                                  <span aria-hidden="true">✕</span> Remove
                                </button>
                              </div>
                            )}
                          </div>
                        </div>

                        {/* Expanded edit form: URL + note. Blur auto-commits to draft. */}
                        {isExpanded && (
                          <div className="space-y-3 border-t border-gray-100 px-3 pb-3.5 pt-3">
                            <div className="space-y-1.5">
                              <label className="block text-xs font-medium text-foreground/50">
                                Airbnb URL
                              </label>
                              <input
                                type="url"
                                placeholder="https://airbnb.com/rooms/..."
                                value={comp.listingUrl}
                                data-testid="benchmark-url-input"
                                data-row-idx={idx}
                                onChange={(e) => {
                                  const next = [...benchmarkDrafts];
                                  next[idx] = { ...next[idx], listingUrl: e.target.value };
                                  setBenchmarkDrafts(next);
                                }}
                                onBlur={() => {
                                  void commitBenchmarkUrlOnBlur(comp.draftId);
                                }}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") {
                                    e.preventDefault();
                                    (e.target as HTMLInputElement).blur();
                                  }
                                }}
                                className="w-full rounded-lg border border-gray-200 bg-gray-50/60 px-2.5 py-2 font-mono text-xs outline-none focus:border-gray-300 focus:bg-white"
                              />
                              {hasUrl && !isValid && (
                                <p className="text-xs text-rose-500">
                                  Must be a valid Airbnb room URL.
                                </p>
                              )}
                            </div>
                            <div className="space-y-1.5">
                              <label className="block text-xs font-medium text-foreground/50">
                                Note{" "}
                                <span className="font-normal text-foreground/30">(optional)</span>
                              </label>
                              <input
                                type="text"
                                placeholder="e.g. closest competitor"
                                value={comp.note}
                                onChange={(e) => {
                                  const next = [...benchmarkDrafts];
                                  next[idx] = { ...next[idx], note: e.target.value };
                                  setBenchmarkDrafts(next);
                                }}
                                className="w-full rounded-lg border border-gray-200 bg-gray-50/60 px-2.5 py-2 text-xs outline-none placeholder:text-foreground/25 focus:border-gray-300 focus:bg-white"
                              />
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Excluded comps accordion (collapsed by default; only count visible) */}
                {excludedDrafts.length > 0 && (
                  <details
                    data-testid="excluded-comps-panel"
                    className="group rounded-xl border border-gray-200/60 bg-white/70"
                    open={excludedOpen}
                    onToggle={(e) => setExcludedOpen((e.target as HTMLDetailsElement).open)}
                  >
                    <summary
                      data-testid="excluded-comps-summary"
                      className="flex cursor-pointer items-center justify-between px-3 py-2 text-sm font-medium text-foreground/55"
                    >
                      <span>Excluded comps ({excludedDrafts.length})</span>
                      <span aria-hidden="true" className="text-xs text-foreground/30 transition-transform group-open:rotate-180">
                        ▾
                      </span>
                    </summary>
                    <div className="space-y-1.5 border-t border-gray-100 px-3 py-2">
                      {excludedDrafts.map((ec) => (
                        <div
                          key={ec.roomId}
                          data-testid="excluded-row"
                          data-room-id={ec.roomId}
                          className="flex items-center gap-2 text-xs"
                        >
                          <span className="flex-1 truncate text-foreground/65">
                            {ec.title?.trim() || `Room ${ec.roomId}`}
                          </span>
                          <button
                            type="button"
                            data-testid="excluded-restore-button"
                            data-room-id={ec.roomId}
                            onClick={() => restoreExcluded(ec.roomId)}
                            className="shrink-0 rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200 transition hover:bg-emerald-100"
                          >
                            Restore
                          </button>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>

              <div className="h-px bg-gray-200/60" />

              {/* ── § 3 Alert settings ── */}
              <div className="space-y-3">
                <p className="text-sm font-semibold text-foreground/55">Alert settings</p>

                {/* Min nights */}
                <div className="space-y-1.5">
                  <label className="block text-xs font-medium text-foreground/50">
                    Minimum booking nights
                    <span className="ml-1 font-normal text-foreground/30">(1–30)</span>
                  </label>
                  <select
                    value={draftMinNights}
                    onChange={(e) => setDraftMinNights(Number(e.target.value))}
                    className="w-full rounded-lg border border-gray-200 bg-gray-50/60 px-2.5 py-2 text-sm outline-none focus:border-gray-300 focus:bg-white"
                  >
                    {Array.from({ length: 30 }, (_, i) => i + 1).map((n) => (
                      <option key={n} value={n}>{n} {n === 1 ? "night" : "nights"}</option>
                    ))}
                  </select>
                </div>

                {/* Toggle */}
                {(() => {
                  const hasValidUrl = draftListingUrl.trim().includes("airbnb.com/rooms/");
                  return (
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-foreground/65">Nightly price alerts</p>
                        {!hasValidUrl && (
                          <p className="mt-0.5 text-xs text-foreground/40">
                            Add a valid Airbnb URL above to enable alerts
                          </p>
                        )}
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={draftAlertsEnabled}
                        onClick={() => {
                          if (!hasValidUrl) {
                            setAlertModalOpen(true);
                          } else {
                            setDraftAlertsEnabled((v) => !v);
                          }
                        }}
                        className={`relative mt-0.5 inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors focus:outline-none ${
                          draftAlertsEnabled && hasValidUrl ? "bg-emerald-500" : "bg-accent"
                        }`}
                      >
                        <span
                          className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
                            draftAlertsEnabled && hasValidUrl ? "translate-x-4" : "translate-x-1"
                          }`}
                        />
                      </button>
                    </div>
                  );
                })()}
              </div>

              <div className="h-px bg-gray-200/60" />

              {/* ── Unified save ── */}
              <div className="space-y-2.5">
                <button
                  type="button"
                  onClick={handleSaveAll}
                  disabled={isSaving || hasBenchmarkValidationError}
                  className="w-full rounded-xl bg-gray-900 py-3 text-sm font-semibold text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
                >
                  {isSaving ? "Saving…" : "Save"}
                </button>
                {saveMessage && (
                  <p
                    className={`text-center text-xs font-medium ${
                      saveMessage.type === "success"
                        ? "text-emerald-600"
                        : "text-rose-500"
                    }`}
                    role="status"
                    aria-live="polite"
                  >
                    {saveMessage.text}
                  </p>
                )}
              </div>

              {/* ── Delete ── */}
              <button
                type="button"
                onClick={onDelete}
                className="w-full rounded-xl border border-rose-200 py-3 text-sm font-semibold text-rose-500 transition-colors hover:bg-rose-50 hover:border-rose-300"
              >
                Delete listing
              </button>

            </div>
          </div>
        )}
      </div>
    </>
  );
}
