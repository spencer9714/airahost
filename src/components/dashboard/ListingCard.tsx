import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { RecommendedPrice, CalendarDay } from "@/lib/schemas";
import { computeFreshness, resolveMarketCapturedAt } from "@/lib/freshness";
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
    preferredComps?: Array<{ listingUrl: string; note?: string; enabled?: boolean }> | null;
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
    preferredComps: Array<{ listingUrl: string; note?: string; enabled?: boolean }> | null
  ) => Promise<void>;
  onSaveAlertSettings: (listingId: string, settings: AlertSettings) => Promise<void>;
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

function shortenUrl(raw: string): string {
  try {
    const u = new URL(raw);
    const path = u.pathname.replace(/\/$/, "");
    return u.hostname + (path.length > 30 ? path.slice(0, 30) + "…" : path);
  } catch {
    return raw;
  }
}

function looksLikeUrl(str: string): boolean {
  return /^https?:\/\//i.test(str.trim());
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
}: Props) {
  const [editOpen, setEditOpen] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [benchmarkDrafts, setBenchmarkDrafts] = useState<
    Array<{ listingUrl: string; note: string }>
  >([]);
  const [expandedBenchmarkIdx, setExpandedBenchmarkIdx] = useState<number | null>(null);

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

  // Sync benchmark drafts.
  useEffect(() => {
    const next = (listing.input_attributes.preferredComps ?? [])
      .filter((c) => c.enabled !== false && c.listingUrl)
      .map((c) => ({ listingUrl: c.listingUrl, note: c.note ?? "" }));
    setBenchmarkDrafts(next.length > 0 ? next : [{ listingUrl: "", note: "" }]);
    setExpandedBenchmarkIdx(null);
  }, [listing.input_attributes.preferredComps]);

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
    try {
      // 1. Rename if changed.
      const nextName = draftName.trim();
      if (nextName && nextName !== displayTitle) {
        await onRename(listing.id, nextName);
      }
      // 2. Benchmarks.
      const valid = benchmarkDrafts
        .map((item) => ({ listingUrl: item.listingUrl.trim(), note: item.note.trim() }))
        .filter((item) => item.listingUrl.includes("airbnb.com/rooms/"));
      await onSavePreferredComps(
        listing.id,
        valid.length > 0
          ? valid.map((item) => ({
              listingUrl: item.listingUrl,
              note: item.note || undefined,
              enabled: true,
            }))
          : null
      );
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
        className={`relative overflow-hidden rounded-2xl transition-all duration-150 ${
          editOpen
            ? "bg-white shadow-[0_6px_24px_rgba(0,0,0,0.11),0_0_0_1px_rgba(0,0,0,0.09)]"
            : isActive
            ? "bg-white shadow-[0_2px_8px_rgba(0,0,0,0.07),0_0_0_1px_rgba(0,0,0,0.06)]"
            : "hover:bg-white/80 hover:shadow-[0_1px_4px_rgba(0,0,0,0.05),0_0_0_1px_rgba(0,0,0,0.04)]"
        }`}
      >
        {/* Active left accent bar */}
        {isActive && (
          <div className="absolute inset-y-0 left-0 w-0.75 rounded-r-sm bg-blue-500/75" />
        )}

        {/* ── Selectable body ── */}
        <div className="cursor-pointer px-5 pb-3 pt-5" onClick={onSelect}>
          <div className="flex min-w-0 items-center gap-2">
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusColor}`}
              title={activeJob ? activeJob.status : latest ? "ready" : "no report"}
            />
            <p className="truncate text-base font-semibold tracking-tight text-foreground">
              {cleanTitle(displayTitle)}
            </p>
          </div>
          {factsLine && (
            <p className="mt-1.5 truncate pl-3.5 text-sm font-medium text-foreground/35">
              {factsLine}
            </p>
          )}
          {/* Alert-sent note — only shown when an alert fired today */}
          {alertBodyNote && (
            <p className="mt-1 truncate pl-3.5 text-[11px] text-foreground/35">
              {alertBodyNote}
            </p>
          )}
        </div>

        {/* ── Pricing alerts row ───────────────────────────────────────────
            Visible in normal card view (hidden when edit panel is open,
            since the panel already has alert settings).
            Clicking the toggle enables/disables directly when eligible,
            or opens the setup modal when configuration is missing.
        ─────────────────────────────────────────────────────────────────── */}
        {!editOpen && (
          <div
            className={`flex items-center justify-between px-5 py-3 transition-colors ${
              alertsEnabled
                ? "border-t border-gray-100/80"
                : "border-t border-accent/10 bg-accent/5"
            }`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex min-w-0 items-center gap-2">
              {!alertsEnabled && (
                <span className="text-sm">🔔</span>
              )}
              <span className={`text-xs font-semibold ${alertsEnabled ? "text-foreground/50" : "text-accent"}`}>
                {alertsEnabled ? "Pricing alerts" : "Enable pricing alerts"}
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

        {/* ── Footer: Edit ── */}
        <div className="flex items-center border-t border-gray-100/80 px-5 py-3">
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
            className={`text-sm font-medium transition-colors ${
              editOpen
                ? "text-foreground/65"
                : "text-foreground/45 hover:text-foreground/70"
            }`}
          >
            {editOpen ? "Close" : "Edit"}
          </button>
        </div>

        {/* ── Edit panel ────────────────────────────────────────────────── */}
        {editOpen && (
          <div
            className="border-t border-gray-200/70 bg-gray-50/60 px-5 pb-6 pt-4"
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
                        setBenchmarkDrafts((prev) => [...prev, { listingUrl: "", note: "" }]);
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
                    const hasUrl = comp.listingUrl.trim().length > 0;
                    const isValid = comp.listingUrl.includes("airbnb.com/rooms/");
                    const roomMatch = comp.listingUrl.match(/\/rooms\/(\d+)/);
                    const roomLabel = roomMatch
                      ? `Room ${roomMatch[1]}`
                      : hasUrl
                      ? "Airbnb listing"
                      : "New benchmark";

                    return (
                      <div
                        key={idx}
                        className={`rounded-xl border transition-colors ${
                          isExpanded
                            ? "border-gray-200 bg-white"
                            : "border-gray-200/60 bg-white/70"
                        }`}
                      >
                        <div className="flex items-center gap-2.5 px-3 py-2.5">
                          <span
                            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                              idx === 0 ? "bg-blue-400" : "bg-gray-300"
                            }`}
                            title={idx === 0 ? "Primary" : "Secondary"}
                          />
                          <span
                            className={`flex-1 truncate text-sm font-medium ${
                              hasUrl ? "text-foreground/70" : "text-foreground/30"
                            }`}
                          >
                            {roomLabel}
                          </span>
                          {!isExpanded && comp.note && (
                            <span className="max-w-16 shrink-0 truncate text-xs italic text-foreground/30">
                              {comp.note}
                            </span>
                          )}
                          <div className="flex shrink-0 items-center gap-3">
                            {idx > 0 && !isExpanded && (
                              <button
                                type="button"
                                onClick={() => {
                                  const next = [...benchmarkDrafts];
                                  const [picked] = next.splice(idx, 1);
                                  next.unshift(picked);
                                  setBenchmarkDrafts(next);
                                }}
                                className="text-xs text-foreground/30 transition-colors hover:text-foreground/60"
                              >
                                Set primary
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() =>
                                setExpandedBenchmarkIdx(isExpanded ? null : idx)
                              }
                              className="text-xs font-medium text-foreground/40 transition-colors hover:text-foreground/70"
                            >
                              {isExpanded ? "Done" : "Edit"}
                            </button>
                            <button
                              type="button"
                              aria-label="Remove benchmark"
                              onClick={() =>
                                setBenchmarkDrafts((prev) =>
                                  prev.length > 1
                                    ? prev.filter((_, i) => i !== idx)
                                    : [{ listingUrl: "", note: "" }]
                                )
                              }
                              className="text-sm leading-none text-foreground/20 transition-colors hover:text-rose-400"
                            >
                              ×
                            </button>
                          </div>
                        </div>

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
                                onChange={(e) => {
                                  const next = [...benchmarkDrafts];
                                  next[idx] = { ...next[idx], listingUrl: e.target.value };
                                  setBenchmarkDrafts(next);
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
