import Link from "next/link";
import { computeFreshness } from "@/lib/freshness";

interface Props {
  /**
   * pricing_reports.market_captured_at — when Airbnb data was collected.
   * Falls back gracefully: pass completed_at or listing_reports.created_at
   * for reports created before migration 010.
   */
  marketCapturedAt: string | null | undefined;
  dateStart: string;
  dateEnd: string;
  reportType?: "live_analysis" | "forecast_snapshot" | string;
  /**
   * listing_reports.trigger — 'scheduled' | 'manual' | 'rerun'.
   * Used to distinguish nightly auto-reports from user-triggered live analyses.
   */
  trigger?: string;
  shareId?: string | null;
  compsUsed?: number | null;
}

function fmtDate(iso: string) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function ForecastBasis({
  marketCapturedAt,
  dateStart,
  dateEnd,
  reportType = "live_analysis",
  trigger,
  shareId,
  compsUsed,
}: Props) {
  const { dotClass, label, hint, status } = computeFreshness(marketCapturedAt);

  const isNightly = trigger === "scheduled";
  const isForecastSnapshot = reportType === "forecast_snapshot";

  // Human-readable label for how this report was generated
  const typeLabel = isForecastSnapshot
    ? "Forecast snapshot"
    : isNightly
    ? "30-Day Market Report"
    : "Live analysis";

  // Subtitle shown below the label row
  const typeNote = isForecastSnapshot
    ? "Derived from a prior live scrape — no fresh Airbnb data was fetched."
    : isNightly
    ? "Auto-generated nightly · based on your listing settings and live Airbnb market data"
    : null;

  return (
    <div className="rounded-2xl border border-border bg-white p-5 sm:p-6">
      <p className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-foreground/30">
        Market basis
      </p>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5">
        {/* Freshness dot + type label + age */}
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotClass}`} />
          <span className="text-sm font-medium text-foreground/70">{typeLabel}</span>
          {status !== "missing" && (
            <span className="text-sm text-foreground/45">· {label}</span>
          )}
        </div>

        {/* Date coverage */}
        {dateStart && dateEnd && (
          <div className="flex items-center gap-1">
            <span className="text-xs text-foreground/35">Covers</span>
            <span className="text-xs font-medium text-foreground/60">
              {fmtDate(dateStart)} – {fmtDate(dateEnd)}
            </span>
          </div>
        )}

        {/* Comp count */}
        {compsUsed != null && compsUsed > 0 && (
          <div className="flex items-center gap-1">
            <span className="text-xs text-foreground/35">Based on</span>
            <span className="text-xs font-medium text-foreground/60">
              {compsUsed} comparable listing{compsUsed !== 1 ? "s" : ""}
            </span>
          </div>
        )}
      </div>

      {typeNote && (
        <p className="mt-3 text-xs text-foreground/45">{typeNote}</p>
      )}

      {hint && (
        <p className="mt-2 text-xs font-medium text-amber-600">{hint}</p>
      )}

      {shareId && (
        <Link
          href={`/r/${shareId}`}
          className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
        >
          View full report →
        </Link>
      )}
    </div>
  );
}
