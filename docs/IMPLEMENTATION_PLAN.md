# Implementation Plan

Based on `docs/ARCHITECTURE.md`. This tracks what's been implemented and what remains.

## Completed

### Step 1) Queue Baseline
- [x] Queue + worker behavior stable.
- [x] Atomic claim via RPC `claim_pricing_report`.
- [x] Heartbeat via RPC `heartbeat_pricing_report`.
- [x] Token-scoped `complete_job`/`fail_job`.
- [x] API-side cache short-circuit + worker-side cache read/write.

### Step 2) Auth + Saved Listings Data Model
- [x] `saved_listings` and `listing_reports` tables created (`003_saved_listings.sql`).
- [x] RLS policies for user-scoped data.
- [x] Supabase Auth integration (email/password).

### Step 3) Listings API + Rerun API
- [x] `GET /api/listings` -- user-scoped list.
- [x] `POST /api/listings` -- create saved listing.
- [x] `GET /api/listings/{id}` -- listing detail with reports.
- [x] `PATCH /api/listings/{id}` -- update listing.
- [x] `DELETE /api/listings/{id}` -- delete listing.
- [x] `POST /api/listings/{id}/rerun` -- create fresh queued report.

### Step 4) Dashboard + Auth UI
- [x] `/login` page with email/password.
- [x] `/dashboard` page with saved listings + report history.
- [x] Header shows auth state (Login / User menu + Sign out).
- [x] Middleware protects `/dashboard`.
- [x] Tool page offers "Save to my dashboard" for signed-in users.

### Step 5) Day-by-Day Pricing Pipeline
- [x] `day_query.py` -- 1-night queries for accurate nightly prices.
- [x] `target_extractor.py` -- multi-strategy location extraction (DOM, JSON-LD, breadcrumbs, meta).
- [x] `comparable_collector.py` -- scroll and collect search cards.
- [x] `similarity.py` + `pricing_engine.py` -- filter and recommend.
- [x] Sampling for ranges >14 nights, interpolation for unsampled days.
- [x] Mock fallback fully removed -- failures produce user-facing errors.

### Step 6) Mobile Responsiveness
- [x] Tool page: date picker stacks vertically on mobile.
- [x] Card component: responsive padding (`p-4 sm:p-6`).
- [x] Input fields: `min-w-0` and `box-sizing` to prevent overflow.
- [x] Stepper component: compact sizing on mobile.
- [x] Mode toggle buttons: smaller text on mobile.

### Step 7) Code Cleanup
- [x] Removed `worker/core/mock_core.py` (dead code).
- [x] Removed `backend/` directory (replaced by `worker/`).
- [x] Removed `src/core/pythonAdapter.ts` (old backend adapter).
- [x] Removed outdated docs (`pricing-api.md`, `frontend-adapter.md`).
- [x] Updated all documentation to reflect current state.

---

## Remaining Work

### Observability + Guardrails
- [ ] Standardize debug payload shape across all scrape modes.
- [ ] Add health check endpoint for worker/CDP readiness.
- [ ] Surface "no comps found" reason in debug and user-safe message.

### Testing
- [ ] Unit tests for `daterange_nights`, `compute_sample_dates`, `interpolate_missing_days`.
- [ ] Integration test: submit 7-night URL report, verify per-day price variation.
- [ ] E2E test: full flow from `/tool` to `/r/{shareId}`.

### Data Quality
- [ ] Improve scraper reliability (handle CAPTCHAs, anti-bot).
- [ ] Geocoding + address normalization.
- [ ] Comp-set quality scoring.

### Retention
- [ ] Email service integration (Resend / SendGrid).
- [ ] Weekly market digest emails.
- [ ] Under-market price alerts.

## Failure Modes & Fixes

- **CDP not reachable:** Worker marks job as error with "Service is busy" message. Start Chrome with `--remote-debugging-port=9222`.
- **Stale jobs stuck in running:** Heartbeat + stale window reclaims via `claim_pricing_report`.
- **No comps found:** Day marked as `missing_data`, interpolated from neighbors. If all days fail, job marked as error.
- **Supabase RLS errors:** Validate policies; route uses auth-context client for user tables.
- **Vercel env missing:** Set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` in Vercel dashboard.
- **`top_slice` NameError:** Fixed -- variable now defined before property type extraction in `target_extractor.py`.
