/**
 * Cache key tests for src/lib/cacheKey.ts.
 * Run with: npx tsx src/lib/cacheKey.test.ts
 *
 * Mirrors the Python tests in worker/tests/test_cache_key.py — a few critical
 * cases are duplicated here so the TS and Python implementations stay
 * byte-for-byte compatible. If a case fails in only one language, the cache
 * key contract is broken.
 */
import { computeCacheKey } from "./cacheKey";

let passed = 0;
let failed = 0;

function assert(description: string, actual: unknown, expected: unknown) {
  if (actual === expected) {
    console.log(`  ✓ ${description}`);
    passed++;
  } else {
    console.error(
      `  ✗ ${description}: expected ${String(expected)}, got ${String(actual)}`
    );
    failed++;
  }
}

const ADDR = "Belmont, CA";
const START = "2026-05-01";
const END = "2026-05-08";
const POLICY = {
  weeklyDiscountPct: 0,
  monthlyDiscountPct: 0,
  refundable: true,
  nonRefundableDiscountPct: 0,
  stackingMode: "compound",
  maxTotalDiscountPct: 0,
};

function key(attrs: Record<string, unknown>): string {
  return computeCacheKey(ADDR, attrs, START, END, POLICY, undefined, "criteria");
}

// ── Baseline determinism ──────────────────────────────────────────────────────

console.log("\nbaseline determinism:");

{
  const a = key({ propertyType: "entire_home", bedrooms: 2 });
  const b = key({ propertyType: "entire_home", bedrooms: 2 });
  assert("same input → same key", a === b, true);
}

// ── preferred_comp_room_ids: order matters (primary = index 0) ───────────────

console.log("\npreferred_comp_room_ids — order matters:");

{
  const k1 = key({
    preferredComps: [
      { listingUrl: "https://www.airbnb.com/rooms/111" },
      { listingUrl: "https://www.airbnb.com/rooms/222" },
    ],
  });
  const k2 = key({
    preferredComps: [
      { listingUrl: "https://www.airbnb.com/rooms/222" },
      { listingUrl: "https://www.airbnb.com/rooms/111" },
    ],
  });
  assert("reordering preferredComps changes key", k1 !== k2, true);
}

{
  const k1 = key({
    preferredComps: [{ listingUrl: "https://www.airbnb.com/rooms/111" }],
  });
  const k2 = key({
    preferredComps: [
      { listingUrl: "https://www.airbnb.com/rooms/111" },
      { listingUrl: "https://www.airbnb.com/rooms/222" },
    ],
  });
  assert("appending secondary preferredComp changes key", k1 !== k2, true);
}

{
  const k1 = key({
    preferredComps: [
      { listingUrl: "https://www.airbnb.com/rooms/111", enabled: true },
    ],
  });
  const k2 = key({
    preferredComps: [
      { listingUrl: "https://www.airbnb.com/rooms/111", enabled: true },
      { listingUrl: "https://www.airbnb.com/rooms/222", enabled: false },
    ],
  });
  assert("disabled preferredComp not counted in key", k1 === k2, true);
}

{
  // URL with query params should collapse to same roomId — same key
  const k1 = key({
    preferredComps: [{ listingUrl: "https://www.airbnb.com/rooms/111" }],
  });
  const k2 = key({
    preferredComps: [
      { listingUrl: "https://www.airbnb.com/rooms/111?check_in=2026-05-01" },
    ],
  });
  assert("URL query params collapse to same roomId", k1 === k2, true);
}

// ── excluded_room_ids: order does NOT matter (sorted set) ────────────────────

console.log("\nexcluded_room_ids — sorted set:");

{
  const k1 = key({
    excludedComps: [
      { roomId: "111", excludedAt: "2026-04-01T00:00:00Z" },
      { roomId: "222", excludedAt: "2026-04-02T00:00:00Z" },
    ],
  });
  const k2 = key({
    excludedComps: [
      { roomId: "222", excludedAt: "2026-04-02T00:00:00Z" },
      { roomId: "111", excludedAt: "2026-04-01T00:00:00Z" },
    ],
  });
  assert("excludedComps order does not affect key", k1 === k2, true);
}

{
  const k1 = key({});
  const k2 = key({
    excludedComps: [{ roomId: "111", excludedAt: "2026-04-01T00:00:00Z" }],
  });
  assert("adding excludedComp changes key", k1 !== k2, true);
}

{
  const k1 = key({});
  const k2 = key({ excludedComps: [] });
  assert("empty excludedComps array preserves key vs missing field", k1 === k2, true);
}

// ── Summary ───────────────────────────────────────────────────────────────────

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
