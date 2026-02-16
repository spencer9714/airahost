import Link from "next/link";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import type { RecommendedPrice, CalendarDay } from "@/lib/schemas";

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
  latestReport: LatestReport;
  latestLinkedAt: string | null;
};

interface Props {
  listing: ListingData;
  isActive: boolean;
  onSelect: () => void;
  onRerun: () => void;
  onDelete: () => void;
  onViewDetails: () => void;
  isRerunning: boolean;
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
}

const PROPERTY_TYPE_SHORT: Record<string, string> = {
  entire_home: "Entire home",
  private_room: "Private room",
  shared_room: "Shared room",
  hotel_room: "Hotel room",
};

function positionBadge(listing: ListingData) {
  const latest = listing.latestReport;
  if (!latest || latest.status !== "ready" || !latest.result_summary) return null;

  const median = latest.result_summary.nightlyMedian;
  const recommended = latest.result_summary.recommendedPrice?.nightly;
  if (!median || !recommended) return null;

  const ratio = recommended / median;
  if (ratio < 0.95) {
    return { label: "Under market", color: "bg-emerald-50 text-emerald-700 border-emerald-200" };
  }
  if (ratio > 1.05) {
    return { label: "Above market", color: "bg-amber-50 text-amber-700 border-amber-200" };
  }
  return { label: "At market", color: "bg-gray-50 text-gray-600 border-gray-200" };
}

export function ListingCard({
  listing,
  isActive,
  onSelect,
  onRerun,
  onDelete,
  onViewDetails,
  isRerunning,
  isExpanded,
  historyLoading,
  historyRows,
}: Props) {
  const latest = listing.latestReport;
  const range =
    latest?.result_summary?.nightlyMin !== undefined &&
    latest?.result_summary?.nightlyMax !== undefined
      ? `$${latest.result_summary.nightlyMin} - $${latest.result_summary.nightlyMax}`
      : "No completed report yet";

  const badge = positionBadge(listing);
  const attrs = listing.input_attributes;

  return (
    <Card
      className={`cursor-pointer transition-all ${
        isActive ? "border-accent/40 ring-1 ring-accent/20" : ""
      }`}
    >
      <div onClick={onSelect}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">{listing.name}</h3>
              {badge && (
                <span
                  className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${badge.color}`}
                >
                  {badge.label}
                </span>
              )}
            </div>
            <p className="text-sm text-muted">{listing.input_address}</p>
            <p className="text-xs text-muted">
              {attrs.propertyType
                ? PROPERTY_TYPE_SHORT[attrs.propertyType] ?? attrs.propertyType
                : ""}
              {attrs.propertyType ? " · " : ""}
              {attrs.maxGuests ?? "?"} guests · {attrs.bedrooms ?? "?"} bed
              {(attrs.bedrooms ?? 0) !== 1 ? "s" : ""} ·{" "}
              {attrs.bathrooms ?? "?"} bath
              {(attrs.bathrooms ?? 0) !== 1 ? "s" : ""}
            </p>
            <p className="text-sm">
              Latest range:{" "}
              <span className="font-semibold">{range}</span>
              {latest?.result_summary?.nightlyMedian && (
                <span className="ml-2 text-xs text-muted">
                  (median: ${latest.result_summary.nightlyMedian})
                </span>
              )}
            </p>
            <p className="text-xs text-muted">
              Last analyzed:{" "}
              {listing.latestLinkedAt
                ? new Date(listing.latestLinkedAt).toLocaleDateString()
                : "Never"}
            </p>
          </div>

          <div
            className="flex flex-wrap items-center gap-2"
            onClick={(e) => e.stopPropagation()}
          >
            {latest?.share_id && (
              <Link href={`/r/${latest.share_id}`}>
                <Button size="sm" variant="ghost">
                  View report
                </Button>
              </Link>
            )}
            <Button size="sm" variant="ghost" onClick={onViewDetails}>
              {isExpanded ? "Hide history" : "History"}
            </Button>
            <Button size="sm" onClick={onRerun} disabled={isRerunning}>
              {isRerunning ? "Re-analyzing..." : "Re-run"}
            </Button>
            <Button size="sm" variant="secondary" onClick={onDelete}>
              Delete
            </Button>
          </div>
        </div>
      </div>

      {isExpanded && (
        <div className="mt-4 border-t border-border pt-4">
          <p className="mb-3 text-sm font-medium">Report history</p>
          {historyLoading ? (
            <p className="text-sm text-muted">Loading...</p>
          ) : historyRows.length === 0 ? (
            <p className="text-sm text-muted">No reports yet.</p>
          ) : (
            <div className="space-y-2">
              {historyRows.map((row) => {
                const report = row.pricing_reports;
                if (!report) return null;
                return (
                  <Link
                    key={row.id}
                    href={`/r/${report.share_id}`}
                    className="flex items-center justify-between rounded-xl border border-border px-3 py-2 text-sm hover:bg-gray-50"
                  >
                    <span>
                      {new Date(row.created_at).toLocaleDateString()} (
                      {row.trigger})
                    </span>
                    <span className="font-medium">
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
    </Card>
  );
}
