"use client";

import { useState, useMemo } from "react";
import type { ComparableListing, CompsSummary } from "@/lib/schemas";

// Match Airbnb room IDs extracted from URLs for pinned-comp detection.
function extractRoomId(url: string): string | null {
  const m = url.match(/\/rooms\/(\d+)/);
  return m ? m[1] : null;
}

function urlsMatchPinned(compUrl: string | null, pinnedUrls: string[]): boolean {
  if (!compUrl) return false;
  const compId = extractRoomId(compUrl);
  for (const pUrl of pinnedUrls) {
    if (compId && extractRoomId(pUrl) === compId) return true;
    if (compUrl.split("?")[0].toLowerCase() === pUrl.split("?")[0].toLowerCase()) return true;
  }
  return false;
}

// ── Date helpers ────────────────────────────────────────────────

function nextDay(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

function listingUrlForDate(url: string, date: string): string {
  const checkout = nextDay(date);
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}checkin=${date}&checkout=${checkout}`;
}

// ── Helpers ─────────────────────────────────────────────────────

function similarityBadgeClasses(similarity: number): string {
  if (similarity >= 0.8) return "bg-green-50 text-green-700";
  if (similarity >= 0.6) return "bg-yellow-50 text-yellow-700";
  return "bg-gray-100 text-gray-600";
}

function matchQualityLabel(stage: string): {
  label: string;
  description: string;
  classes: string;
} {
  switch (stage) {
    case "strict":
      return {
        label: "Strong match set",
        description: "Most of these listings are very close matches to your home.",
        classes: "bg-green-50 text-green-700",
      };
    case "medium":
      return {
        label: "Balanced match set",
        description: "We found a good mix of close matches and broader nearby comps.",
        classes: "bg-yellow-50 text-yellow-700",
      };
    default:
      return {
        label: "Broader match set",
        description: "There were fewer near-identical listings, so we used a wider comparison set.",
        classes: "bg-gray-100 text-gray-700",
      };
  }
}

function formatPropertyType(type: string): string {
  return type
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── Skeleton ────────────────────────────────────────────────────

function SkeletonCard() {
  return (
    <div className="animate-pulse rounded-xl border border-gray-100 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="h-4 w-3/5 rounded bg-gray-100" />
          <div className="h-3.5 w-4/5 rounded bg-gray-50" />
        </div>
        <div className="shrink-0 space-y-2 text-right">
          <div className="ml-auto h-6 w-24 rounded bg-gray-100" />
          <div className="ml-auto h-5 w-16 rounded-full bg-gray-50" />
        </div>
      </div>
      <div className="mt-3 flex gap-4 border-t border-gray-50 pt-3">
        <div className="h-3.5 w-20 rounded bg-gray-50" />
        <div className="h-3.5 w-16 rounded bg-gray-50" />
      </div>
    </div>
  );
}

// ── Listing Card ────────────────────────────────────────────────

function ComparableCard({
  listing,
  isPinned = false,
  selectedDate,
  isSnappedDate = false,
}: {
  listing: ComparableListing;
  isPinned?: boolean;
  selectedDate?: string | null;
  /** True when selectedDate is a snapped-to-nearest date, not the exactly clicked date. */
  isSnappedDate?: boolean;
}) {
  const matchPct = Math.round(listing.similarity * 100);
  const badgeClasses = similarityBadgeClasses(listing.similarity);

  // When a date is selected, use only the exact scraped price for that date.
  // Do NOT fall back to nightlyPrice or an average — that would mislead the user.
  const datePrice: number | undefined = selectedDate
    ? listing.priceByDate?.[selectedDate]
    : undefined;

  // No date selected → show the general comparable price.
  // Date selected + price found → show sampled date price.
  // Date selected + no price → show unavailable (not a fallback average).
  const hasSampledDatePrice = selectedDate != null && datePrice != null;
  const isPriceUnavailable = selectedDate != null && datePrice == null;
  const displayPrice = selectedDate ? datePrice : listing.nightlyPrice;

  // When a specific date is selected, append checkin/checkout so the Airbnb
  // listing page opens in the same date context as our shown price.
  const viewUrl = listing.url
    ? selectedDate
      ? listingUrlForDate(listing.url, selectedDate)
      : listing.url
    : null;

  return (
    <div
      className={`rounded-xl border p-4 transition hover:shadow-sm ${
        isPinned ? "border-amber-300 bg-amber-50/70 shadow-sm ring-1 ring-amber-100" : "border-gray-100 bg-white"
      }`}
    >
      {/* Top row: title+specs LEFT, price+badge RIGHT */}
      <div className="flex items-start justify-between gap-4">
        {/* Left zone */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-base font-medium text-gray-900">
              {listing.title}
            </p>
            {isPinned && (
              <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800 ring-1 ring-amber-200">
                Pinned by you
              </span>
            )}
          </div>
          <p className="mt-0.5 text-sm text-gray-600">
            {formatPropertyType(listing.propertyType)}
            {" · "}
            {listing.accommodates} guest{listing.accommodates !== 1 ? "s" : ""}
            {" · "}
            {listing.bedrooms} bd
            {" · "}
            {listing.baths} ba
          </p>
        </div>

        {/* Right zone */}
        <div className="shrink-0 text-right">
          {isPriceUnavailable ? (
            <p className="text-sm font-medium text-gray-400">
              No data for this date
            </p>
          ) : (
            <p className="text-2xl font-semibold text-gray-900">
              ${displayPrice}
              <span className="text-sm font-normal text-gray-500"> / night</span>
            </p>
          )}
          {hasSampledDatePrice && (
            <p className="text-[10px] text-emerald-600 font-medium">
              {isSnappedDate ? "nearest sampled date" : "exact date price"}
            </p>
          )}
          <p className={`text-[10px] font-medium ${
            listing.queryNights != null && listing.queryNights > 1
              ? "text-amber-600"
              : "text-gray-400"
          }`}>
            {listing.queryNights != null && listing.queryNights > 1
              ? `${listing.queryNights}-night min · price ÷ ${listing.queryNights}`
              : "1-night price"}
          </p>
          <span
            className={`mt-1 inline-block rounded-full px-2 py-1 text-xs font-medium ${badgeClasses}`}
          >
            {matchPct}% match
          </span>
        </div>
      </div>

      {/* Bottom meta row */}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-gray-50 pt-3 text-sm text-gray-500">
        {listing.rating != null && (
          <span>
            <span className="text-amber-500">&#9733;</span>
            {listing.rating.toFixed(2)}
            {listing.reviews != null && (
              <span className="text-gray-400"> ({listing.reviews})</span>
            )}
          </span>
        )}
        {listing.location && <span>{listing.location}</span>}
        {viewUrl && (
          <a
            href={viewUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto text-xs font-medium text-accent hover:underline"
          >
            View &rarr;
          </a>
        )}
      </div>
    </div>
  );
}

// ── Section ─────────────────────────────────────────────────────

type SortMode = "similarity" | "price";

interface ComparableListingsSectionProps {
  listings: ComparableListing[] | null | undefined;
  comps: CompsSummary | null | undefined;
  loading?: boolean;
  embedded?: boolean;
  pinnedUrls?: string[];
  /** Effective price date — nearest sampled date with real comp data. */
  selectedDate?: string | null;
  /** The date the user actually clicked. When this differs from selectedDate,
   *  a disclosure banner is shown explaining the price date substitution. */
  clickedDate?: string | null;
}

export function ComparableListingsSection({
  listings,
  comps,
  loading = false,
  embedded = false,
  pinnedUrls = [],
  selectedDate = null,
  clickedDate = null,
}: ComparableListingsSectionProps) {
  const isSnappedDate = !!(selectedDate && clickedDate && selectedDate !== clickedDate);
  const [sortBy, setSortBy] = useState<SortMode>("similarity");
  const [expanded, setExpanded] = useState(false);

  const matchQuality = comps
    ? matchQualityLabel(comps.filterStage)
    : null;

  const sorted = useMemo(() => {
    if (!listings || listings.length === 0) return [];
    const copy = [...listings];
    // When a date is selected, use only the exact date price (may be undefined).
    // When no date is selected, fall back to the general nightlyPrice.
    const getPrice = (listing: ComparableListing): number | undefined =>
      selectedDate ? listing.priceByDate?.[selectedDate] : listing.nightlyPrice;
    const comparePinned = (a: ComparableListing, b: ComparableListing) => {
      const aPinned = urlsMatchPinned(a.url ?? null, pinnedUrls);
      const bPinned = urlsMatchPinned(b.url ?? null, pinnedUrls);
      if (aPinned === bPinned) return 0;
      return aPinned ? -1 : 1;
    };
    if (sortBy === "similarity") {
      copy.sort((a, b) => comparePinned(a, b) || b.similarity - a.similarity);
    } else {
      // Listings with no price for the selected date sort to the end.
      copy.sort((a, b) => {
        const pinned = comparePinned(a, b);
        if (pinned !== 0) return pinned;
        const pa = getPrice(a);
        const pb = getPrice(b);
        if (pa == null && pb == null) return 0;
        if (pa == null) return 1;
        if (pb == null) return -1;
        return pa - pb;
      });
    }
    return copy;
  }, [listings, sortBy, pinnedUrls, selectedDate]);

  const visible = expanded ? sorted.slice(0, 20) : sorted.slice(0, 10);
  const hasMore = sorted.length > 10 && !expanded;
  const canCollapse = expanded && sorted.length > 10;

  // Subtext
  const used = comps?.usedForPricing ?? sorted.length;
  const locationBasis = "your area"; // fallback; caller can pass queryCriteria
  const showingCount = visible.length;

  // ── Loading state ──────────────────────────────────────────
  if (loading) {
    return (
      <section className={embedded ? "" : "mb-8"}>
        {!embedded && (
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Comparable listings</h2>
          </div>
        )}
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </section>
    );
  }

  // ── Empty state ────────────────────────────────────────────
  if (!listings || listings.length === 0) {
    if (comps && comps.usedForPricing > 0) {
      return (
        <section className={embedded ? "" : "mb-8"}>
          {!embedded && <h2 className="mb-2 text-lg font-semibold">Comparable listings</h2>}
          <div className="rounded-xl border border-gray-100 p-6 text-center">
            <p className="text-sm text-gray-500">
              Comparable details unavailable, but{" "}
              <span className="font-medium text-gray-700">
                {comps.usedForPricing}
              </span>{" "}
              listings were still used in the analysis.
            </p>
          </div>
        </section>
      );
    }
    return null;
  }

  // ── Populated state ────────────────────────────────────────
  return (
    <section className={embedded ? "" : "mb-8"}>
      {/* Header */}
      <div className="mb-1 flex items-center justify-between">
        {!embedded && <h2 className="text-lg font-semibold">Comparable listings</h2>}
        {matchQuality && (
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${matchQuality.classes}`}
          >
            {matchQuality.label}
          </span>
        )}
      </div>
      <p className="mb-4 text-sm text-gray-600">
        Based on {used} similar listings in {locationBasis}. Showing the {showingCount} clearest matches first so the recommendation is easier to trust at a glance.
      </p>
      {matchQuality && (
        <p className="mb-3 text-xs text-gray-500">
          {matchQuality.description}
        </p>
      )}
      {pinnedUrls.length > 0 && (
        <p className="mb-3 text-xs font-medium text-amber-700">
          Your pinned benchmark listing appears first in this list whenever it is present in the collected comps.
        </p>
      )}

      {/* Sort toggle */}
      <div className="mb-3 flex gap-1 self-start rounded-lg border border-gray-200 p-0.5 w-fit">
        <button
          onClick={() => setSortBy("similarity")}
          className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
            sortBy === "similarity"
              ? "bg-gray-900 text-white"
              : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Similarity
        </button>
        <button
          onClick={() => setSortBy("price")}
          className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
            sortBy === "price"
              ? "bg-gray-900 text-white"
              : "text-gray-500 hover:text-gray-700"
          }`}
        >
          Price
        </button>
      </div>

      {/* Date context / snap disclosure */}
      {isSnappedDate ? (
        <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          <p className="text-xs font-medium text-amber-800">
            <span className="font-semibold">{clickedDate}</span> was not directly sampled — no comp prices were scraped on that day.
          </p>
          <p className="mt-0.5 text-xs text-amber-700">
            Showing prices from <span className="font-semibold">{selectedDate}</span>, the nearest day that was actually queried.
            These are real scraped prices, but for a different day than you clicked.
          </p>
        </div>
      ) : selectedDate ? (
        <p className="mb-2 text-xs font-medium text-accent">
          Showing scraped nightly prices for {selectedDate}.
          {" "}Listings marked &ldquo;No data for this date&rdquo; were not queried on this day.
          {" "}Click a calendar day to change, or click the selected day to deselect.
        </p>
      ) : null}

      {/* Cards */}
      <div className="space-y-3">
        {visible.map((listing) => (
          <ComparableCard
            key={listing.id}
            listing={listing}
            isPinned={urlsMatchPinned(listing.url ?? null, pinnedUrls)}
            selectedDate={selectedDate}
            isSnappedDate={isSnappedDate}
          />
        ))}
      </div>

      {/* Show more / Show less */}
      {hasMore && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-4 w-full rounded-xl border border-gray-200 py-2.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50"
        >
          Show {Math.min(sorted.length, 20) - 10} more listings
        </button>
      )}
      {canCollapse && (
        <button
          onClick={() => setExpanded(false)}
          className="mt-4 w-full rounded-xl border border-gray-200 py-2.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50"
        >
          Show less
        </button>
      )}
    </section>
  );
}
