import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { RecommendedPrice, CalendarDay } from "@/lib/schemas";
import { computeFreshness, resolveMarketCapturedAt } from "@/lib/freshness";

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
};

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
}

const PROPERTY_TYPE_SHORT: Record<string, string> = {
  entire_home: "Entire home",
  private_room: "Private room",
  shared_room: "Shared room",
  hotel_room: "Hotel room",
};

// Strip noisy prefixes that don't add meaning.
// "Airbnb Listing #12345" → "#12345"
// "Airbnb My Place" → "My Place"
function cleanTitle(raw: string): string {
  return raw
    .replace(/^Airbnb\s+/i, "")
    .replace(/^Listing\s+#(\d+)$/i, "#$1")
    .trim();
}

// Returns a shortened display string for a URL (hostname + truncated path).
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
}: Props) {
  const [editOpen, setEditOpen] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [benchmarkDrafts, setBenchmarkDrafts] = useState<
    Array<{ listingUrl: string; note: string }>
  >([]);
  const [expandedBenchmarkIdx, setExpandedBenchmarkIdx] = useState<number | null>(null);

  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

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

  const freshnessLabel = latest !== null ? freshness.label : null;

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

  // Sync benchmark drafts when external data changes (e.g. after a save round-trip).
  useEffect(() => {
    const next = (listing.input_attributes.preferredComps ?? [])
      .filter((c) => c.enabled !== false && c.listingUrl)
      .map((c) => ({ listingUrl: c.listingUrl, note: c.note ?? "" }));
    setBenchmarkDrafts(next.length > 0 ? next : [{ listingUrl: "", note: "" }]);
    setExpandedBenchmarkIdx(null);
  }, [listing.input_attributes.preferredComps]);

  // ── Unified save ─────────────────────────────────────────────────
  // One button commits name + benchmarks together.
  async function handleSaveAll() {
    if (isSaving) return;
    setIsSaving(true);
    setSaveMessage(null);
    try {
      // 1. Rename if the name actually changed.
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
      setSaveMessage({ type: "success", text: "Saved" });
    } catch {
      setSaveMessage({ type: "error", text: "Could not save changes." });
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
        {/* Price summary line */}
        {latest?.result_summary && (() => {
          const s = latest.result_summary;
          const suggested = s.recommendedPrice?.nightly ?? s.nightlyMedian ?? null;
          const median = s.nightlyMedian ?? null;
          const observed = (s as { observedListingPrice?: number | null }).observedListingPrice ?? null;
          if (!suggested && !observed) return null;
          return (
            <div className="mt-2 pl-3.5">
              {observed != null ? (
                <p className="text-base font-bold text-foreground">
                  ${observed}
                  <span className="ml-1 text-xs font-normal text-foreground/35">your price</span>
                  {median != null && median > 0 && (
                    <span className="ml-2 text-sm font-normal text-foreground/40">
                      mkt ${median}
                    </span>
                  )}
                </p>
              ) : suggested != null ? (
                <p className="text-base font-bold text-foreground">
                  ${suggested}
                  <span className="ml-1 text-xs font-normal text-foreground/35">suggested</span>
                  {median != null && median > 0 && suggested !== median && (
                    <span className="ml-2 text-sm font-normal text-foreground/40">
                      mkt ${median}
                    </span>
                  )}
                </p>
              ) : null}
            </div>
          );
        })()}
        {freshnessLabel && (
          <div className="mt-0.5 flex items-center gap-1.5 pl-3.5">
            <p className="text-xs text-foreground/30">{freshnessLabel}</p>
            {listing.latestTrigger === "scheduled" ? (
              <span className="rounded-full bg-teal-100 px-1.5 py-0.5 text-[11px] font-semibold text-teal-700">
                Nightly
              </span>
            ) : latest?.report_type === "forecast_snapshot" ? (
              <span className="rounded-full bg-violet-100 px-1.5 py-0.5 text-[11px] font-semibold text-violet-600">
                Forecast
              </span>
            ) : null}
          </div>
        )}
      </div>

      {/* ── Footer: Edit + View ── */}
      <div className="flex items-center justify-between border-t border-gray-100/80 px-5 py-3">
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
        {latest?.share_id && (
          <Link
            href={`/r/${latest.share_id}`}
            className="text-sm font-medium text-foreground/35 transition-colors hover:text-foreground/60"
          >
            View →
          </Link>
        )}
      </div>

      {/* ── Edit panel ───────────────────────────────────────────────
          Two sections: Property (read-only) + Name + Benchmarks.
          One Save button commits everything together.
          All clicks stop propagation so nothing leaks to onSelect.
      ─────────────────────────────────────────────────────────────── */}
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

            {/* ── § 0 Property address / URL — read-only ── */}
            <div className="space-y-2">
              <p className="text-sm font-semibold text-foreground/55">Property</p>
              {attrs.listingUrl ? (
                <div className="space-y-1.5">
                  <a
                    href={attrs.listingUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 rounded-lg border border-gray-200/60 bg-white/80 px-3 py-2.5 transition-colors hover:bg-blue-50/60"
                  >
                    <span className="flex-1 truncate font-mono text-xs text-blue-600">
                      {shortenUrl(attrs.listingUrl)}
                    </span>
                    <span className="shrink-0 text-xs text-blue-400">↗</span>
                  </a>
                  {listing.input_address && (
                    <p className="truncate px-1 text-xs text-foreground/40">
                      {listing.input_address}
                    </p>
                  )}
                </div>
              ) : looksLikeUrl(listing.input_address) ? (
                <a
                  href={listing.input_address}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 rounded-lg border border-gray-200/60 bg-white/80 px-3 py-2.5 transition-colors hover:bg-blue-50/60"
                >
                  <span className="flex-1 truncate font-mono text-xs text-blue-600">
                    {shortenUrl(listing.input_address)}
                  </span>
                  <span className="shrink-0 text-xs text-blue-400">↗</span>
                </a>
              ) : (
                <p className="rounded-lg border border-gray-200/60 bg-white/80 px-3 py-2.5 text-sm leading-snug text-foreground/65">
                  {listing.input_address}
                </p>
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
                      {/* Header row */}
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

                      {/* Expanded fields */}
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

            <div className="h-px bg-gray-200/60" />

            {/* ── Footer links ── */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={onViewHistory}
                  className="text-xs font-medium text-foreground/40 transition-colors hover:text-foreground/70"
                >
                  All reports →
                </button>
                {latest?.share_id && (
                  <Link
                    href={`/r/${latest.share_id}`}
                    className="text-xs font-medium text-foreground/40 transition-colors hover:text-foreground/70"
                    onClick={(e) => e.stopPropagation()}
                  >
                    Latest →
                  </Link>
                )}
              </div>
              <button
                type="button"
                onClick={onDelete}
                className="text-xs font-medium text-foreground/25 transition-colors hover:text-rose-500"
              >
                Delete
              </button>
            </div>

          </div>
        </div>
      )}
    </div>
  );
}
