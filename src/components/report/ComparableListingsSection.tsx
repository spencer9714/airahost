"use client";

import { useState, useMemo } from "react";
import type { BenchmarkInfo, ComparableListing, CompsSummary } from "@/lib/schemas";

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

function isPinnedListing(listing: ComparableListing, pinnedUrls: string[]): boolean {
  const flaggedPinned =
    (listing as ComparableListing & { isPinnedBenchmark?: boolean }).isPinnedBenchmark === true;
  if (flaggedPinned) return true;
  return urlsMatchPinned(listing.url ?? null, pinnedUrls);
}

// ── Date helpers ────────────────────────────────────────────────

function nextDay(dateStr: string, nights = 1): string {
  const d = new Date(dateStr + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + Math.max(1, nights));
  return d.toISOString().slice(0, 10);
}

function listingUrlForDate(url: string, date: string, nights = 1): string {
  const checkout = nextDay(date, nights);
  try {
    // Canonicalize: strip any existing date params (old or new key names),
    // then inject the correct Airbnb params check_in / check_out.
    const u = new URL(url, "https://www.airbnb.com");
    u.searchParams.delete("checkin");
    u.searchParams.delete("checkout");
    u.searchParams.delete("check_in");
    u.searchParams.delete("check_out");
    u.searchParams.set("check_in", date);
    u.searchParams.set("check_out", checkout);
    return u.toString();
  } catch {
    // Fallback for relative or malformed URLs — safe append.
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}check_in=${date}&check_out=${checkout}`;
  }
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

function formatComparableSpecs(listing: ComparableListing): string {
  const parts = [formatPropertyType(listing.propertyType)];

  if (typeof listing.accommodates === "number") {
    parts.push(`${listing.accommodates} guest${listing.accommodates !== 1 ? "s" : ""}`);
  }
  if (typeof listing.bedrooms === "number") {
    parts.push(`${listing.bedrooms} bd`);
  }
  if (typeof listing.baths === "number") {
    parts.push(`${listing.baths} ba`);
  }

  return parts.join(" · ");
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
}: {
  listing: ComparableListing;
  isPinned?: boolean;
  selectedDate?: string | null;
}) {
  const matchPct = Math.round(listing.similarity * 100);
  const badgeClasses = similarityBadgeClasses(listing.similarity);

  // When a date is selected, use only the exact scraped price for that date.
  // Do NOT fall back to nightlyPrice or an average — that would mislead the user.
  const datePrice: number | undefined = selectedDate
    ? (listing.priceByDateDetails?.[selectedDate]?.price ?? listing.priceByDate?.[selectedDate])
    : undefined;
  // No date selected → show the general comparable price.
  // Date selected + price found → show sampled date price.
  // Date selected + no price → show unavailable (not a fallback average).
  const hasSampledDatePrice = selectedDate != null && datePrice != null;
  const isPriceUnavailable = selectedDate != null && datePrice == null;
  const displayPrice = selectedDate ? datePrice : listing.nightlyPrice;
  const hasDisplayPrice =
    typeof displayPrice === "number" && Number.isFinite(displayPrice) && displayPrice > 0;
  const detailForDate = selectedDate ? listing.priceByDateDetails?.[selectedDate] : undefined;
  const queryNights =
    selectedDate && detailForDate?.queryNights != null
      ? detailForDate.queryNights
      : listing.queryNights != null
        ? listing.queryNights
        : 1;
  const queryTotalPrice =
    queryNights > 1
      ? typeof detailForDate?.queryTotalPrice === "number" && detailForDate.queryTotalPrice > 0
        ? detailForDate.queryTotalPrice
        : typeof listing.queryTotalPrice === "number" && listing.queryTotalPrice > 0
          ? listing.queryTotalPrice
        : typeof displayPrice === "number" && displayPrice > 0
          ? Number((displayPrice * queryNights).toFixed(2))
          : null
      : null;

  // When a specific date is selected, append checkin/checkout so the Airbnb
  // listing page opens in the same date context as our shown price.
  const viewUrl = listing.url
    ? selectedDate
      ? detailForDate?.url
        ? detailForDate.url
        : listingUrlForDate(listing.url, selectedDate, queryNights)
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
            {formatComparableSpecs(listing)}
          </p>
        </div>

        {/* Right zone */}
        <div className="shrink-0 text-right">
          {isPriceUnavailable ? (
            <div className="text-right">
              <p className="text-sm font-medium text-gray-400">
                No scraped data for this date
              </p>
            </div>
          ) : hasDisplayPrice ? (
            <p className="text-2xl font-semibold text-gray-900">
              ${displayPrice}
              <span className="text-sm font-normal text-gray-500"> / night</span>
            </p>
          ) : null}
          {hasSampledDatePrice && (
            <p className="text-[10px] text-emerald-600 font-medium">
              exact date price
            </p>
          )}
          <p className={`text-[10px] font-medium ${queryNights > 1 ? "text-amber-600" : "text-gray-400"}`}>
            {queryNights > 1 ? `${queryNights}-night-derived / night` : "1-night price"}
          </p>
          {queryNights > 1 && queryTotalPrice != null && !isPriceUnavailable && hasDisplayPrice && (
            <p className="text-[10px] text-amber-700">
              {`From $${queryTotalPrice} total for ${queryNights} nights`}
            </p>
          )}
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
  benchmarkInfo?: BenchmarkInfo | null;
  loading?: boolean;
  embedded?: boolean;
  pinnedUrls?: string[];
  /** Effective price date for exact day-level comp filtering. */
  selectedDate?: string | null;
  /** The date the user clicked. */
  clickedDate?: string | null;
}

export function ComparableListingsSection({
  listings,
  comps,
  loading = false,
  embedded = false,
  pinnedUrls = [],
  selectedDate = null,
}: ComparableListingsSectionProps) {
  const [sortBy, setSortBy] = useState<SortMode>("similarity");
  const [expanded, setExpanded] = useState(false);

  const matchQuality = comps
    ? matchQualityLabel(comps.filterStage)
    : null;

  const sorted = useMemo(() => {
    if (!listings || listings.length === 0) return [];
    const filtered = selectedDate
      ? listings.filter((listing) => listing.priceByDate?.[selectedDate] != null)
      : listings;
    const copy = [...filtered];
    // When a date is selected, use only the exact date price (may be undefined).
    // When no date is selected, fall back to the general nightlyPrice.
    const getPrice = (listing: ComparableListing): number | undefined =>
      selectedDate ? listing.priceByDate?.[selectedDate] : listing.nightlyPrice;
    const comparePinned = (a: ComparableListing, b: ComparableListing) => {
      const aPinned = isPinnedListing(a, pinnedUrls);
      const bPinned = isPinnedListing(b, pinnedUrls);
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

  const initialVisibleCount = embedded ? 5 : 10;
  const visible = expanded ? sorted : sorted.slice(0, initialVisibleCount);
  const hasMore = sorted.length > initialVisibleCount && !expanded;
  const canCollapse = expanded && sorted.length > initialVisibleCount;

  // Subtext
  const used = comps?.usedForPricing ?? sorted.length;
  const locationBasis = "your area"; // fallback; caller can pass queryCriteria
  const showingCount = visible.length;
  const nonPinnedVisibleForDate = selectedDate
    ? visible.filter((listing) => !isPinnedListing(listing, pinnedUrls))
    : [];
  const hasAnyComparableDataForSelectedDate = !!(
    selectedDate &&
    (nonPinnedVisibleForDate.length > 0
      ? nonPinnedVisibleForDate.some((listing) => listing.priceByDate?.[selectedDate] != null)
      : visible.some((listing) => listing.priceByDate?.[selectedDate] != null))
  );

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

      {/* Date context */}
      {selectedDate ? (
        !hasAnyComparableDataForSelectedDate ? (
          <p className="mb-2 text-xs font-medium text-amber-700">
            No comparable listing prices were queried exactly on {selectedDate}.
            {" "}Click a different calendar day, or click the selected day to deselect.
          </p>
        ) : (
          <p className="mb-2 text-xs font-medium text-accent">
            Showing scraped nightly prices for {selectedDate}.
            {" "}Click a calendar day to change, or click the selected day to deselect.
          </p>
        )
      ) : null}

      {/* Cards */}
      <div className="space-y-3">
        {visible.map((listing) => (
          <ComparableCard
            key={listing.id}
            listing={listing}
            isPinned={isPinnedListing(listing, pinnedUrls)}
            selectedDate={selectedDate}
          />
        ))}
      </div>

      {/* Show more / Show less */}
      {hasMore && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-4 w-full rounded-xl border border-gray-200 py-2.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50"
        >
          Show {sorted.length - initialVisibleCount} more listings
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
