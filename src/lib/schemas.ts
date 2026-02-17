import { z } from "zod";

// ── Property Input ──────────────────────────────────────────────

export const propertyTypeEnum = z.enum([
  "entire_home",
  "private_room",
  "shared_room",
  "hotel_room",
]);

export const amenityEnum = z.enum([
  "wifi",
  "kitchen",
  "washer",
  "dryer",
  "ac",
  "heating",
  "pool",
  "hot_tub",
  "free_parking",
  "ev_charger",
  "gym",
  "bbq",
  "fire_pit",
  "piano",
  "lake_access",
  "ski_in_out",
  "beach_access",
]);

export const listingInputSchema = z.object({
  address: z.string().min(5, "Please enter a valid address"),
  propertyType: propertyTypeEnum,
  bedrooms: z.number().int().min(0).max(20),
  bathrooms: z.number().min(0.5).max(20).multipleOf(0.5),
  maxGuests: z.number().int().min(1).max(50),
  sizeSqFt: z.number().int().min(0).max(50000).optional(),
  amenities: z.array(amenityEnum).optional().default([]),
});

// ── Date Input ──────────────────────────────────────────────────

export const dateInputSchema = z
  .object({
    startDate: z.string().refine((v) => !isNaN(Date.parse(v)), "Invalid date"),
    endDate: z.string().refine((v) => !isNaN(Date.parse(v)), "Invalid date"),
  })
  .refine(
    (d) => {
      const start = new Date(d.startDate);
      const end = new Date(d.endDate);
      const diff = (end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24);
      return diff >= 1 && diff <= 30;
    },
    { message: "Date range must be 1–30 days" }
  );

// ── Discount Policy ─────────────────────────────────────────────

export const discountStackingModeEnum = z.enum([
  "compound",
  "best_only",
  "additive",
]);

export const discountPolicySchema = z.object({
  weeklyDiscountPct: z.number().min(0).max(50).default(0),
  monthlyDiscountPct: z.number().min(0).max(70).default(0),
  refundable: z.boolean().default(true),
  nonRefundableDiscountPct: z.number().min(0).max(30).default(0),
  stackingMode: discountStackingModeEnum.default("compound"),
  maxTotalDiscountPct: z.number().min(0).max(80).default(40),
});

export const lastMinuteStrategyModeEnum = z.enum(["auto", "manual"]);
export const lastMinuteStrategyPreferenceSchema = z.object({
  mode: lastMinuteStrategyModeEnum.default("auto"),
  aggressiveness: z.number().int().min(0).max(100).default(50),
  floor: z.number().min(0.65).max(0.9).default(0.65),
  cap: z.number().min(1.0).max(1.1).default(1.05),
});

// ── Input Mode ──────────────────────────────────────────────────

export const inputModeEnum = z.enum(["url", "criteria"]);

// ── Full Report Request ─────────────────────────────────────────

export const createReportRequestSchema = z.object({
  inputMode: inputModeEnum.default("criteria"),
  listing: listingInputSchema,
  dates: dateInputSchema,
  discountPolicy: discountPolicySchema,
  lastMinuteStrategy: lastMinuteStrategyPreferenceSchema.optional(),
  listingUrl: z.string().url().optional(),
  saveToListings: z
    .object({
      enabled: z.boolean().default(false),
      name: z.string().min(1).max(100).optional(),
    })
    .optional(),
});

export type CreateReportRequest = z.infer<typeof createReportRequestSchema>;
export type InputMode = z.infer<typeof inputModeEnum>;
export type ListingInput = z.infer<typeof listingInputSchema>;
export type DateInput = z.infer<typeof dateInputSchema>;
export type DiscountPolicy = z.infer<typeof discountPolicySchema>;
export type LastMinuteStrategyMode = z.infer<typeof lastMinuteStrategyModeEnum>;
export type LastMinuteStrategyPreference = z.infer<typeof lastMinuteStrategyPreferenceSchema>;
export type PropertyType = z.infer<typeof propertyTypeEnum>;
export type Amenity = z.infer<typeof amenityEnum>;
export type DiscountStackingMode = z.infer<typeof discountStackingModeEnum>;

// ── Report Output Types ─────────────────────────────────────────

export interface CalendarDay {
  date: string;
  dayOfWeek: string;
  isWeekend: boolean;
  basePrice: number;
  refundablePrice: number;
  nonRefundablePrice: number;
  dynamicAdjustment?: {
    demandScore: number;
    confidence: "low" | "medium" | "high";
    timeMultiplier: number;
    demandAdjustment: number;
    finalMultiplier: number;
    reasons: string[];
  };
  // Last-minute discount transparency (newer reports)
  baseDailyPrice?: number | null;
  lastMinuteMultiplier?: number | null;
  priceAfterTimeAdjustment?: number | null;
  effectiveDailyPriceRefundable?: number | null;
  effectiveDailyPriceNonRefundable?: number | null;
  flags?: string[]; // e.g. "peak", "low_demand", "missing_data", "interpolated"
}

// ── Transparency Types ──────────────────────────────────────────

export interface TargetSpec {
  title: string;
  location: string;
  propertyType: string;
  accommodates: number | null;
  bedrooms: number | null;
  beds: number | null;
  baths: number | null;
  amenities: string[];
  rating: number | null;
  reviews: number | null;
}

export interface QueryCriteria {
  locationBasis: string;
  searchAdults: number;
  checkin: string;
  checkout: string;
  propertyTypeFilter: string | null;
  tolerances: {
    accommodates: number;
    bedrooms: number;
    beds: number;
    baths: number;
  };
}

export interface CompsSummary {
  collected: number;
  afterFiltering: number;
  usedForPricing: number;
  filterStage: string;
  topSimilarity: number | null;
  avgSimilarity: number | null;
  sampledDays?: number;
  interpolatedDays?: number;
  missingDays?: number;
}

export interface PriceDistribution {
  min: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  max: number | null;
  currency: string;
}

export interface ComparableListing {
  id: string;
  title: string;
  propertyType: string;
  accommodates: number;
  bedrooms: number;
  baths: number;
  nightlyPrice: number;
  currency: string;
  similarity: number; // 0–1
  rating: number | null;
  reviews: number | null;
  location: string | null;
  url: string | null;
}

export interface RecommendedPrice {
  nightly: number | null;
  weekdayEstimate: number | null;
  weekendEstimate: number | null;
  discountApplied: number;
  notes: string;
}

// ── Summary & Report ────────────────────────────────────────────

export interface ReportSummary {
  insightHeadline: string;
  nightlyMin: number;
  nightlyMedian: number;
  nightlyMax: number;
  occupancyPct: number;
  weekdayAvg: number;
  weekendAvg: number;
  estimatedMonthlyRevenue: number;
  weeklyStayAvgNightly: number;
  monthlyStayAvgNightly: number;
  selectedRangeNights?: number;
  selectedRangeAvgNightly?: number;
  stayLengthAverages?: Array<{
    stayLength: number;
    avgNightly: number;
    lengthDiscountPct: number;
  }>;
  // Embedded transparency fields (present in new reports)
  targetSpec?: TargetSpec;
  queryCriteria?: QueryCriteria;
  compsSummary?: CompsSummary;
  priceDistribution?: PriceDistribution;
  recommendedPrice?: RecommendedPrice;
  comparableListings?: ComparableListing[];
}

export interface PricingReport {
  id: string;
  shareId: string;
  createdAt: string;
  status: "queued" | "running" | "ready" | "error";
  coreVersion: string;
  inputAddress: string;
  inputAttributes: ListingInput & {
    inputMode?: InputMode;
    listingUrl?: string | null;
    lastMinuteStrategy?: LastMinuteStrategyPreference;
  };
  inputDateStart: string;
  inputDateEnd: string;
  discountPolicy: DiscountPolicy;
  resultSummary: ReportSummary | null;
  resultCalendar: CalendarDay[] | null;
  errorMessage: string | null;
  workerAttempts?: number;
  // Top-level transparency fields (extracted from resultSummary by API)
  targetSpec?: TargetSpec | null;
  queryCriteria?: QueryCriteria | null;
  compsSummary?: CompsSummary | null;
  priceDistribution?: PriceDistribution | null;
  recommendedPrice?: RecommendedPrice | null;
  comparableListings?: ComparableListing[] | null;
}

// ── Saved Listings ─────────────────────────────────────────────

export const createListingSchema = z.object({
  name: z.string().min(1, "Name is required").max(100),
  inputAddress: z.string().min(5, "Please enter a valid address"),
  inputAttributes: listingInputSchema,
  defaultDiscountPolicy: discountPolicySchema.optional(),
});

export const updateListingSchema = z.object({
  name: z.string().min(1).max(100).optional(),
  inputAddress: z.string().min(5).optional(),
  inputAttributes: listingInputSchema.optional(),
  defaultDiscountPolicy: discountPolicySchema.optional(),
});

export interface SavedListing {
  id: string;
  userId: string;
  name: string;
  inputAddress: string;
  inputAttributes: ListingInput;
  defaultDiscountPolicy: DiscountPolicy | null;
  lastUsedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ListingReport {
  id: string;
  savedListingId: string;
  pricingReportId: string;
  trigger: "manual" | "rerun" | "scheduled";
  createdAt: string;
}

export const rerunListingSchema = z.object({
  dates: dateInputSchema,
  discountPolicy: discountPolicySchema.optional(),
  inputMode: inputModeEnum.optional(),
  listingUrl: z.string().url().optional(),
});

export type CreateListingRequest = z.infer<typeof createListingSchema>;
export type UpdateListingRequest = z.infer<typeof updateListingSchema>;
export type RerunListingRequest = z.infer<typeof rerunListingSchema>;

// ── Market Tracking ─────────────────────────────────────────────

export const trackMarketRequestSchema = z.object({
  email: z.string().email("Please enter a valid email"),
  address: z.string().min(5),
  notifyWeekly: z.boolean().default(false),
  notifyUnderMarket: z.boolean().default(false),
});

export type TrackMarketRequest = z.infer<typeof trackMarketRequestSchema>;
