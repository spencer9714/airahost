import { createHash } from "crypto";

/**
 * Cache key computation — shared between /api/reports and /api/listings/[id]/rerun.
 * Mirrors worker/core/cache.py for consistency.
 *
 * Algorithm: SHA-256 of canonical JSON (sorted keys), first 32 hex chars.
 * Must stay byte-for-byte identical to worker/core/cache.py:compute_cache_key().
 */

const CACHE_SCHEMA_VERSION = "v1";

export function computeCacheKey(
  address: string,
  attributes: Record<string, unknown>,
  startDate: string,
  endDate: string,
  discountPolicy: Record<string, unknown>,
  listingUrl?: string,
  inputMode: string = "criteria"
): string {
  // Use the first enabled comp in preferredComps as the benchmark URL for cache keying.
  // This ensures different benchmark URLs produce different cache entries.
  const preferredCompsArr = Array.isArray(attributes.preferredComps)
    ? (attributes.preferredComps as Array<Record<string, unknown>>)
    : [];
  const firstEnabledComp = preferredCompsArr.find(
    (c) => c.enabled !== false && typeof c.listingUrl === "string" && c.listingUrl
  );
  const preferredCompListingUrl =
    typeof firstEnabledComp?.listingUrl === "string" ? firstEnabledComp.listingUrl : "";

  const payload: Record<string, unknown> = {
    address,
    bathrooms: attributes.bathrooms || 0,
    bedrooms: attributes.bedrooms || 0,
    cacheSchemaVersion: CACHE_SCHEMA_VERSION,
    endDate,
    inputMode,
    listing_url: listingUrl || "",
    maxGuests: attributes.maxGuests || 0,
    maxTotalDiscountPct: discountPolicy.maxTotalDiscountPct || 40,
    monthlyDiscountPct: discountPolicy.monthlyDiscountPct || 0,
    nonRefundableDiscountPct: discountPolicy.nonRefundableDiscountPct || 0,
    preferred_comp_listing_url: preferredCompListingUrl,
    propertyType: attributes.propertyType || "",
    refundable: discountPolicy.refundable ?? true,
    stackingMode: discountPolicy.stackingMode || "compound",
    startDate,
    weeklyDiscountPct: discountPolicy.weeklyDiscountPct || 0,
  };
  // Canonical JSON: alphabetically sorted keys, no spaces — matches json.dumps(sort_keys=True, separators=(",",":"))
  const canonical = JSON.stringify(payload, Object.keys(payload).sort());
  return createHash("sha256").update(canonical).digest("hex").slice(0, 32);
}
