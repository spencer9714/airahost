import { createHash } from "crypto";

/**
 * Cache key computation — shared between /api/reports and /api/listings/[id]/rerun.
 * Mirrors worker/core/cache.py for consistency.
 *
 * Algorithm: SHA-256 of canonical JSON (sorted keys), first 32 hex chars.
 * Must stay byte-for-byte identical to worker/core/cache.py:compute_cache_key().
 */

const CACHE_SCHEMA_VERSION = "v2";

/**
 * Extract Airbnb room ID from a listing URL.
 * Returns the URL itself as fallback (rare; logs a sentinel string into the key).
 */
function extractRoomIdOrFallback(url: string): string {
  const m = url.match(/\/rooms\/(\d+)/);
  if (m) return m[1];
  return url;
}

export function computeCacheKey(
  address: string,
  attributes: Record<string, unknown>,
  startDate: string,
  endDate: string,
  discountPolicy: Record<string, unknown>,
  listingUrl?: string,
  inputMode: string = "criteria"
): string {
  // Preferred comps in cache key:
  //   - Include ALL enabled preferred comps (not just the first).
  //   - Preserve order — primary is index 0, swapping order changes pricing.
  //   - Use roomId (canonical) so URL query-param variants collapse.
  const preferredCompsArr = Array.isArray(attributes.preferredComps)
    ? (attributes.preferredComps as Array<Record<string, unknown>>)
    : [];
  const preferredCompRoomIds: string[] = [];
  for (const pc of preferredCompsArr) {
    if (!pc || pc.enabled === false) continue;
    const url = typeof pc.listingUrl === "string" ? pc.listingUrl.trim() : "";
    if (!url) continue;
    preferredCompRoomIds.push(extractRoomIdOrFallback(url));
  }

  // Excluded comps in cache key:
  //   - Sorted (order doesn't matter — set semantics).
  //   - CSV-joined for compact representation.
  const excludedCompsArr = Array.isArray(attributes.excludedComps)
    ? (attributes.excludedComps as Array<Record<string, unknown>>)
    : [];
  const excludedRoomIdsSet = new Set<string>();
  for (const ec of excludedCompsArr) {
    if (!ec) continue;
    const rid = typeof ec.roomId === "string" ? ec.roomId.trim() : "";
    if (rid) excludedRoomIdsSet.add(rid);
  }
  const excludedRoomIds = Array.from(excludedRoomIdsSet).sort().join(",");

  const payload: Record<string, unknown> = {
    address,
    bathrooms: attributes.bathrooms || 0,
    bedrooms: attributes.bedrooms || 0,
    cacheSchemaVersion: CACHE_SCHEMA_VERSION,
    endDate,
    excluded_room_ids: excludedRoomIds,
    inputMode,
    listing_url: listingUrl || "",
    maxGuests: attributes.maxGuests || 0,
    maxTotalDiscountPct: discountPolicy.maxTotalDiscountPct || 40,
    monthlyDiscountPct: discountPolicy.monthlyDiscountPct || 0,
    nonRefundableDiscountPct: discountPolicy.nonRefundableDiscountPct || 0,
    preferred_comp_room_ids: preferredCompRoomIds,
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
