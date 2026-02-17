"use client";

import { useState, useMemo } from "react";
import type { ComparableListing, CompsSummary } from "@/lib/schemas";

// ── Helpers ─────────────────────────────────────────────────────

function similarityBadgeClasses(similarity: number): string {
  if (similarity >= 0.8) return "bg-green-50 text-green-700";
  if (similarity >= 0.6) return "bg-yellow-50 text-yellow-700";
  return "bg-gray-100 text-gray-600";
}

function confidenceLabel(stage: string): {
  label: string;
  classes: string;
} {
  switch (stage) {
    case "strict":
      return { label: "High confidence", classes: "bg-green-50 text-green-700" };
    case "medium":
      return { label: "Medium confidence", classes: "bg-yellow-50 text-yellow-700" };
    default:
      return { label: "Low confidence", classes: "bg-gray-100 text-gray-600" };
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

function ComparableCard({ listing }: { listing: ComparableListing }) {
  const matchPct = Math.round(listing.similarity * 100);
  const badgeClasses = similarityBadgeClasses(listing.similarity);

  return (
    <div className="rounded-xl border border-gray-100 p-4 transition hover:shadow-sm">
      {/* Top row: title+specs LEFT, price+badge RIGHT */}
      <div className="flex items-start justify-between gap-4">
        {/* Left zone */}
        <div className="min-w-0 flex-1">
          <p className="truncate text-base font-medium text-gray-900">
            {listing.title}
          </p>
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
          <p className="text-2xl font-semibold text-gray-900">
            ${listing.nightlyPrice}
            <span className="text-sm font-normal text-gray-500"> / night</span>
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
        {listing.url && (
          <a
            href={listing.url}
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
}

export function ComparableListingsSection({
  listings,
  comps,
  loading = false,
  embedded = false,
}: ComparableListingsSectionProps) {
  const [sortBy, setSortBy] = useState<SortMode>("similarity");
  const [expanded, setExpanded] = useState(false);

  const confidence = comps
    ? confidenceLabel(comps.filterStage)
    : null;

  const sorted = useMemo(() => {
    if (!listings || listings.length === 0) return [];
    const copy = [...listings];
    if (sortBy === "similarity") {
      copy.sort((a, b) => b.similarity - a.similarity);
    } else {
      copy.sort((a, b) => a.nightlyPrice - b.nightlyPrice);
    }
    return copy;
  }, [listings, sortBy]);

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
        {confidence && (
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${confidence.classes}`}
          >
            {confidence.label}
          </span>
        )}
      </div>
      <p className="mb-4 text-sm text-gray-500">
        Based on {used} similar listings in {locationBasis}. Showing top{" "}
        {showingCount} most similar.
      </p>

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

      {/* Cards */}
      <div className="space-y-3">
        {visible.map((listing) => (
          <ComparableCard key={listing.id} listing={listing} />
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
