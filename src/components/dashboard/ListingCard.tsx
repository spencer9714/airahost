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
  onViewDetails: () => void;
  onViewHistory: () => void;
  isRunning: boolean;
  isExpanded: boolean;
  historyLoading: boolean;
  historyRows: Array<{
    id: string;
    trigger: string;
    created_at: string;
    pricing_reports: {
      share_id: string;
      status: string;
      result_summary: { nightlyMedian?: number } | null;
    } | null;
  }>;
  onRename: (listingId: string, nextName: string) => Promise<void>;
  onSaveDateDefaults: (
    listingId: string,
    mode: DateMode,
    startDate: string | null,
    endDate: string | null
  ) => void;
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
  onViewDetails,
  onViewHistory,
  isRunning,
  isExpanded,
  historyLoading,
  historyRows,
  onRename,
  onSaveDateDefaults,
}: Props) {
  const [editOpen, setEditOpen] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);
  const [renameError, setRenameError] = useState("");
  const [showRenameSuccess, setShowRenameSuccess] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Date settings
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
      ? `$${latest.result_summary.nightlyMin} – $${latest.result_summary.nightlyMax}`
      : "No report yet";

  const attrs = listing.input_attributes;

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

  // Debounced save of date defaults
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

  return (
    <div
      className={`px-5 py-5 cursor-pointer transition-colors hover:bg-gray-50/50 ${
        isActive ? "border-l-3 border-l-accent pl-4" : ""
      }`}
    >
      <div onClick={onSelect}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          {/* Left: info */}
          <div className="min-w-0 flex-1 space-y-1">
            <h3 className="truncate text-base font-semibold">{displayTitle}</h3>
            <p className="text-sm text-foreground/60">
              {attrs.propertyType
                ? (PROPERTY_TYPE_SHORT[attrs.propertyType] ?? attrs.propertyType)
                : ""}
              {attrs.propertyType ? " · " : ""}
              {attrs.maxGuests ?? "?"} guests · {attrs.bedrooms ?? "?"} bed
              {(attrs.bedrooms ?? 0) !== 1 ? "s" : ""} ·{" "}
              {attrs.bathrooms ?? "?"} bath
              {(attrs.bathrooms ?? 0) !== 1 ? "s" : ""}
            </p>
            <p className="text-sm">
              <span className="font-semibold text-foreground">{range}</span>
              {listing.latestLinkedAt && (
                <span className="ml-3 text-foreground/50">
                  Analyzed{" "}
                  {new Date(listing.latestLinkedAt).toLocaleDateString()}
                </span>
              )}
            </p>
          </div>

          {/* Right: actions */}
          <div
            className="flex flex-wrap items-center gap-1.5"
            onClick={(e) => e.stopPropagation()}
          >
            {latest?.share_id && (
              <Link href={`/r/${latest.share_id}`}>
                <Button size="sm" variant="ghost">
                  Report
                </Button>
              </Link>
            )}
            <Button size="sm" variant="ghost" onClick={onViewHistory}>
              All reports
            </Button>
            <Button size="sm" variant="ghost" onClick={onViewDetails}>
              {isExpanded ? "Hide" : "History"}
            </Button>
            <Button size="sm" variant="ghost" onClick={onDelete}>
              Delete
            </Button>
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
              {editOpen ? "Close edit" : "Edit"}
            </Button>
          </div>
        </div>
      </div>

      {/* ── Inline date settings ────────────────────────────── */}
      {editOpen && (
        <div className="mt-3" onClick={(e) => e.stopPropagation()}>
          <div className="space-y-4 rounded-xl border border-border bg-white p-4">
            <div className="space-y-2">
              <p className="text-sm font-semibold text-foreground">Rename</p>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  ref={inputRef}
                  type="text"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  onBlur={() => {
                    void commitRename();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void commitRename();
                    }
                  }}
                  aria-label="Rename listing title"
                  className="w-full max-w-sm rounded-lg border border-border bg-white px-3 py-2 text-base font-semibold outline-none focus:border-accent"
                />
                {showRenameSuccess && (
                  <span
                    className="text-sm font-medium text-emerald-700"
                    role="status"
                    aria-live="polite"
                  >
                    Updated
                  </span>
                )}
              </div>
              {renameError && (
                <p
                  className="text-sm text-rose-600"
                  role="status"
                  aria-live="polite"
                >
                  {renameError}
                </p>
              )}
            </div>

            <div className="space-y-3">
              <p className="text-sm font-semibold text-foreground">
                Date settings
              </p>
              <div className="inline-flex gap-1 rounded-xl border border-border bg-gray-100/80 p-1">
                <button
                  type="button"
                  onClick={() => handleDateModeChange("next_30")}
                  className={`rounded-lg px-4 py-2 text-sm font-semibold transition-all ${
                    dateMode === "next_30"
                      ? "bg-white text-foreground shadow-sm"
                      : "text-foreground/60 hover:text-foreground"
                  }`}
                >
                  Next 30 days
                </button>
                <button
                  type="button"
                  onClick={() => handleDateModeChange("custom")}
                  className={`rounded-lg px-4 py-2 text-sm font-semibold transition-all ${
                    dateMode === "custom"
                      ? "bg-white text-foreground shadow-sm"
                      : "text-foreground/60 hover:text-foreground"
                  }`}
                >
                  Custom range
                </button>
              </div>

              {dateMode === "custom" && (
                <div className="flex flex-wrap items-center gap-4">
                  <label className="space-y-1">
                    <span className="text-sm font-medium text-foreground/60">
                      Start
                    </span>
                    <input
                      type="date"
                      value={customStart}
                      onChange={(e) => handleCustomStartChange(e.target.value)}
                      className="block rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-accent"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-sm font-medium text-foreground/60">
                      End
                    </span>
                    <input
                      type="date"
                      value={customEnd}
                      onChange={(e) => handleCustomEndChange(e.target.value)}
                      min={customStart}
                      className="block rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-accent"
                    />
                  </label>
                </div>
              )}
            </div>

            <Button size="md" onClick={handleRunClick} disabled={isRunning}>
              {isRunning ? "Queued..." : "Run analysis"}
            </Button>
          </div>
        </div>
      )}

      {/* ── Expanded report history ─────────────────────────── */}
      {isExpanded && (
        <div className="mt-4 border-t border-border pt-4">
          <p className="mb-3 text-sm font-semibold">Report history</p>
          {historyLoading ? (
            <p className="text-sm text-foreground/60">Loading...</p>
          ) : historyRows.length === 0 ? (
            <p className="text-sm text-foreground/60">No reports yet.</p>
          ) : (
            <div className="space-y-1">
              {historyRows.map((row) => {
                const report = row.pricing_reports;
                if (!report) return null;
                return (
                  <Link
                    key={row.id}
                    href={`/r/${report.share_id}`}
                    className="flex items-center justify-between rounded-xl px-3 py-2.5 text-sm hover:bg-gray-100"
                  >
                    <span className="text-foreground/60">
                      {new Date(row.created_at).toLocaleDateString()} (
                      {row.trigger})
                    </span>
                    <span className="font-semibold">
                      {report.result_summary?.nightlyMedian
                        ? `$${report.result_summary.nightlyMedian}/night`
                        : report.status}
                    </span>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
