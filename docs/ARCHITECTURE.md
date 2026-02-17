# AiraHost Architecture

This document describes the current production architecture.

## 1) Component Diagram + Responsibilities

```text
Client Browser (Next.js UI)
    |
    | HTTPS
    v
Vercel Next.js App Router API (/api/*)
    |  (service-role DB access on server)
    v
Supabase Postgres (RLS enabled, Auth)
    |  pricing_reports queue + pricing_cache + saved_listings
    v
Local Windows Worker (python -m worker.main)
    |  Playwright connect_over_cdp
    v
Local Chrome with CDP (:9222, logged-in Airbnb session)
```

- `Frontend (src/)`
  - Collects listing, date range, and discount strategy.
  - Creates reports via `POST /api/reports`.
  - Polls status/results via `GET /api/r/{shareId}`.
  - Auth-aware: signed-in users can save listings and access dashboard.
- `Vercel API routes (src/app/api/*)`
  - Validates requests with Zod.
  - Applies IP rate limiting for report creation.
  - Computes cache key and performs cache short-circuit.
  - Persists and reads report rows using server-side Supabase client.
  - Listings API requires authentication (auth-context client).
- `Supabase Postgres`
  - Source of truth for reports, queue state, saved listings, cache.
  - Enforces RLS for non-service-role clients.
  - Provides atomic claim + heartbeat RPC functions.
  - Auth handles email/password sign-in.
- `Worker (worker/)`
  - Polls queue, atomically claims jobs, runs heartbeats, writes results.
  - Day-by-day 1-night scraping for accurate nightly prices.
  - No mock fallback -- failures produce error status with user-facing message.
  - Writes debug/telemetry fields and cache entries.
- `Chrome CDP`
  - Worker-side browser session for Airbnb scraping.
  - Requires local login persistence in CDP profile.

## 2) Data Model (Columns + Indexes)

### A. `pricing_reports`

- Purpose: queue + report payload + execution metadata.
- Key columns: `id`, `user_id`, `share_id`, `input_address`, `input_listing_url`, `input_attributes`, `input_date_start`, `input_date_end`, `discount_policy`, `status`, `core_version`, `result_summary`, `result_calendar`, `result_core_debug`, `error_message`, `cache_key`, `worker_claimed_at`, `worker_claim_token`, `worker_heartbeat_at`, `worker_attempts`.
- Indexes: `(user_id, created_at desc)`, `(status, created_at)`, `(worker_heartbeat_at)`, `(cache_key)`.

### B. `pricing_cache`

- Purpose: report cache by canonical input hash (24h TTL).
- Key columns: `cache_key` (unique), `expires_at`, `summary`, `calendar`, `meta`.

### C. `saved_listings`

- Purpose: per-user saved listing definitions for reuse.
- Key columns: `id`, `user_id` (FK auth.users), `name`, `input_address`, `input_attributes`, `default_discount_policy`, `created_at`, `updated_at`.
- Indexes: `(user_id, created_at desc)`.

### D. `listing_reports`

- Purpose: links saved listings to generated reports.
- Key columns: `id`, `saved_listing_id`, `pricing_report_id`, `trigger` (manual|rerun|scheduled), `created_at`.
- Indexes: `(saved_listing_id, created_at desc)`.

## 3) RLS Strategy + Service Role Usage

- RLS enabled on all tables.
- `saved_listings` and `listing_reports` use user-scoped RLS policies (`auth.uid()`).
- `pricing_cache` has no public policies (service-role only).
- Service role is used for: share link reads, worker queue operations, cache reads/writes.
- `SUPABASE_SERVICE_ROLE_KEY` is server/worker only, never sent to client.

## 4) Queue Lifecycle

1. **Create:** API inserts `pricing_reports` as `queued` (or `ready` on cache hit).
2. **Claim:** Worker calls `claim_pricing_report(worker_token, stale_minutes)` -- atomically selects one row via `FOR UPDATE SKIP LOCKED`.
3. **Process:** Worker sets `status='running'`, starts heartbeat thread, runs scraping pipeline.
4. **Complete:** Worker writes `status='ready'` with results (token-scoped update).
5. **Fail:** Worker writes `status='error'` with user-facing error message.
6. **Retry:** `worker_attempts` increments on each claim; max 3 attempts then permanent error.
7. **Stale recovery:** Jobs with expired heartbeat are reclaimed automatically.

## 5) Scraping Pipeline

1. **Target extraction** (`target_extractor.py`): Navigate to listing URL, extract specs via DOM subtitle, breadcrumbs, meta tags, JSON-LD, body text scan.
2. **Day-by-day queries** (`day_query.py`): 1-night searches (`checkin=day_i, checkout=day_i+1`) for accurate nightly prices. Sampling for ranges >14 nights (~20 queries max).
3. **Comparable filtering** (`comparable_collector.py` + `similarity.py`): Score and rank search results by similarity to target.
4. **Price recommendation** (`pricing_engine.py`): Weighted median from top-K comparables per day.
5. **Interpolation** (`day_query.py`): Linear interpolation between nearest valid price anchors for unsampled days.
6. **Discount application** (`main.py`): Apply weekly/monthly/non-refundable discounts per day.

## 6) API Contracts

### Reports
- `POST /api/reports` -- Create report (validated: inputMode, listing, dates, discountPolicy, optional listingUrl, optional saveToListings).
- `GET /api/r/{shareId}` -- Full report response with status, summary, calendar.
- `GET /api/reports/{id}` -- Same shape, by internal ID.

### Listings (authenticated)
- `GET /api/listings` -- User's saved listings with latest linked report.
- `POST /api/listings` -- Create saved listing.
- `GET /api/listings/{id}` -- Listing detail with linked reports.
- `PATCH /api/listings/{id}` -- Update listing.
- `DELETE /api/listings/{id}` -- Delete listing.
- `POST /api/listings/{id}/rerun` -- Create new queued report from listing.

## 7) Operational Concerns

- **Rate limiting:** 10 req/min per IP on `POST /api/reports`.
- **Hard limits:** Date range 1-180 nights (schema), 60 nights max for worker (ValueError).
- **Worker env:** `MAX_RUNTIME_SECONDS` (180), `SAMPLE_THRESHOLD_NIGHTS` (14), `DAY_QUERY_SCROLL_ROUNDS` (2), `DAY_QUERY_MAX_CARDS` (30).
- **Queue timing:** Poll 5s, heartbeat 10s, stale threshold 15min.
- **Caching:** SHA-256 canonical input hash, 24h TTL, API short-circuits on cache hit.
- **Logs:** `worker/logs/worker.log` (5 MB x 5 backups, rotating).

## 8) Security

- Browser: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
- Server/worker only: `SUPABASE_SERVICE_ROLE_KEY`.
- Zod validation on all request payloads.
- RLS enabled; API-mediated access preferred.
- Middleware protects `/dashboard` route.

## 9) Windows Runbook

- **Start Chrome:** `chrome.exe --remote-debugging-port=9222 --user-data-dir=%USERPROFILE%\chrome-cdp-profile`
- **Run worker:** `python -m worker.main`
- **NSSM service:** Install pointing to Python, working dir repo root, args `-m worker.main`. Configure env vars and log rotation.
- **Service control:** `nssm start|stop|status HostRevenueWorker`
