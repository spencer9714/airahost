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
      return diff >= 1 && diff <= 180;
    },
    { message: "Date range must be 1–180 days" }
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

// ── Full Report Request ─────────────────────────────────────────

export const createReportRequestSchema = z.object({
  listing: listingInputSchema,
  dates: dateInputSchema,
  discountPolicy: discountPolicySchema,
});

export type CreateReportRequest = z.infer<typeof createReportRequestSchema>;
export type ListingInput = z.infer<typeof listingInputSchema>;
export type DateInput = z.infer<typeof dateInputSchema>;
export type DiscountPolicy = z.infer<typeof discountPolicySchema>;
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
}

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
}

export interface PricingReport {
  id: string;
  shareId: string;
  createdAt: string;
  status: "queued" | "ready" | "error";
  coreVersion: string;
  inputAddress: string;
  inputAttributes: ListingInput;
  inputDateStart: string;
  inputDateEnd: string;
  discountPolicy: DiscountPolicy;
  resultSummary: ReportSummary | null;
  resultCalendar: CalendarDay[] | null;
  errorMessage: string | null;
}

// ── Market Tracking ─────────────────────────────────────────────

export const trackMarketRequestSchema = z.object({
  email: z.string().email("Please enter a valid email"),
  address: z.string().min(5),
  notifyWeekly: z.boolean().default(false),
  notifyUnderMarket: z.boolean().default(false),
});

export type TrackMarketRequest = z.infer<typeof trackMarketRequestSchema>;
