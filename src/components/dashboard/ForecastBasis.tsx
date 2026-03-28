import Link from "next/link";

interface Props {
  linkedAt: string | null;
  dateStart: string;
  dateEnd: string;
  reportType?: "live_analysis" | "forecast_snapshot" | string;
  shareId?: string | null;
  compsUsed?: number | null;
}

function freshnessInfo(linkedAt: string | null): {
  dot: string;
  label: string;
  hint: string | null;
  daysAgo: number | null;
} {
  if (!linkedAt) {
    return { dot: "bg-gray-300", label: "No data", hint: null, daysAgo: null };
  }
  const days = Math.floor(
    (Date.now() - new Date(linkedAt).getTime()) / 86_400_000
  );
  if (days === 0) return { dot: "bg-emerald-400", label: "Updated today", hint: null, daysAgo: 0 };
  if (days === 1) return { dot: "bg-emerald-400", label: "Updated yesterday", hint: null, daysAgo: 1 };
  if (days <= 3) return { dot: "bg-emerald-400", label: `${days}d ago`, hint: null, daysAgo: days };
  if (days <= 7) return { dot: "bg-amber-400", label: `${days}d ago`, hint: "Consider refreshing soon", daysAgo: days };
  return {
    dot: "bg-rose-400",
    label: `${days}d ago`,
    hint: "Market data is stale — run a fresh analysis for accurate pricing",
    daysAgo: days,
  };
}

function fmtDate(iso: string) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function ForecastBasis({
  linkedAt,
  dateStart,
  dateEnd,
  reportType = "live_analysis",
  shareId,
  compsUsed,
}: Props) {
  const { dot, label, hint } = freshnessInfo(linkedAt);
  const typeLabel =
    reportType === "forecast_snapshot" ? "Forecast snapshot" : "Live analysis";

  return (
    <div className="rounded-2xl border border-border bg-white p-5 sm:p-6">
      <p className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-foreground/30">
        Market basis
      </p>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5">
        {/* Freshness */}
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
          <span className="text-sm font-medium text-foreground/70">{typeLabel}</span>
          <span className="text-sm text-foreground/45">· {label}</span>
        </div>

        {/* Coverage */}
        {dateStart && dateEnd && (
          <div className="flex items-center gap-1">
            <span className="text-xs text-foreground/35">Covers</span>
            <span className="text-xs font-medium text-foreground/60">
              {fmtDate(dateStart)} – {fmtDate(dateEnd)}
            </span>
          </div>
        )}

        {/* Comps */}
        {compsUsed != null && compsUsed > 0 && (
          <div className="flex items-center gap-1">
            <span className="text-xs text-foreground/35">Based on</span>
            <span className="text-xs font-medium text-foreground/60">
              {compsUsed} comparable listing{compsUsed !== 1 ? "s" : ""}
            </span>
          </div>
        )}
      </div>

      {hint && (
        <p className="mt-3 text-xs font-medium text-amber-600">{hint}</p>
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
