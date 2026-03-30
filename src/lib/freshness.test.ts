/**
 * Lightweight tests for computeFreshness and resolveMarketCapturedAt.
 * Run with: npx tsx src/lib/freshness.test.ts
 * No test framework dependency required.
 */
import {
  computeFreshness,
  resolveMarketCapturedAt,
  resolveSnapshotMarketCapturedAt,
  FRESHNESS_THRESHOLDS,
} from "./freshness";

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

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

// ── computeFreshness ──────────────────────────────────────────────────────────

console.log("\ncomputeFreshness:");

// null → missing
{
  const r = computeFreshness(null);
  assert("null → status missing",      r.status, "missing");
  assert("null → daysAgo null",        r.daysAgo, null);
}

// 0 days → fresh, label "Today"
{
  const r = computeFreshness(daysAgo(0));
  assert("0d → status fresh",   r.status, "fresh");
  assert("0d → label Today",    r.label,  "Today");
}

// 1 day → fresh, label "Yesterday"
{
  const r = computeFreshness(daysAgo(1));
  assert("1d → status fresh",     r.status, "fresh");
  assert("1d → label Yesterday",  r.label,  "Yesterday");
}

// FRESH_MAX_DAYS → still fresh
{
  const r = computeFreshness(daysAgo(FRESHNESS_THRESHOLDS.FRESH_MAX_DAYS));
  assert(`${FRESHNESS_THRESHOLDS.FRESH_MAX_DAYS}d → status fresh`, r.status, "fresh");
  assert(`${FRESHNESS_THRESHOLDS.FRESH_MAX_DAYS}d → no hint`,      r.hint, null);
}

// FRESH_MAX_DAYS + 1 → aging
{
  const d = FRESHNESS_THRESHOLDS.FRESH_MAX_DAYS + 1;
  const r = computeFreshness(daysAgo(d));
  assert(`${d}d → status aging`,  r.status, "aging");
  assert(`${d}d → has hint`,      r.hint !== null, true);
}

// AGING_MAX_DAYS → still aging
{
  const r = computeFreshness(daysAgo(FRESHNESS_THRESHOLDS.AGING_MAX_DAYS));
  assert(`${FRESHNESS_THRESHOLDS.AGING_MAX_DAYS}d → status aging`, r.status, "aging");
}

// AGING_MAX_DAYS + 1 → stale
{
  const d = FRESHNESS_THRESHOLDS.AGING_MAX_DAYS + 1;
  const r = computeFreshness(daysAgo(d));
  assert(`${d}d → status stale`,  r.status, "stale");
  assert(`${d}d → has hint`,      r.hint !== null, true);
}

// ── resolveMarketCapturedAt ───────────────────────────────────────────────────

console.log("\nresolveMarketCapturedAt:");

const MCT = "2024-01-15T12:00:00Z";
const COMPLETED = "2024-01-15T12:05:00Z";
const LINKED    = "2024-01-14T08:00:00Z";
const CREATED   = "2024-01-14T07:59:00Z";

assert(
  "prefers market_captured_at",
  resolveMarketCapturedAt({ market_captured_at: MCT, completed_at: COMPLETED, created_at: CREATED }, LINKED),
  MCT
);
assert(
  "falls back to completed_at",
  resolveMarketCapturedAt({ market_captured_at: null, completed_at: COMPLETED, created_at: CREATED }, LINKED),
  COMPLETED
);
assert(
  "falls back to linkedAt",
  resolveMarketCapturedAt({ market_captured_at: null, completed_at: null, created_at: CREATED }, LINKED),
  LINKED
);
assert(
  "falls back to created_at last",
  resolveMarketCapturedAt({ market_captured_at: null, completed_at: null, created_at: CREATED }),
  CREATED
);
assert(
  "null report → null",
  resolveMarketCapturedAt(null),
  null
);

// ── resolveSnapshotMarketCapturedAt ───────────────────────────────────────────

console.log("\nresolveSnapshotMarketCapturedAt:");

assert(
  "uses source market_captured_at",
  resolveSnapshotMarketCapturedAt({ market_captured_at: MCT, completed_at: COMPLETED, created_at: CREATED }),
  MCT
);
assert(
  "falls back to source completed_at",
  resolveSnapshotMarketCapturedAt({ market_captured_at: null, completed_at: COMPLETED, created_at: CREATED }),
  COMPLETED
);
assert(
  "falls back to sourceLinkedAt",
  resolveSnapshotMarketCapturedAt({ market_captured_at: null, completed_at: null, created_at: CREATED }, LINKED),
  LINKED
);
assert(
  "falls back to source created_at",
  resolveSnapshotMarketCapturedAt({ market_captured_at: null, completed_at: null, created_at: CREATED }),
  CREATED
);
assert(
  "null source → null",
  resolveSnapshotMarketCapturedAt(null),
  null
);

// ── Summary ───────────────────────────────────────────────────────────────────

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
