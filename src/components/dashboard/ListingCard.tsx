import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/Button";
import type { RecommendedPrice, CalendarDay, DateMode } from "@/lib/schemas";

type LatestReport = {
  id: string;
  share_id: string;
  status: "ready";
  created_at: string;
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
    preferredComps?: Array<{ listingUrl: string; note?: string; enabled?: boolean }> | null;
  };
  default_date_mode?: DateMode;
  default_start_date?: string | null;
  default_end_date?: string | null;
  latestReport: LatestReport;
  latestLinkedAt: string | null;
  activeJob: ActiveJob;
};

interface Props {
  listing: ListingData;
  isActive: boolean;
  onSelect: () => void;
  onRunAnalysis: (
    listingId: string,
    dates: { startDate: string; endDate: string }
  ) => Promise<void>;
  onDelete: () => void;
  onViewHistory: () => void;
  isRunning: boolean;
  onRename: (listingId: string, nextName: string) => Promise<void>;
  onSaveDateDefaults: (
    listingId: string,
    mode: DateMode,
    startDate: string | null,
    endDate: string | null
  ) => void;
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

// Strip common prefixes that add visual noise without adding meaning.
// "Airbnb Listing #12345" → "Listing #12345"
function cleanTitle(raw: string): string {
  return raw.replace(/^Airbnb\s+/i, "").trim();
}

function todayStr() {
  return new Date().toISOString().split("T")[0];
}

function plus30Str() {
  const d = new Date();
  d.setDate(d.getDate() + 30);
  return d.toISOString().split("T")[0];
}

export function ListingCard({
  listing,
  isActive,
  onSelect,
  onRunAnalysis,
  onDelete,
  onViewHistory,
  isRunning,
  onRename,
  onSaveDateDefaults,
  onSavePreferredComps,
}: Props) {
  const [editOpen, setEditOpen] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);
  const [renameError, setRenameError] = useState("");
  const [showRenameSuccess, setShowRenameSuccess] = useState(false);
  const [benchmarkDrafts, setBenchmarkDrafts] = useState<
    Array<{ listingUrl: string; note: string }>
  >([]);
  const [benchmarkSaving, setBenchmarkSaving] = useState(false);
  const [benchmarkMessage, setBenchmarkMessage] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [dateMode, setDateMode] = useState<DateMode>(
    listing.default_date_mode ?? "next_30"
  );
  const [customStart, setCustomStart] = useState(
    listing.default_start_date ?? todayStr()
  );
  const [customEnd, setCustomEnd] = useState(
    listing.default_end_date ?? plus30Str()
  );

  const displayTitle =
    listing.name?.trim() || listing.input_address || "Listing";
  const latest = listing.latestReport;
  const { activeJob } = listing;

  // Canonical suggested price — same derivation as banner and Recent Reports.
  const suggestedPrice =
    latest?.result_summary?.recommendedPrice?.nightly ??
    latest?.result_summary?.nightlyMedian ??
    null;

  const attrs = listing.input_attributes;
  const activeBenchmarks = (attrs.preferredComps ?? []).filter(
    (c) => c.enabled !== false && c.listingUrl
  );
  const hasBenchmark = activeBenchmarks.length > 0;

  // Status dot: driven by activeJob first, then latestReport (ready), else idle.
  const statusColor =
    activeJob?.status === "running" || activeJob?.status === "queued"
      ? "bg-amber-400 animate-pulse"
      : activeJob?.status === "error"
      ? "bg-rose-400"
      : latest !== null
      ? "bg-emerald-500"
      : "bg-gray-300";

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

  const analysisDate = listing.latestLinkedAt
    ? new Date(listing.latestLinkedAt).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })
    : null;

  useEffect(() => {
    if (!editOpen) return;
    const t = setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 0);
    return () => clearTimeout(t);
  }, [editOpen]);

  useEffect(() => {
    if (!showRenameSuccess) return;
    const t = setTimeout(() => setShowRenameSuccess(false), 1500);
    return () => clearTimeout(t);
  }, [showRenameSuccess]);

  useEffect(() => {
    const next = (listing.input_attributes.preferredComps ?? [])
      .filter((c) => c.enabled !== false && c.listingUrl)
      .map((c) => ({ listingUrl: c.listingUrl, note: c.note ?? "" }));
    setBenchmarkDrafts(next.length > 0 ? next : [{ listingUrl: "", note: "" }]);
    setBenchmarkMessage("");
  }, [listing.input_attributes.preferredComps]);

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debouncedSaveDates = useCallback(
    (mode: DateMode, start: string | null, end: string | null) => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => {
        onSaveDateDefaults(listing.id, mode, start, end);
      }, 800);
    },
    [listing.id, onSaveDateDefaults]
  );

  function handleDateModeChange(mode: DateMode) {
    setDateMode(mode);
    const start = mode === "next_30" ? null : customStart;
    const end = mode === "next_30" ? null : customEnd;
    debouncedSaveDates(mode, start, end);
  }

  function handleCustomStartChange(val: string) {
    setCustomStart(val);
    debouncedSaveDates("custom", val, customEnd);
  }

  function handleCustomEndChange(val: string) {
    setCustomEnd(val);
    debouncedSaveDates("custom", customStart, val);
  }

  function getActiveDates() {
    if (dateMode === "next_30") {
      return { startDate: todayStr(), endDate: plus30Str() };
    }
    return { startDate: customStart, endDate: customEnd };
  }

  async function commitRename() {
    if (renameSaving) return;
    const next = draftName.trim();
    if (!next || next === displayTitle) {
      setDraftName(displayTitle);
      setRenameError("");
      return;
    }
    try {
      setRenameSaving(true);
      setRenameError("");
      await onRename(listing.id, next);
      setShowRenameSuccess(true);
    } catch {
      setRenameError("Could not update name.");
    } finally {
      setRenameSaving(false);
    }
  }

  async function handleRunClick() {
    const dates = getActiveDates();
    await onRunAnalysis(listing.id, dates);
  }

  async function handleSaveBenchmarks() {
    const valid = benchmarkDrafts
      .map((item) => ({
        listingUrl: item.listingUrl.trim(),
        note: item.note.trim(),
      }))
      .filter((item) => item.listingUrl.includes("airbnb.com/rooms/"));

    try {
      setBenchmarkSaving(true);
      setBenchmarkMessage("");
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
      setBenchmarkMessage(
        valid.length > 0
          ? `Saved ${valid.length} benchmark${valid.length !== 1 ? "s" : ""}.`
          : "Benchmarks cleared."
      );
    } catch {
      setBenchmarkMessage("Could not save benchmark listings.");
    } finally {
      setBenchmarkSaving(false);
    }
  }

  return (
    <div
      className={`group transition-all duration-150 rounded-xl ${
        isActive
          ? "bg-white border border-blue-500/25 shadow-sm"
          : "border border-transparent hover:bg-white/80 hover:border-gray-100/80"
      }`}
    >
      {/* ── Card body (clickable) ── */}
      <div className="cursor-pointer px-4 py-3.5" onClick={onSelect}>

        {/* Band A: Identity */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusColor}`}
              title={activeJob ? activeJob.status : latest ? "ready" : "no report"}
            />
            <p className="truncate text-[13px] font-semibold tracking-tight text-foreground">
              {cleanTitle(displayTitle)}
            </p>
          </div>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setEditOpen((v) => {
                const next = !v;
                if (next) {
                  setRenameError("");
                  setDraftName(displayTitle);
                }
                return next;
              });
            }}
            className={`shrink-0 select-none text-sm leading-none transition-all ${
              editOpen
                ? "text-foreground/45"
                : "text-foreground/20 opacity-0 group-hover:opacity-100"
            }`}
            aria-label="Settings"
          >
            ···
          </button>
        </div>

        {/* Band B: Property metadata */}
        {factsLine && (
          <p className="mt-1.5 truncate text-[10px] font-medium text-foreground/35">
            {factsLine}
          </p>
        )}

        {/* Band C: Pricing signal + action — separated by a faint rule */}
        <div className="mt-3 flex items-end justify-between border-t border-gray-100 pt-3">
          {/* Left: price stack */}
          <div>
            <p className="text-[9px] font-bold uppercase tracking-widest text-foreground/30">
              Suggested
            </p>
            {suggestedPrice != null ? (
              <div className="mt-0.5 flex items-baseline gap-0.5">
                <span className="text-base font-bold tracking-tight text-foreground">
                  ${suggestedPrice}
                </span>
                <span className="text-[10px] text-foreground/30">/nt</span>
              </div>
            ) : activeJob?.status === "running" ||
              activeJob?.status === "queued" ? (
              <p className="mt-0.5 text-xs text-foreground/40">Analyzing…</p>
            ) : (
              <p className="mt-0.5 text-xs text-foreground/30">—</p>
            )}
            {analysisDate && (
              <p className="mt-0.5 text-[9px] text-foreground/25">{analysisDate}</p>
            )}
          </div>

          {/* Right: primary action + quiet link — stop propagation only on buttons */}
          <div
            className="flex flex-col items-end gap-1.5"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={handleRunClick}
              disabled={isRunning}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-[11px] font-bold text-white shadow-sm transition-colors hover:bg-blue-700 disabled:opacity-40"
            >
              {isRunning ? "…" : "Analyze"}
            </button>
            {latest?.share_id && (
              <Link
                href={`/r/${latest.share_id}`}
                className="text-[10px] font-medium text-foreground/30 transition-colors hover:text-foreground/60"
              >
                View →
              </Link>
            )}
          </div>
        </div>
      </div>

      {/* ── Settings / edit panel ── */}
      {editOpen && (
        <div
          className="border-t border-gray-100 px-4 pb-4 pt-3"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="space-y-4 rounded-xl border border-gray-100 bg-white/80 p-3">

            {/* Rename */}
            <div className="space-y-1.5">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-foreground/35">
                Rename
              </p>
              <input
                ref={inputRef}
                type="text"
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onBlur={() => void commitRename()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void commitRename();
                  }
                }}
                aria-label="Rename listing"
                className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold outline-none focus:border-accent"
              />
              <div className="flex items-center gap-2">
                {showRenameSuccess && (
                  <span className="text-xs font-medium text-emerald-600" role="status" aria-live="polite">
                    Saved
                  </span>
                )}
                {renameError && (
                  <p className="text-xs text-rose-600" role="status" aria-live="polite">
                    {renameError}
                  </p>
                )}
              </div>
            </div>

            {/* Analysis window */}
            <div className="space-y-2">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-foreground/35">
                Analysis window
              </p>
              <div className="inline-flex gap-0.5 rounded-lg border border-gray-200 bg-gray-100/60 p-0.5">
                <button
                  type="button"
                  onClick={() => handleDateModeChange("next_30")}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${
                    dateMode === "next_30"
                      ? "bg-white text-foreground shadow-sm"
                      : "text-foreground/45 hover:text-foreground"
                  }`}
                >
                  Next 30d
                </button>
                <button
                  type="button"
                  onClick={() => handleDateModeChange("custom")}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${
                    dateMode === "custom"
                      ? "bg-white text-foreground shadow-sm"
                      : "text-foreground/45 hover:text-foreground"
                  }`}
                >
                  Custom
                </button>
              </div>

              {dateMode === "custom" && (
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-foreground/45">Start</span>
                    <input
                      type="date"
                      value={customStart}
                      onChange={(e) => handleCustomStartChange(e.target.value)}
                      className="w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none focus:border-accent"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-foreground/45">End</span>
                    <input
                      type="date"
                      value={customEnd}
                      onChange={(e) => handleCustomEndChange(e.target.value)}
                      min={customStart}
                      className="w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none focus:border-accent"
                    />
                  </label>
                </div>
              )}
            </div>

            {/* Benchmark listings */}
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-foreground/35">
                  Benchmarks
                </p>
                {hasBenchmark && (
                  <span className="text-[10px] font-medium text-foreground/35">
                    {activeBenchmarks.length} active
                  </span>
                )}
              </div>
              <div className="space-y-2">
                {benchmarkDrafts.map((comp, idx) => (
                  <div
                    key={idx}
                    className="rounded-lg border border-gray-200 bg-white p-2.5"
                  >
                    <div className="mb-1.5 flex items-center justify-between">
                      {idx === 0 ? (
                        <span className="text-[10px] font-semibold text-foreground/45">
                          Primary
                        </span>
                      ) : benchmarkDrafts.length > 1 ? (
                        <button
                          type="button"
                          onClick={() => {
                            const next = [...benchmarkDrafts];
                            const [picked] = next.splice(idx, 1);
                            next.unshift(picked);
                            setBenchmarkDrafts(next);
                          }}
                          className="rounded-full border border-gray-200 px-1.5 py-px text-[10px] font-medium text-foreground/40 hover:border-gray-300 hover:text-foreground/70 transition-colors"
                        >
                          Set primary
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() =>
                          setBenchmarkDrafts((prev) =>
                            prev.length > 1
                              ? prev.filter((_, i) => i !== idx)
                              : [{ listingUrl: "", note: "" }]
                          )
                        }
                        className="ml-auto text-xs text-foreground/30 hover:text-foreground/60 transition-colors"
                      >
                        Remove
                      </button>
                    </div>
                    <div className="space-y-1.5">
                      <input
                        type="url"
                        placeholder="https://airbnb.com/rooms/123..."
                        value={comp.listingUrl}
                        onChange={(e) => {
                          const next = [...benchmarkDrafts];
                          next[idx] = { ...next[idx], listingUrl: e.target.value };
                          setBenchmarkDrafts(next);
                        }}
                        className="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-xs outline-none focus:border-accent"
                      />
                      {comp.listingUrl &&
                        !comp.listingUrl.includes("airbnb.com/rooms/") && (
                          <p className="text-[10px] text-rose-600">
                            Must be a valid Airbnb room URL.
                          </p>
                        )}
                      <input
                        type="text"
                        placeholder="Optional note"
                        value={comp.note}
                        onChange={(e) => {
                          const next = [...benchmarkDrafts];
                          next[idx] = { ...next[idx], note: e.target.value };
                          setBenchmarkDrafts(next);
                        }}
                        className="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-xs outline-none focus:border-accent"
                      />
                    </div>
                  </div>
                ))}
              </div>
              {benchmarkDrafts.length < 10 && (
                <button
                  type="button"
                  onClick={() =>
                    setBenchmarkDrafts((prev) => [
                      ...prev,
                      { listingUrl: "", note: "" },
                    ])
                  }
                  className="text-xs font-medium text-foreground/40 transition-colors hover:text-foreground/70 hover:underline"
                >
                  + Add benchmark
                </button>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  className="px-3 py-1.5 text-xs"
                  onClick={handleSaveBenchmarks}
                  disabled={
                    benchmarkSaving ||
                    benchmarkDrafts.some(
                      (comp) =>
                        comp.listingUrl.trim().length > 0 &&
                        !comp.listingUrl.includes("airbnb.com/rooms/")
                    )
                  }
                >
                  {benchmarkSaving ? "Saving…" : "Save benchmarks"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="px-3 py-1.5 text-xs"
                  onClick={() =>
                    setBenchmarkDrafts([{ listingUrl: "", note: "" }])
                  }
                  disabled={benchmarkSaving}
                >
                  Clear
                </Button>
                {benchmarkMessage && (
                  <span
                    className="text-xs text-foreground/45"
                    role="status"
                    aria-live="polite"
                  >
                    {benchmarkMessage}
                  </span>
                )}
              </div>
            </div>

            {/* Run analysis */}
            <Button
              size="sm"
              className="w-full py-2 text-xs"
              onClick={handleRunClick}
              disabled={isRunning}
            >
              {isRunning ? "Queued…" : "Run analysis"}
            </Button>

            {/* Footer: secondary links */}
            <div className="flex items-center justify-between border-t border-gray-100 pt-3">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={onViewHistory}
                  className="text-xs font-medium text-foreground/35 transition-colors hover:text-foreground"
                >
                  All reports →
                </button>
                {latest?.share_id && (
                  <Link
                    href={`/r/${latest.share_id}`}
                    className="text-xs font-medium text-foreground/35 transition-colors hover:text-foreground"
                    onClick={(e) => e.stopPropagation()}
                  >
                    Latest report →
                  </Link>
                )}
              </div>
              <button
                type="button"
                onClick={onDelete}
                className="text-xs font-medium text-rose-400 transition-colors hover:text-rose-600"
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
