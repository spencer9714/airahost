/**
 * Python Pricing Service Adapter
 *
 * Calls the FastAPI backend (Cloud Run) to scrape Airbnb comparables
 * and returns a pricing recommendation mapped to PricingCoreOutput.
 *
 * Environment variable:
 *   NEXT_PUBLIC_PYTHON_API_URL — base URL of the Python service
 *     e.g. "https://ariahost-pricing-xxxxx-de.a.run.app"
 *
 * To switch from mock to real:
 *   Update the import in /api/reports/route.ts
 *     from: import { generatePricingReport } from "@/core/pricingCore"
 *     to:   import { generatePricingReport } from "@/core/pythonAdapter"
 */

import type {
  ListingInput,
  DiscountPolicy,
  ReportSummary,
  CalendarDay,
} from "@/lib/schemas";

// ── Python API types ──────────────────────────────────────────

export interface EstimateRequest {
  listing_url: string;
  checkin: string;
  checkout: string;
  adults: number;
  top_k?: number;
  max_scroll_rounds?: number;
  new_listing_discount?: number;
  location?: string;
}

export interface ListingSpecOut {
  url: string;
  title: string;
  location: string;
  accommodates: number | null;
  bedrooms: number | null;
  beds: number | null;
  baths: number | null;
  property_type: string;
  nightly_price: number | null;
  currency: string;
  rating: number | null;
  reviews: number | null;
  similarity: number | null;
}

export interface DiscountSuggestion {
  weekly_discount_pct: number;
  monthly_discount_pct: number;
  non_refundable_discount_pct: number;
  weekly_nightly: number | null;
  monthly_nightly: number | null;
  non_refundable_nightly: number | null;
}

export interface RecommendationStats {
  picked_n: number;
  weighted_median: number | null;
  discount_applied: number;
  recommended_nightly: number | null;
  p25: number | null;
  p75: number | null;
  min: number | null;
  max: number | null;
}

export interface EstimateResponse {
  target: ListingSpecOut;
  comparables: ListingSpecOut[];
  recommendation: RecommendationStats;
  discount_suggestions: DiscountSuggestion;
  total_comparables_found: number;
}

// ── Raw fetch helper ──────────────────────────────────────────

const API_BASE =
  process.env.NEXT_PUBLIC_PYTHON_API_URL ?? "http://localhost:8000";

export async function fetchEstimate(
  req: EstimateRequest
): Promise<EstimateResponse> {
  const url = `${API_BASE}/api/v1/estimate`;

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal: AbortSignal.timeout(300_000), // 5 min — Playwright scraping is slow
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail =
      (body as { detail?: string } | null)?.detail ?? res.statusText;
    throw new Error(`Pricing API error (${res.status}): ${detail}`);
  }

  return (await res.json()) as EstimateResponse;
}

// ── PricingCore-compatible interface ──────────────────────────

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

/**
 * Drop-in replacement for the mock `generatePricingReport`.
 *
 * Calls the Python scraping API, then maps the response into the
 * same PricingCoreOutput shape the frontend already consumes.
 */
export async function generatePricingReport(
  input: PricingCoreInput
): Promise<PricingCoreOutput> {
  // Build the listing URL from address (or use address directly as location)
  // The Python API needs a real Airbnb URL. If the frontend only has an address,
  // we pass it as the `location` override and use a placeholder URL.
  const estimateReq: EstimateRequest = {
    listing_url: `https://www.airbnb.com/s/${encodeURIComponent(input.listing.address)}/homes`,
    checkin: input.startDate,
    checkout: input.endDate,
    adults: input.listing.maxGuests,
    location: input.listing.address,
  };

  const data = await fetchEstimate(estimateReq);
  const rec = data.recommendation;
  const ds = data.discount_suggestions;

  const median = rec.recommended_nightly ?? rec.weighted_median ?? 0;
  const nightlyMin = rec.min ?? Math.round(median * 0.75);
  const nightlyMax = rec.max ?? Math.round(median * 1.25);

  // Generate a synthetic calendar from the recommendation
  const start = new Date(input.startDate);
  const end = new Date(input.endDate);
  const totalDays = Math.round(
    (end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)
  );
  const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const calendar: CalendarDay[] = [];

  for (let i = 0; i < totalDays; i++) {
    const d = new Date(
      Date.UTC(
        start.getUTCFullYear(),
        start.getUTCMonth(),
        start.getUTCDate() + i
      )
    );
    const dow = d.getUTCDay();
    const isWeekend = dow === 5 || dow === 6;
    const weekendBoost = isWeekend ? Math.round(median * 0.12) : 0;
    const basePrice = Math.round(median + weekendBoost);

    // Apply discount policy
    const lengthDiscount =
      totalDays >= 28
        ? input.discountPolicy.monthlyDiscountPct / 100
        : totalDays >= 7
          ? input.discountPolicy.weeklyDiscountPct / 100
          : 0;

    const nonRefDiscount = input.discountPolicy.refundable
      ? 0
      : input.discountPolicy.nonRefundableDiscountPct / 100;

    const refundablePrice = Math.round(basePrice * (1 - lengthDiscount));
    const nonRefundablePrice = Math.round(
      basePrice * (1 - Math.min(lengthDiscount + nonRefDiscount, input.discountPolicy.maxTotalDiscountPct / 100))
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

  const weekdayPrices = calendar
    .filter((d) => !d.isWeekend)
    .map((d) => d.basePrice);
  const weekendPrices = calendar
    .filter((d) => d.isWeekend)
    .map((d) => d.basePrice);

  const weekdayAvg = weekdayPrices.length
    ? Math.round(
        weekdayPrices.reduce((a, b) => a + b, 0) / weekdayPrices.length
      )
    : Math.round(median);
  const weekendAvg = weekendPrices.length
    ? Math.round(
        weekendPrices.reduce((a, b) => a + b, 0) / weekendPrices.length
      )
    : Math.round(median);

  const occupancyPct = 70; // Conservative estimate
  const estimatedMonthlyRevenue = Math.round(
    median * 30 * (occupancyPct / 100)
  );

  // Weekly / monthly stay average nightly (with discount applied)
  const weeklyStayAvgNightly = ds.weekly_nightly ?? Math.round(median * 0.92);
  const monthlyStayAvgNightly =
    ds.monthly_nightly ?? Math.round(median * 0.82);

  const insightHeadline =
    rec.discount_applied > 0
      ? `Recommended ${Math.round(rec.discount_applied * 100)}% new-listing discount applied. Market median: $${rec.weighted_median?.toFixed(0) ?? "N/A"}/night.`
      : `Your pricing is aligned with the local market median of $${median.toFixed(0)}/night.`;

  return {
    coreVersion: "python-v1.0.0",
    summary: {
      insightHeadline,
      nightlyMin,
      nightlyMedian: Math.round(median),
      nightlyMax,
      occupancyPct,
      weekdayAvg,
      weekendAvg,
      estimatedMonthlyRevenue,
      weeklyStayAvgNightly: Math.round(weeklyStayAvgNightly),
      monthlyStayAvgNightly: Math.round(monthlyStayAvgNightly),
    },
    calendar,
  };
}
