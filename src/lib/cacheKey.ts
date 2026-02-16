/**
 * Cache key computation — shared between /api/reports and /api/listings/[id]/rerun.
 * Mirrors worker/core/cache.py for consistency.
 */
export function computeCacheKey(
  address: string,
  attributes: Record<string, unknown>,
  startDate: string,
  endDate: string,
  discountPolicy: Record<string, unknown>,
  listingUrl?: string,
  inputMode: string = "criteria"
): string {
  const payload: Record<string, unknown> = {
    address,
    bathrooms: attributes.bathrooms || 0,
    bedrooms: attributes.bedrooms || 0,
    endDate,
    inputMode,
    listing_url: listingUrl || "",
    maxGuests: attributes.maxGuests || 0,
    maxTotalDiscountPct: discountPolicy.maxTotalDiscountPct || 40,
    monthlyDiscountPct: discountPolicy.monthlyDiscountPct || 0,
    nonRefundableDiscountPct: discountPolicy.nonRefundableDiscountPct || 0,
    propertyType: attributes.propertyType || "",
    refundable: discountPolicy.refundable ?? true,
    stackingMode: discountPolicy.stackingMode || "compound",
    startDate,
    weeklyDiscountPct: discountPolicy.weeklyDiscountPct || 0,
  };
  // Canonical JSON with sorted keys
  const canonical = JSON.stringify(payload, Object.keys(payload).sort());
  // Sync FNV-1a-style hash for edge runtime compatibility
  const encoder = new TextEncoder();
  const data = encoder.encode(canonical);
  let hash = 0x811c9dc5;
  for (let i = 0; i < data.length; i++) {
    hash ^= data[i];
    hash = (hash * 0x01000193) >>> 0;
  }
  // Create a longer hash by running multiple rounds
  let result = "";
  for (let round = 0; round < 8; round++) {
    let h = hash ^ (round * 0x9e3779b9);
    for (let i = 0; i < data.length; i++) {
      h ^= data[i];
      h = (h * 0x01000193) >>> 0;
    }
    result += h.toString(16).padStart(8, "0");
  }
  return result.slice(0, 32);
}
