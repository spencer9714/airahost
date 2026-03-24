import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/Button";
import type { RecommendedPrice, CalendarDay, DateMode } from "@/lib/schemas";

type LatestReport = {
  id: string;
  share_id: string;
  status: "queued" | "running" | "ready" | "error";
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

  const range =
    latest?.result_summary?.nightlyMin !== undefined &&
    latest?.result_summary?.nightlyMax !== undefined
      ? `$${latest.result_summary.nightlyMin}–$${latest.result_summary.nightlyMax}`
      : null;

  const attrs = listing.input_attributes;
  const activeBenchmarks = (attrs.preferredComps ?? []).filter(
    (c) => c.enabled !== false && c.listingUrl
  );
  const hasBenchmark = activeBenchmarks.length > 0;

  const statusDot =
    latest?.status === "ready"
      ? "bg-emerald-500"
      : latest?.status === "running" || latest?.status === "queued"
      ? "bg-amber-400"
      : "bg-gray-300";

  const typeLabel = attrs.propertyType
    ? (PROPERTY_TYPE_SHORT[attrs.propertyType] ?? attrs.propertyType)
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
      className={`transition-colors ${
        isActive
          ? "border-l-[3px] border-l-accent bg-blue-50/20"
          : "border-l-[3px] border-l-transparent hover:bg-gray-50/60"
      }`}
    >
      {/* ── Main row ── */}
      <div
        className="flex items-center gap-3 px-5 py-4 cursor-pointer"
        onClick={onSelect}
      >
        {/* Status indicator */}
        <span
          className={`mt-0.5 h-2 w-2 shrink-0 rounded-full ${statusDot}`}
          title={latest?.status ?? "No report"}
        />

        {/* Listing info */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <h3 className="truncate text-sm font-semibold text-foreground">
              {displayTitle}
            </h3>
            {hasBenchmark && (
              <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                Benchmark
              </span>
            )}
          </div>
          <p className="text-xs text-foreground/50">
            {[
              typeLabel,
              attrs.maxGuests ? `${attrs.maxGuests} guests` : null,
              attrs.bedrooms != null
                ? `${attrs.bedrooms} bed${attrs.bedrooms !== 1 ? "s" : ""}`
                : null,
              attrs.bathrooms != null
                ? `${attrs.bathrooms} bath${attrs.bathrooms !== 1 ? "s" : ""}`
                : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <div className="mt-1 flex items-center gap-2">
            {range ? (
              <span className="text-sm font-semibold text-foreground">
                {range}
                <span className="ml-1 text-xs font-normal text-foreground/40">
                  /night
                </span>
              </span>
            ) : (
              <span className="text-xs text-foreground/40">No report yet</span>
            )}
            {listing.latestLinkedAt && (
              <span className="text-xs text-foreground/35">
                · {new Date(listing.latestLinkedAt).toLocaleDateString()}
              </span>
            )}
          </div>
          {!hasBenchmark && (
            <Link
              href={`/dashboard/listings/${listing.id}`}
              className="mt-1 inline-block text-xs text-amber-700 hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              + Add benchmark — improves accuracy
            </Link>
          )}
        </div>

        {/* Actions */}
        <div
          className="flex shrink-0 items-center gap-2"
          onClick={(e) => e.stopPropagation()}
        >
          {latest?.share_id && latest.status === "ready" && (
            <Link
              href={`/r/${latest.share_id}`}
              className="hidden text-xs font-medium text-foreground/50 hover:text-foreground transition-colors sm:inline"
            >
              View report
            </Link>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setEditOpen((v) => {
                const next = !v;
                if (next) {
                  setRenameError("");
                  setDraftName(displayTitle);
                }
                return next;
              });
            }}
          >
            {editOpen ? "Close" : "Edit"}
          </Button>
          <Button size="sm" onClick={handleRunClick} disabled={isRunning}>
            {isRunning ? "Running…" : "Analyze"}
          </Button>
        </div>
      </div>

      {/* ── Edit panel ── */}
      {editOpen && (
        <div
          className="border-t border-border px-5 pb-5 pt-4"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="space-y-5 rounded-xl border border-border bg-gray-50/70 p-4">
            {/* Rename */}
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-foreground/40">
                Rename
              </p>
              <div className="flex flex-wrap items-center gap-2">
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
                  aria-label="Rename listing title"
                  className="w-full max-w-sm rounded-lg border border-border bg-white px-3 py-2 text-sm font-semibold outline-none focus:border-accent"
                />
                {showRenameSuccess && (
                  <span className="text-xs font-medium text-emerald-600" role="status" aria-live="polite">
                    Saved
                  </span>
                )}
              </div>
              {renameError && (
                <p className="text-xs text-rose-600" role="status" aria-live="polite">
                  {renameError}
                </p>
              )}
            </div>

            {/* Analysis window */}
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-foreground/40">
                Analysis window
              </p>
              <div className="inline-flex gap-1 rounded-lg border border-border bg-gray-100/80 p-1">
                <button
                  type="button"
                  onClick={() => handleDateModeChange("next_30")}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${
                    dateMode === "next_30"
                      ? "bg-white text-foreground shadow-sm"
                      : "text-foreground/50 hover:text-foreground"
                  }`}
                >
                  Next 30 days
                </button>
                <button
                  type="button"
                  onClick={() => handleDateModeChange("custom")}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${
                    dateMode === "custom"
                      ? "bg-white text-foreground shadow-sm"
                      : "text-foreground/50 hover:text-foreground"
                  }`}
                >
                  Custom range
                </button>
              </div>

              {dateMode === "custom" && (
                <div className="flex flex-wrap items-center gap-4">
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-foreground/50">Start</span>
                    <input
                      type="date"
                      value={customStart}
                      onChange={(e) => handleCustomStartChange(e.target.value)}
                      className="block rounded-lg border border-border bg-white px-3 py-1.5 text-sm outline-none focus:border-accent"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-foreground/50">End</span>
                    <input
                      type="date"
                      value={customEnd}
                      onChange={(e) => handleCustomEndChange(e.target.value)}
                      min={customStart}
                      className="block rounded-lg border border-border bg-white px-3 py-1.5 text-sm outline-none focus:border-accent"
                    />
                  </label>
                </div>
              )}
            </div>

            {/* Benchmark listings */}
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-foreground/40">
                  Benchmark listings
                </p>
                {hasBenchmark && (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                    {activeBenchmarks.length} active
                  </span>
                )}
              </div>
              <div className="space-y-2">
                {benchmarkDrafts.map((comp, idx) => (
                  <div key={idx} className={`rounded-lg border p-3 ${idx === 0 ? "border-amber-300 bg-amber-50/60" : "border-border bg-white"}`}>
                    {/* Row header */}
                    <div className="mb-2 flex items-center justify-between">
                      {idx === 0 ? (
                        <span className="rounded-full bg-amber-200 px-2 py-0.5 text-[10px] font-semibold text-amber-900">
                          ★ Primary benchmark
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
                          className="rounded-full border border-gray-300 px-2 py-0.5 text-[10px] font-medium text-gray-500 hover:border-amber-400 hover:text-amber-700 transition-colors"
                        >
                          Set as primary
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() =>
                          setBenchmarkDrafts((prev) =>
                            prev.length > 1 ? prev.filter((_, i) => i !== idx) : [{ listingUrl: "", note: "" }]
                          )
                        }
                        className="ml-auto text-xs text-foreground/45 hover:text-rose-600"
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
                        className="w-full rounded-lg border border-border bg-white px-3 py-2 text-sm outline-none focus:border-accent"
                      />
                      {comp.listingUrl && !comp.listingUrl.includes("airbnb.com/rooms/") && (
                        <p className="text-xs text-rose-600">Must be a valid Airbnb room URL.</p>
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
                        className="w-full rounded-lg border border-border bg-white px-3 py-2 text-sm outline-none focus:border-accent"
                      />
                    </div>
                  </div>
                ))}
              </div>
              {benchmarkDrafts.length < 10 && (
                <button
                  type="button"
                  onClick={() =>
                    setBenchmarkDrafts((prev) => [...prev, { listingUrl: "", note: "" }])
                  }
                  className="text-xs font-medium text-amber-700 hover:underline"
                >
                  + Add benchmark listing
                </button>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
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
                  {benchmarkSaving ? "Saving..." : "Save benchmarks"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setBenchmarkDrafts([{ listingUrl: "", note: "" }])}
                  disabled={benchmarkSaving}
                >
                  Clear draft
                </Button>
                {benchmarkMessage && (
                  <span className="text-xs text-foreground/55" role="status" aria-live="polite">
                    {benchmarkMessage}
                  </span>
                )}
              </div>
            </div>

            {/* Run */}
            <Button size="sm" onClick={handleRunClick} disabled={isRunning}>
              {isRunning ? "Queued…" : "Run analysis"}
            </Button>

            {/* Footer: secondary actions */}
            <div className="flex items-center justify-between border-t border-border pt-3">
              <button
                type="button"
                onClick={onViewHistory}
                className="text-xs font-medium text-foreground/50 hover:text-foreground transition-colors"
              >
                All reports →
              </button>
              <button
                type="button"
                onClick={onDelete}
                className="text-xs font-medium text-rose-500 hover:text-rose-700 transition-colors"
              >
                Delete listing
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
