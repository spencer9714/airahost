"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { getSupabaseBrowser } from "@/lib/supabase";
import type { RecommendedPrice } from "@/lib/schemas";

type ReportSnapshot = {
  id: string;
  share_id: string;
  status: "queued" | "running" | "ready" | "error";
  created_at: string;
  input_date_start: string;
  input_date_end: string;
  result_summary: {
    nightlyMedian?: number;
    recommendedPrice?: RecommendedPrice;
  } | null;
  error_message?: string | null;
};

type HistoryRow = {
  id: string;
  trigger: string;
  created_at: string;
  pricing_reports: ReportSnapshot | ReportSnapshot[] | null;
};

type ListingDetail = {
  id: string;
  name: string;
  input_address: string;
};

type StatusFilter = "all" | "ready" | "error";

export default function ListingHistoryPage() {
  const { listingId } = useParams<{ listingId: string }>();
  const router = useRouter();

  const [listing, setListing] = useState<ListingDetail | null>(null);
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [rerunningId, setRerunningId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // Verify auth
      const supabase = getSupabaseBrowser();
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) {
        router.push("/login");
        return;
      }

      // Fetch listing details + reports in parallel
      const [listingRes, reportsRes] = await Promise.all([
        fetch(`/api/listings/${listingId}`),
        fetch(`/api/listings/${listingId}/reports`),
      ]);

      if (listingRes.status === 401 || reportsRes.status === 401) {
        router.push("/login");
        return;
      }
      if (listingRes.status === 404) {
        setError("Listing not found.");
        setLoading(false);
        return;
      }

      const listingData = await listingRes.json();
      const reportsData = await reportsRes.json();

      setListing(listingData.listing ?? null);
      setRows(reportsData.reports ?? []);
    } catch {
      setError("Failed to load listing history.");
    } finally {
      setLoading(false);
    }
  }, [listingId, router]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  // Normalize report relation (can be array or object from Supabase join)
  function getReport(row: HistoryRow): ReportSnapshot | null {
    const r = row.pricing_reports;
    if (!r) return null;
    return Array.isArray(r) ? r[0] ?? null : r;
  }

  const filteredRows = useMemo(() => {
    if (statusFilter === "all") return rows;
    return rows.filter((row) => {
      const report = getReport(row);
      return report?.status === statusFilter;
    });
  }, [rows, statusFilter]);

  async function handleRerun(row: HistoryRow) {
    const report = getReport(row);
    if (!report) return;

    setRerunningId(row.id);
    try {
      const res = await fetch(`/api/listings/${listingId}/rerun`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dates: {
            startDate: report.input_date_start,
            endDate: report.input_date_end,
          },
        }),
      });
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();
      if (data.shareId) {
        router.push(`/r/${data.shareId}`);
      } else {
        await loadData();
      }
    } catch {
      // Silently fail — user can retry
    } finally {
      setRerunningId(null);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="text-sm text-muted">Loading...</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-10">
      {/* Header */}
      <div>
        <Link
          href="/dashboard"
          className="text-sm text-muted hover:text-foreground"
        >
          &larr; Dashboard
        </Link>
        <h1 className="mt-2 text-2xl font-bold">
          {listing?.name ?? "Listing"} — Report History
        </h1>
        {listing?.input_address && (
          <p className="mt-0.5 text-sm text-muted">{listing.input_address}</p>
        )}
      </div>

      {error && (
        <Card>
          <p className="text-sm text-warning">{error}</p>
        </Card>
      )}

      {/* Filters */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted">Filter:</span>
        {(["all", "ready", "error"] as StatusFilter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setStatusFilter(f)}
            className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
              statusFilter === f
                ? "bg-foreground text-white"
                : "bg-white text-muted border border-border hover:text-foreground"
            }`}
          >
            {f === "all" ? "All" : f === "ready" ? "Ready" : "Error"}
          </button>
        ))}
        <span className="ml-auto text-xs text-muted">
          {filteredRows.length} report{filteredRows.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Report list */}
      {filteredRows.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">No reports found.</p>
        </Card>
      ) : (
        <Card>
          <div className="divide-y divide-border">
            {filteredRows.map((row) => {
              const report = getReport(row);
              if (!report) return null;

              const recommended =
                report.result_summary?.recommendedPrice?.nightly;
              const median = report.result_summary?.nightlyMedian;

              const statusColor =
                report.status === "ready"
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : report.status === "error"
                    ? "bg-rose-50 text-rose-700 border-rose-200"
                    : "bg-gray-50 text-gray-600 border-gray-200";

              return (
                <div key={row.id} className="px-3 py-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    {/* Left: info */}
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusColor}`}
                        >
                          {report.status}
                        </span>
                        <span className="text-xs text-muted">
                          {row.trigger}
                        </span>
                      </div>
                      <p className="text-sm">
                        <span className="font-medium">
                          {report.input_date_start} &rarr;{" "}
                          {report.input_date_end}
                        </span>
                      </p>
                      <p className="text-xs text-muted">
                        Created{" "}
                        {new Date(report.created_at).toLocaleDateString(
                          undefined,
                          {
                            year: "numeric",
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          }
                        )}
                      </p>
                      {report.status === "ready" && (
                        <p className="text-sm">
                          {recommended != null && (
                            <span className="font-semibold">
                              ${recommended}/night
                            </span>
                          )}
                          {median != null && (
                            <span className="ml-2 text-xs text-muted">
                              (median ${median})
                            </span>
                          )}
                        </p>
                      )}
                      {report.status === "error" && report.error_message && (
                        <p className="text-xs text-rose-600">
                          {report.error_message}
                        </p>
                      )}
                    </div>

                    {/* Right: actions */}
                    <div className="flex shrink-0 items-center gap-1.5">
                      {report.status === "ready" && (
                        <Link href={`/r/${report.share_id}`}>
                          <Button size="sm" variant="ghost">
                            View report
                          </Button>
                        </Link>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleRerun(row)}
                        disabled={rerunningId === row.id}
                      >
                        {rerunningId === row.id ? "Queued..." : "Re-run"}
                      </Button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}
