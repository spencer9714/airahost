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
  address: z.string().min(1, "Please enter a city or ZIP code"),
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

export const inputModeEnum = z.enum(["url", "criteria", "criteria-by-city", "criteria-by-zip"]);

// ── Preferred Comparables ────────────────────────────────────────

export const preferredCompSchema = z.object({
  listingUrl: z.string().url("Please enter a valid Airbnb listing URL"),
  name: z.string().max(100).optional(),
  note: z.string().max(500).optional(),
  enabled: z.boolean().default(true),
});

/** A list of up to 10 preferred comparable listings. */
export const preferredCompsSchema = z.array(preferredCompSchema).max(10);

export type PreferredComp = z.infer<typeof preferredCompSchema>;
export type PreferredComps = z.infer<typeof preferredCompsSchema>;

// ── Full Report Request ─────────────────────────────────────────

export const createReportRequestSchema = z.object({
  inputMode: inputModeEnum.default("criteria"),
  listing: listingInputSchema,
  dates: dateInputSchema,
  discountPolicy: discountPolicySchema,
  lastMinuteStrategy: lastMinuteStrategyPreferenceSchema.optional(),
  listingUrl: z.string().url().optional(),
  preferredComps: preferredCompsSchema.optional(),
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
  /** Comps excluded by the similarity floor (score < filterFloor). Present on new reports only. */
  belowSimilarityFloor?: number;
  /** The minimum similarity score required for a comp to enter pricing. */
  filterFloor?: number;
  /** Days where only 1–2 comps survived the similarity floor (low confidence). */
  lowCompConfidenceDays?: number;
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
  /** Nightly price keyed by date ("YYYY-MM-DD"). Present on new reports only. */
  priceByDate?: Record<string, number>;
  currency: string;
  similarity: number; // 0–1
  rating: number | null;
  reviews: number | null;
  location: string | null;
  url: string | null;
  /**
   * Number of nights that the scraped Airbnb card price covered.
   * 1 = "for 1 night" or "/night" — price is already per-night, no change.
   * 2 = "for 2 nights" — original price was a 2-night total; divided by 2.
   * N = "for N nights" — original price was an N-night total; divided by N.
   * Absent on old reports (treat as 1).
   */
  queryNights?: number;
  /**
   * Number of sampled days on which this comp appeared in the pricing pool.
   * Higher = more consistently similar across dates. Present on new reports only.
   */
  usedInPricingDays?: number;
}

export interface RecommendedPrice {
  nightly: number | null;
  weekdayEstimate: number | null;
  weekendEstimate: number | null;
  discountApplied: number;
  notes: string;
}

// ── Benchmark Transparency ───────────────────────────────────────

export interface BenchmarkInfo {
  benchmarkUsed: boolean;
  benchmarkUrl: string;
  /** "search_hit" | "direct_page" | "failed" */
  benchmarkFetchStatus: string;
  benchmarkFetchMethod: string;
  avgBenchmarkPrice: number | null;
  avgMarketPrice: number | null;
  /** Raw market offset vs benchmark, in percent (e.g. +10.5 or -6.2) */
  marketAdjustmentPct: number | null;
  /** Nominal market weight constant (baseline, before confidence/guardrail adjustments) */
  appliedMarketWeight: number;
  /**
   * Average effective market weight actually applied across sampled days.
   * Lower than appliedMarketWeight when benchmark confidence is high and
   * comps are plentiful; higher when confidence is low (market corrects more).
   */
  effectiveMarketWeight?: number | null;
  maxAdjCap: number;
  /**
   * Structural similarity score (0–1) between the benchmark listing and the
   * user's target property attributes (bedrooms, baths, accommodates, type).
   * 1.0 = perfect match; lower = more different.
   * null when user attributes were not available for comparison.
   */
  benchmarkTargetSimilarity?: number | null;
  /**
   * Human-readable classification of the benchmark-to-target similarity.
   * "high_match"          ≥ 0.70 — benchmark is structurally suitable
   * "moderate_mismatch"   0.45–0.70 — some structural differences
   * "strong_mismatch"     < 0.45 — benchmark may be a poor anchor
   * "unknown"             user attributes not provided
   */
  benchmarkMismatchLevel?: "high_match" | "moderate_mismatch" | "strong_mismatch" | "unknown" | null;
  /** Count of sampled days where benchmark vs market gap exceeded 40% */
  outlierDays?: number | null;
  /**
   * True when benchmark and market are in significant conflict:
   * outlier days > 30% of sampled days, or secondary comps signal "divergent".
   */
  conflictDetected?: boolean | null;
  fallbackReason: string | null;
  fetchStats: {
    searchHits: number;
    directFetches: number;
    failed: number;
    totalDays: number;
    /** Phase 2 — confidence breakdown from extract_nightly_price_from_listing_page() */
    highConfidenceDays?: number;
    mediumConfidenceDays?: number;
    lowConfidenceDays?: number;
  };
  /**
   * Phase 2 stub — preferredComps[1:] prices collected from search results.
   * Observational only; does NOT affect pricing formula.
   */
  secondaryComps?: Array<{
    url: string;
    avgPrice: number | null;
    daysFound: number;
    totalDays: number;
  }> | null;
  /**
   * Phase 2 stub — whether secondary comps agree with benchmark or market.
   * "strong"    secondary comps cluster near benchmark price (±20%)
   * "divergent" secondary comps cluster near market price instead
   * "mixed"     no clear consensus
   */
  consensusSignal?: "strong" | "mixed" | "divergent" | null;
}

// ── Worker Progress ──────────────────────────────────────────────

export interface ProgressMeta {
  /** 0–100 percentage complete */
  pct: number;
  /** Stage identifier: "connecting" | "extracting_target" | "fetching_benchmark" | "searching_comps" | "pricing" | "saving_results" | "completed" */
  stage: string;
  /** Human-readable status message for the frontend */
  message: string;
  /** ISO-8601 UTC timestamp when this progress was last written */
  updated_at: string;
  /** Optional estimated seconds remaining */
  est_seconds_remaining?: number | null;
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
  benchmarkInfo?: BenchmarkInfo;
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
    preferredComps?: PreferredComps | null;
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
  benchmarkInfo?: BenchmarkInfo | null;
  /** Worker progress snapshot. Null until the worker first calls update_progress(). */
  progressMeta?: ProgressMeta | null;
  /** ISO-8601 UTC timestamp of last worker heartbeat. Null for cached/old reports. */
  workerHeartbeatAt?: string | null;
}

// ── Saved Listings ─────────────────────────────────────────────

export const dateModeEnum = z.enum(["next_30", "custom"]);
export type DateMode = z.infer<typeof dateModeEnum>;

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
  defaultDateMode: dateModeEnum.optional(),
  defaultStartDate: z.string().nullable().optional(),
  defaultEndDate: z.string().nullable().optional(),
  preferredComps: preferredCompsSchema.nullable().optional(),
});

export interface SavedListing {
  id: string;
  userId: string;
  name: string;
  inputAddress: string;
  inputAttributes: ListingInput & { preferredComps?: PreferredComps | null };
  defaultDiscountPolicy: DiscountPolicy | null;
  defaultDateMode: DateMode;
  defaultStartDate: string | null;
  defaultEndDate: string | null;
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
  preferredComps: preferredCompsSchema.optional(),
});

export const listingAnalysisSchema = z.object({
  listingId: z.string().uuid(),
  listingUrl: z.string().url().optional(),
  dateRange: dateInputSchema,
});

export type CreateListingRequest = z.infer<typeof createListingSchema>;
export type UpdateListingRequest = z.infer<typeof updateListingSchema>;
export type RerunListingRequest = z.infer<typeof rerunListingSchema>;
export type ListingAnalysisRequest = z.infer<typeof listingAnalysisSchema>;

// ── Market Tracking ─────────────────────────────────────────────

export const trackMarketRequestSchema = z.object({
  email: z.string().email("Please enter a valid email"),
  address: z.string().min(5),
  notifyWeekly: z.boolean().default(false),
  notifyUnderMarket: z.boolean().default(false),
});

export type TrackMarketRequest = z.infer<typeof trackMarketRequestSchema>;
