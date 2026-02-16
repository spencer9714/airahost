# Implementation Plan

Based on `docs/ARCHITECTURE.md`. This plan is ordered, minimal, and executable.

## Step 1) Lock Current Queue Baseline

- [ ] Confirm current queue + worker behavior is stable before adding new features.
- Files to inspect:
  - `supabase/migrations/001_initial.sql`
  - `supabase/migrations/002_worker_queue.sql`
  - `worker/main.py`
  - `worker/core/db.py`
  - `worker/core/cache.py`
  - `src/app/api/reports/route.ts`
- Key code behaviors:
  - Atomic claim via RPC `claim_pricing_report`.
  - Heartbeat via RPC `heartbeat_pricing_report`.
  - Token-scoped `complete_job`/`fail_job`.
  - API-side cache short-circuit + worker-side cache read/write.
- Quick test:
  - Start worker, create one report from `/tool`, verify lifecycle: `queued -> running -> ready`.
  - Validate `result_core_debug` has `worker_version`, `total_ms`, `cache_hit`.

## Step 2) Add Auth + Saved Listings Data Model

- [ ] Add schema for `saved_listings` and `listing_reports`.
- [ ] Add strict RLS policies for user-scoped data.
- DB migration ordering:
  1. Existing: `001_initial.sql`
  2. Existing: `002_worker_queue.sql`
  3. New: `003_saved_listings.sql` (create tables + indexes + RLS policies)
- Files to create/edit:
  - `supabase/migrations/003_saved_listings.sql` (new)
  - `src/lib/schemas.ts` (types for SavedListing/ListingReport)
  - `docs/openapi.yaml` (new endpoint schemas)
- Key code behaviors:
  - `saved_listings.user_id` required and policy-bound to `auth.uid()`.
  - `listing_reports` links saved listing to generated report + trigger type.
  - Indexes for list pages (`user_id, created_at desc`).
- Quick test:
  - In Supabase SQL editor: insert/select as authenticated user works.
  - Cross-user select denied by RLS.

## Step 3) Build Listings API + Rerun API

- [ ] Add authenticated routes for CRUD-lite listings and rerun.
- [ ] Keep report reads server-mediated for share links.
- Files to create/edit:
  - `src/app/api/listings/route.ts` (new: GET, POST)
  - `src/app/api/listings/[id]/route.ts` (new: GET, PATCH/DELETE optional)
  - `src/app/api/listings/[id]/rerun/route.ts` (new: POST)
  - `src/lib/supabase.ts` (ensure auth-context client + admin client separation)
  - `src/lib/schemas.ts`
- Key code behaviors:
  - `GET /api/listings`: user-scoped list sorted by latest.
  - `POST /api/listings`: create saved listing.
  - `POST /api/listings/{id}/rerun`: create fresh `pricing_reports` row, status `queued|ready`, link via `listing_reports`.
  - Reuse same cache-key logic as `/api/reports`.
- Quick test:
  - Create saved listing, rerun it, confirm new report row + listing_reports link.
  - Unauthorized request returns `401/403`.

## Step 4) Dashboard: Login + Saved Listings + Report History

- [ ] Add auth gate and dashboard data loading.
- [ ] Show listing cards, report history, and rerun action.
- Files to create/edit:
  - `src/app/dashboard/page.tsx`
  - `src/components/Header.tsx` (auth state / sign in-out links)
  - `src/lib/supabase.ts` (browser auth client usage)
  - Optional new components:
    - `src/components/dashboard/SavedListings.tsx`
    - `src/components/dashboard/ReportHistory.tsx`
- Key code behaviors:
  - Unauthenticated users redirected to sign-in flow.
  - Dashboard loads user listings + latest linked reports.
  - Rerun button calls `/api/listings/{id}/rerun` then routes to `/r/{shareId}`.
- Quick test:
  - Sign in, create listing, rerun, and see new history entry without page reload issues.

## Step 5) Observability + Guardrails

- [ ] Standardize debug payload shape and failure messages.
- [ ] Add health checks for environment and CDP readiness.
- Files to edit:
  - `worker/main.py`
  - `worker/scraper/price_estimator.py`
  - `src/app/api/reports/route.ts`
  - `worker/README.md`
- Key code behaviors:
  - Always include `source`, `worker_host`, `worker_version`, `total_ms`, `cache_key`.
  - Early fail with clear error if required env vars missing.
  - Surface “no comps found” reason in debug and user-safe message.
- Quick test:
  - Trigger scrape with CDP off and confirm graceful fallback + actionable debug.

## Step 6) Hardening + Regression Tests

- [ ] Add lightweight automated checks around queue, cache, and rerun.
- Files to create/edit:
  - `worker/tests/test_queue_contract.py` (new, optional)
  - `src/app/api/**/__tests__/*` (new, optional)
  - `docs/ARCHITECTURE.md` and this file for final updates
- Key code behaviors:
  - No duplicate completion for same claim token.
  - Cache hit returns deterministic ready responses.
  - Rerun creates a new report ID every time.
- Quick test:
  - Run lint + targeted API tests + one end-to-end manual flow.

## Definition of Done

### Queue claim function
- [ ] `claim_pricing_report` uses `FOR UPDATE SKIP LOCKED`.
- [ ] Reclaims stale running jobs.
- [ ] Increments `worker_attempts`.
- [ ] Returns exactly one updated row per claim call.

### Worker loop
- [ ] Polls continuously with backoff.
- [ ] Heartbeat thread active while processing.
- [ ] `complete_job` and `fail_job` are token-scoped.
- [ ] Max-attempt policy enforced (`WORKER_MAX_ATTEMPTS`).

### Caching
- [ ] Canonical cache key consistent between API and worker.
- [ ] API immediate-ready on valid cache hit.
- [ ] Worker writes cache on successful completion.
- [ ] Expired entries are ignored.

### Dashboard login + saved listings + report history + rerun
- [ ] Authenticated dashboard view only.
- [ ] User can create/view saved listings.
- [ ] User can view history linked to each listing.
- [ ] User can rerun listing and receive a new queued/ready report.
- [ ] Cross-user access blocked by RLS.

## Failure Modes & Fixes

- `CDP not reachable`
  - Symptom: scrape errors/timeouts in `worker.log`.
  - Fix: start Chrome with `--remote-debugging-port=9222`, verify `http://127.0.0.1:9222/json`, keep fallback to mock enabled.

- `Stale jobs stuck in running`
  - Symptom: reports never finish after worker crash.
  - Fix: ensure heartbeat interval + stale window configured; reclaim via `claim_pricing_report`; manual SQL reset only as emergency.

- `No comps found`
  - Symptom: scraper returns empty comp set.
  - Fix: criteria-search fallback then mock fallback; store reason in `result_core_debug` and user-safe headline.

- `Supabase RLS errors`
  - Symptom: `permission denied` on listings/history.
  - Fix: validate policies in `003_saved_listings.sql`; verify route uses auth-context client for user tables and service role only where required.

- `Vercel env missing`
  - Symptom: API returns `503 Database not configured`.
  - Fix: set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` in Vercel project env; redeploy.

