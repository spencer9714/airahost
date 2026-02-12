/**
 * Mock Pricing Core
 *
 * Generates deterministic pricing reports based on listing attributes.
 * Uses simple hashing for reproducible output.
 *
 * TODO: Replace this module with a call to the Python pricing service.
 * See pythonAdapter.ts for the integration boundary.
 */

import type {
  ListingInput,
  DiscountPolicy,
  ReportSummary,
  CalendarDay,
} from "@/lib/schemas";

const CORE_VERSION = "mock-v1.0.0";

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

  const nonRefDiscount = policy.refundable
    ? 0
    : policy.nonRefundableDiscountPct / 100;

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
  const estimatedMonthlyRevenue = Math.round(median * 30 * (occupancyPct / 100));

  const marketMedian = Math.round(median * (0.9 + rand() * 0.3));
  const priceDiff = marketMedian - median;
  const insightHeadline =
    priceDiff > 5
      ? `You may be underpricing by ~$${priceDiff} per night.`
      : priceDiff < -5
        ? `You're pricing $${Math.abs(priceDiff)} above the local median — consider if your amenities justify this.`
        : `Your pricing is well-aligned with the local market.`;

  const refundablePrices7 = calendar.slice(0, Math.min(7, calendar.length));
  const weeklyStayAvgNightly = Math.round(
    refundablePrices7.reduce((a, d) => a + d.refundablePrice, 0) /
      refundablePrices7.length
  );

  const refundablePrices28 = calendar.slice(0, Math.min(28, calendar.length));
  const monthlyStayAvgNightly = Math.round(
    refundablePrices28.reduce((a, d) => a + d.refundablePrice, 0) /
      refundablePrices28.length
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
    },
    calendar,
  };
}
