/**
 * Schema validation tests for createReportRequestSchema.
 * Run with: npx tsx src/lib/schemas.test.ts
 * No test framework dependency required.
 */
import { createReportRequestSchema } from "./schemas";

let passed = 0;
let failed = 0;

function assert(description: string, actual: unknown, expected: unknown) {
  if (actual === expected) {
    console.log(`  ✓ ${description}`);
    passed++;
  } else {
    console.error(`  ✗ ${description}: expected ${String(expected)}, got ${String(actual)}`);
    failed++;
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const START = "2026-05-01";
const END   = "2026-05-08";

function baseListing(overrides: Record<string, unknown> = {}) {
  return {
    address: "Belmont, CA",
    propertyType: "entire_home" as const,
    bedrooms: 2,
    bathrooms: 1,
    maxGuests: 4,
    ...overrides,
  };
}

function baseDates() {
  return { startDate: START, endDate: END };
}

function basePolicy() {
  return {
    weeklyDiscountPct: 0,
    monthlyDiscountPct: 0,
    refundable: true,
    nonRefundableDiscountPct: 0,
    stackingMode: "compound" as const,
    maxTotalDiscountPct: 0,
  };
}

function parse(listing: Record<string, unknown>, inputMode = "criteria") {
  return createReportRequestSchema.safeParse({
    inputMode,
    listing,
    dates: baseDates(),
    discountPolicy: basePolicy(),
  });
}

function errorPaths(result: ReturnType<typeof parse>): string[] {
  if (result.success) return [];
  return result.error.issues.map((i) => i.path.join("."));
}

// ── criteria mode: city required ──────────────────────────────────────────────

console.log("\ncriteria mode — city required:");

{
  const r = parse(baseListing({ state: "CA" }));
  assert("city missing → invalid", r.success, false);
  assert("city missing → error on listing.city", errorPaths(r).includes("listing.city"), true);
}

{
  const r = parse(baseListing({ city: "", state: "CA" }));
  assert("city empty string → invalid", r.success, false);
  assert("city empty → error on listing.city", errorPaths(r).includes("listing.city"), true);
}

// ── criteria mode: state required ────────────────────────────────────────────

console.log("\ncriteria mode — state required:");

{
  const r = parse(baseListing({ city: "Belmont" }));
  assert("state missing → invalid", r.success, false);
  assert("state missing → error on listing.state", errorPaths(r).includes("listing.state"), true);
}

{
  const r = parse(baseListing({ city: "Belmont", state: "" }));
  assert("state empty string → invalid", r.success, false);
  assert("state empty → error on listing.state", errorPaths(r).includes("listing.state"), true);
}

// ── criteria mode: city + state → valid ──────────────────────────────────────

console.log("\ncriteria mode — city + state:");

{
  const r = parse(baseListing({ city: "Belmont", state: "CA" }));
  assert("city + state → valid", r.success, true);
}

{
  const r = parse(baseListing({ city: "Belmont", state: "California" }));
  assert("city + full state name → valid", r.success, true);
}

{
  const r = parse(baseListing({ city: "台北市", state: "台灣" }));
  assert("non-US city + state → valid", r.success, true);
}

// ── criteria mode: city + state + postalCode → valid ─────────────────────────

console.log("\ncriteria mode — city + state + postalCode:");

{
  const r = parse(baseListing({ city: "Belmont", state: "CA", postalCode: "94002" }));
  assert("city + state + postalCode → valid", r.success, true);
}

{
  const r = parse(baseListing({ city: "Austin", state: "TX", postalCode: "78701" }));
  assert("Austin TX 78701 → valid", r.success, true);
}

// ── both city and state missing ───────────────────────────────────────────────

console.log("\ncriteria mode — both missing:");

{
  const r = parse(baseListing());
  assert("no city no state → invalid", r.success, false);
  const paths = errorPaths(r);
  assert("errors include listing.city", paths.includes("listing.city"), true);
  assert("errors include listing.state", paths.includes("listing.state"), true);
}

// ── url mode: city + state NOT required ──────────────────────────────────────

console.log("\nurl mode — city/state not required:");

{
  const r = createReportRequestSchema.safeParse({
    inputMode: "url",
    listingUrl: "https://www.airbnb.com/rooms/12345678",
    listing: baseListing(),          // no city, no state
    dates: baseDates(),
    discountPolicy: basePolicy(),
  });
  assert("url mode without city/state → valid", r.success, true);
}

// ── criteria-by-zip: city + state required ───────────────────────────────────

console.log("\ncriteria-by-zip — city + state still required:");

{
  const r = createReportRequestSchema.safeParse({
    inputMode: "criteria-by-zip",
    listing: baseListing({ postalCode: "94002" }),   // no city/state
    dates: baseDates(),
    discountPolicy: basePolicy(),
  });
  assert("criteria-by-zip without city/state → invalid", r.success, false);
}

// ── Summary ───────────────────────────────────────────────────────────────────

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
