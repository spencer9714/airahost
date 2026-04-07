import Link from "next/link";
import { computeFreshness } from "@/lib/freshness";

interface Props {
  marketCapturedAt: string | null | undefined;
  dateStart: string;
  dateEnd: string;
  reportType?: "live_analysis" | "forecast_snapshot" | string;
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

  const typeLabel = isForecastSnapshot
    ? "Forecast snapshot"
    : isNightly
    ? "Nightly report"
    : "Live analysis";

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1">
      {/* Freshness dot + type */}
      <span className="flex items-center gap-1.5 text-xs text-foreground/40">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotClass}`} />
        {typeLabel}
        {status !== "missing" && (
          <span className="text-foreground/30">· {label}</span>
        )}
      </span>

      {/* Date coverage */}
      {dateStart && dateEnd && (
        <span className="text-xs text-foreground/30">
          {fmtDate(dateStart)} – {fmtDate(dateEnd)}
        </span>
      )}

      {/* Comp count */}
      {compsUsed != null && compsUsed > 0 && (
        <span className="text-xs text-foreground/30">
          {compsUsed} comp{compsUsed !== 1 ? "s" : ""}
        </span>
      )}

      {/* Full report link */}
      {shareId && (
        <Link
          href={`/r/${shareId}`}
          className="ml-auto text-xs font-medium text-accent/70 transition-colors hover:text-accent"
        >
          Full report →
        </Link>
      )}

      {/* Stale data warning */}
      {hint && (
        <span className="w-full text-xs font-medium text-amber-600/80">{hint}</span>
      )}

      {/* Forecast snapshot note */}
      {isForecastSnapshot && (
        <span className="w-full text-xs text-foreground/30">
          Derived from a prior live scrape — no fresh Airbnb data was fetched.
        </span>
      )}
    </div>
  );
}
