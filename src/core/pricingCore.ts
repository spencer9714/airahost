/**
 * Mock Pricing Core
 *
 * Generates deterministic pricing reports based on listing attributes.
 * Uses simple hashing for reproducible output.
 *
 * Used by the /r/demo page for seeded demo reports.
 * Real pricing is handled by the worker queue (see worker/).
 */

import type {
  ListingInput,
  DiscountPolicy,
  ReportSummary,
  CalendarDay,
  RecommendedPrice,
  PriceDistribution,
} from "@/lib/schemas";

const CORE_VERSION = "airahost-v1.0";

// ── Deterministic hash ──────────────────────────────────────────

function simpleHash(str: string): number {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) {
    hash = (hash * 33) ^ str.charCodeAt(i);
  }
  return Math.abs(hash);
}

function seededRandom(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

// ── Price calculation helpers ───────────────────────────────────

function getBaseMultiplier(input: ListingInput): number {
  const typeMultipliers: Record<string, number> = {
    entire_home: 1.0,
    private_room: 0.55,
    shared_room: 0.3,
    hotel_room: 0.7,
  };
  const base = typeMultipliers[input.propertyType] ?? 1.0;
  const bedroomBoost = input.bedrooms * 0.15;
  const bathroomBoost = (input.bathrooms - 1) * 0.08;
  const guestBoost = Math.max(0, input.maxGuests - 2) * 0.03;
  const amenityBoost = (input.amenities?.length ?? 0) * 0.02;

  return base + bedroomBoost + bathroomBoost + guestBoost + amenityBoost;
}

function applyDiscount(
  basePrice: number,
  stayLength: number,
  policy: DiscountPolicy
): { refundablePrice: number; nonRefundablePrice: number } {
  let lengthDiscount = 0;
  if (stayLength >= 28 && policy.monthlyDiscountPct > 0) {
    lengthDiscount = policy.monthlyDiscountPct / 100;
  } else if (stayLength >= 7 && policy.weeklyDiscountPct > 0) {
    lengthDiscount = policy.weeklyDiscountPct / 100;
  }

  // Always compute non-refundable discount so the UI can show both prices
  // for comparison, regardless of the user's cancellation preference.
  const nonRefDiscount = policy.nonRefundableDiscountPct / 100;

  let refundableDiscount: number;
  let nonRefundableDiscount: number;

  switch (policy.stackingMode) {
    case "best_only":
      refundableDiscount = lengthDiscount;
      nonRefundableDiscount = Math.max(lengthDiscount, nonRefDiscount);
      break;
    case "additive":
      refundableDiscount = lengthDiscount;
      nonRefundableDiscount = Math.min(
        lengthDiscount + nonRefDiscount,
        policy.maxTotalDiscountPct / 100
      );
      break;
    case "compound":
    default:
      refundableDiscount = lengthDiscount;
      nonRefundableDiscount = Math.min(
        1 - (1 - lengthDiscount) * (1 - nonRefDiscount),
        policy.maxTotalDiscountPct / 100
      );
      break;
  }

  refundableDiscount = Math.min(
    refundableDiscount,
    policy.maxTotalDiscountPct / 100
  );

  return {
    refundablePrice: Math.round(basePrice * (1 - refundableDiscount)),
    nonRefundablePrice: Math.round(basePrice * (1 - nonRefundableDiscount)),
  };
}

function averageRefundablePriceForStay(
  basePrices: number[],
  stayLength: number,
  policy: DiscountPolicy
): number {
  if (basePrices.length === 0) return 0;
  const total = basePrices.reduce(
    (sum, p) => sum + applyDiscount(p, stayLength, policy).refundablePrice,
    0
  );
  return Math.round(total / basePrices.length);
}

function buildStayLengthAverages(
  basePrices: number[],
  totalDays: number,
  policy: DiscountPolicy
): Array<{ stayLength: number; avgNightly: number; lengthDiscountPct: number }> {
  if (totalDays < 1) return [];

  const points = new Set<number>([1, totalDays]);
  if (totalDays >= 7) points.add(7);
  if (totalDays >= 28) points.add(28);

  return Array.from(points)
    .sort((a, b) => a - b)
    .map((stayLength) => {
      let lengthDiscountPct = 0;
      if (stayLength >= 28 && policy.monthlyDiscountPct > 0) {
        lengthDiscountPct = policy.monthlyDiscountPct;
      } else if (stayLength >= 7 && policy.weeklyDiscountPct > 0) {
        lengthDiscountPct = policy.weeklyDiscountPct;
      }
      return {
        stayLength,
        avgNightly: averageRefundablePriceForStay(basePrices, stayLength, policy),
        lengthDiscountPct,
      };
    });
}

// ── Main function ───────────────────────────────────────────────

export interface PricingCoreInput {
  listing: ListingInput;
  startDate: string;
  endDate: string;
  discountPolicy: DiscountPolicy;
}

export interface PricingCoreOutput {
  coreVersion: string;
  summary: ReportSummary;
  calendar: CalendarDay[];
}

export function generatePricingReport(
  input: PricingCoreInput
): PricingCoreOutput {
  const seed = simpleHash(
    input.listing.address + input.listing.propertyType + input.listing.bedrooms
  );
  const rand = seededRandom(seed);

  const multiplier = getBaseMultiplier(input.listing);
  const baseNightly = Math.round(60 + multiplier * 90 + rand() * 40);

  const start = new Date(input.startDate);
  const end = new Date(input.endDate);
  const totalDays = Math.round(
    (end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)
  );

  const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const calendar: CalendarDay[] = [];

  for (let i = 0; i < totalDays; i++) {
    const d = new Date(
      Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate() + i)
    );
    const dow = d.getUTCDay();
    const isWeekend = dow === 5 || dow === 6;

    const dailyVariation = Math.round((rand() - 0.5) * 20);
    const weekendBoost = isWeekend ? Math.round(baseNightly * 0.15) : 0;
    const basePrice = baseNightly + dailyVariation + weekendBoost;

    const { refundablePrice, nonRefundablePrice } = applyDiscount(
      basePrice,
      totalDays,
      input.discountPolicy
    );

    calendar.push({
      date: d.toISOString().split("T")[0],
      dayOfWeek: dayNames[dow],
      isWeekend,
      // Canonical recommendation field — set equal to basePrice for demo data.
      // Demo reports do not distinguish market vs recommendation; UI consumers
      // that read recommendedDailyPrice ?? basePrice get the same value either way.
      recommendedDailyPrice: basePrice,
      // baseDailyPrice = raw market median (same as basePrice in demo, no scrape).
      baseDailyPrice: basePrice,
      basePrice,
      refundablePrice,
      nonRefundablePrice,
    });
  }

  const basePrices = calendar.map((d) => d.basePrice);
  const weekdayPrices = calendar
    .filter((d) => !d.isWeekend)
    .map((d) => d.basePrice);
  const weekendPrices = calendar
    .filter((d) => d.isWeekend)
    .map((d) => d.basePrice);

  const sorted = [...basePrices].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const min = sorted[0];
  const max = sorted[sorted.length - 1];

  const weekdayAvg = weekdayPrices.length
    ? Math.round(
        weekdayPrices.reduce((a, b) => a + b, 0) / weekdayPrices.length
      )
    : baseNightly;
  const weekendAvg = weekendPrices.length
    ? Math.round(
        weekendPrices.reduce((a, b) => a + b, 0) / weekendPrices.length
      )
    : baseNightly;

  const occupancyPct = Math.round(55 + rand() * 30);
  const selectedRangeAvgNightly = averageRefundablePriceForStay(
    basePrices,
    totalDays,
    input.discountPolicy
  );
  const estimatedMonthlyRevenue = Math.round(
    selectedRangeAvgNightly * 30 * (occupancyPct / 100)
  );

  // Keep this rand() call so the seed sequence stays stable for all inputs.
  void rand();

  // Canonical recommendation contract: nightly must equal calendar[0].recommendedDailyPrice.
  // calendar[0].recommendedDailyPrice was set to basePrice (demand-adjusted, weekend-boosted)
  // earlier in the loop — that is the correct day-0 value to pin here.
  // The old recNightly (3–8% above median) is preserved as windowMedian for secondary context.
  const recNightly = Math.round(median * (1.03 + rand() * 0.05));
  const recommendedPrice: RecommendedPrice = {
    nightly: calendar[0]?.recommendedDailyPrice ?? recNightly,
    weekdayEstimate: Math.round(weekdayAvg * (1.02 + rand() * 0.04)),
    weekendEstimate: Math.round(weekendAvg * (1.03 + rand() * 0.05)),
    discountApplied: 0,
    notes: "Demo data: nightly equals day-0 recommendedDailyPrice per canonical contract.",
    windowMedian: recNightly,
  };

  // Price distribution from sorted comp prices (quartiles).
  const p25 = sorted[Math.floor(sorted.length * 0.25)];
  const p75 = sorted[Math.floor(sorted.length * 0.75)];
  const priceDistribution: PriceDistribution = {
    min,
    p25,
    median,
    p75,
    max,
    currency: "USD",
  };

  const insightHeadline =
    recNightly > median + 5
      ? `We recommend $${recNightly}/night — positioning your listing $${recNightly - median} above the local median.`
      : recNightly < median - 5
        ? `This market is competitive. We suggest $${recNightly}/night to stay well-booked against nearby listings.`
        : `Your listing is well-matched to local rates. We recommend $${recNightly}/night based on comparable properties nearby.`;

  const weeklyStayAvgNightly = averageRefundablePriceForStay(
    basePrices,
    Math.min(7, totalDays),
    input.discountPolicy
  );
  const monthlyStayAvgNightly = averageRefundablePriceForStay(
    basePrices,
    Math.min(28, totalDays),
    input.discountPolicy
  );
  const stayLengthAverages = buildStayLengthAverages(
    basePrices,
    totalDays,
    input.discountPolicy
  );

  return {
    coreVersion: CORE_VERSION,
    summary: {
      insightHeadline,
      nightlyMin: min,
      nightlyMedian: median,
      nightlyMax: max,
      occupancyPct,
      weekdayAvg,
      weekendAvg,
      estimatedMonthlyRevenue,
      weeklyStayAvgNightly,
      monthlyStayAvgNightly,
      selectedRangeNights: totalDays,
      selectedRangeAvgNightly,
      stayLengthAverages,
      recommendedPrice,
      priceDistribution,
    },
    calendar,
  };
}
