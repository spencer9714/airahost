# AiraHost Architecture

This document describes the current production architecture in this repo and the near-term target model for listing management and reruns.

## 1) Component Diagram (Text) + Responsibilities

```text
Client Browser (Next.js UI)
    |
    | HTTPS
    v
Vercel Next.js App Router API (/api/*)
    |  (service-role DB access on server)
    v
Supabase Postgres (RLS enabled)
    |  pricing_reports queue + pricing_cache
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
  - Does not query DB tables directly.
- `Vercel API routes (src/app/api/*)`
  - Validates requests with Zod.
  - Applies IP rate limiting for report creation.
  - Computes cache key and performs cache short-circuit.
  - Persists and reads report rows using server-side Supabase client.
- `Supabase Postgres`
  - Source of truth for reports, queue state, tracking prefs, cache.
  - Enforces RLS for non-service-role clients.
  - Provides atomic claim + heartbeat RPC functions.
- `Worker (worker/)`
  - Polls queue, atomically claims jobs, runs heartbeats, writes results.
  - Runs scrape mode (URL/criteria) or deterministic mock fallback.
  - Writes debug/telemetry fields and cache entries.
- `Chrome CDP`
  - Worker-side browser session for Airbnb scraping.
  - Requires local login persistence in CDP profile.

## 2) Data Model (Columns + Indexes)

### A. `pricing_reports` (implemented)

- Purpose: queue + report payload + execution metadata.
- Columns:
  - `id uuid pk`
  - `user_id uuid null` (FK auth.users)
  - `created_at timestamptz not null default now()`
  - `share_id text unique not null`
  - `input_address text not null`
  - `input_attributes jsonb not null`
  - `input_date_start date not null`
  - `input_date_end date not null`
  - `discount_policy jsonb not null`
  - `input_listing_url text null`
  - `status text not null` (`queued|running|ready|error`)
  - `core_version text not null`
  - `result_summary jsonb null`
  - `result_calendar jsonb null`
  - `result_core_debug jsonb null`
  - `error_message text null`
  - `cache_key text null`
  - `worker_claimed_at timestamptz null`
  - `worker_claim_token uuid null`
  - `worker_heartbeat_at timestamptz null`
  - `worker_attempts int not null default 0`
- Indexes:
  - `idx_reports_user_created (user_id, created_at desc)`
  - `idx_reports_status_created (status, created_at)`
  - `idx_reports_heartbeat (worker_heartbeat_at)`
  - `idx_reports_cache_key (cache_key)`

### B. `pricing_cache` (implemented)

- Purpose: report cache by canonical input hash.
- Columns:
  - `id uuid pk`
  - `cache_key text unique not null`
  - `created_at timestamptz not null default now()`
  - `expires_at timestamptz not null`
  - `summary jsonb not null`
  - `calendar jsonb not null`
  - `meta jsonb null`
- Indexes:
  - `idx_cache_expires (expires_at)`
  - implicit unique index on `cache_key`

### C. `saved_listings` (target, not yet in migrations)

- Purpose: per-user saved listing definitions to reuse for future reports.
- Recommended columns:
  - `id uuid pk`
  - `user_id uuid not null` (FK auth.users)
  - `name text not null`
  - `input_address text not null`
  - `input_attributes jsonb not null`
  - `default_discount_policy jsonb null`
  - `last_used_at timestamptz null`
  - `created_at timestamptz not null default now()`
  - `updated_at timestamptz not null default now()`
- Recommended indexes:
  - `(user_id, created_at desc)`
  - `(user_id, last_used_at desc)`

### D. `listing_reports` (target, not yet in migrations)

- Purpose: many-to-many/history relation between saved listings and generated reports.
- Recommended columns:
  - `id uuid pk`
  - `saved_listing_id uuid not null` (FK saved_listings)
  - `pricing_report_id uuid not null` (FK pricing_reports)
  - `trigger text not null` (`manual|rerun|scheduled`)
  - `created_at timestamptz not null default now()`
- Recommended indexes:
  - `(saved_listing_id, created_at desc)`
  - unique `(saved_listing_id, pricing_report_id)`

## 3) RLS Strategy + Service Role Usage

- `Current RLS posture`
  - `pricing_reports`, `market_tracking_preferences`, and `pricing_cache` have RLS enabled.
  - `pricing_cache` intentionally has no public policies.
  - No frontend path performs direct table `select` from browser for report data.
- `Why service role is used`
  - Public share links (`/api/r/{shareId}`) must read reports without exposing broad SELECT policies.
  - Worker must claim/update any queue job regardless of end-user identity.
  - Cache lookup/write is cross-user and must bypass user-scoped policies.
- `Routes currently using server-side admin client`
  - `POST /api/reports`
  - `GET /api/reports/{id}`
  - `GET /api/r/{shareId}`
  - `POST /api/track-market`
- `Security note`
  - `SUPABASE_SERVICE_ROLE_KEY` is server/worker only and must never be sent to client.

## 4) Queue Lifecycle: Claim, Heartbeat, Stale Recovery, Retry

- `Create`
  - API inserts `pricing_reports` as `queued` (or `ready` on cache hit).
- `Claim`
  - Worker calls `claim_pricing_report(worker_token, stale_minutes)`.
  - DB atomically chooses one claimable row using `FOR UPDATE SKIP LOCKED`.
  - Claimable row is:
    - `status='queued'`, or
    - `status='running'` with stale heartbeat.
- `Lease/heartbeat`
  - Worker sets `status='running'`, claim token, timestamps.
  - Background heartbeat updates `worker_heartbeat_at` every configured interval.
- `Complete`
  - Worker writes `status='ready'`, summary/calendar/core version/debug.
  - Update is token-scoped (`eq(worker_claim_token, token)`) for idempotent ownership-safe completion.
- `Fail`
  - Worker writes `status='error'` + user-safe error + debug.
- `Retry policy`
  - `worker_attempts` increments on each claim.
  - Worker hard-stops retries when attempts exceed `WORKER_MAX_ATTEMPTS` (default 3), marks `error`.
- `Stale recovery`
  - Lost worker leases are reclaimed automatically when heartbeat exceeds stale threshold.

## 5) API Contracts Used by Frontend (Reports, Listings, Rerun)

### Implemented contracts

- `POST /api/reports`
  - Request shape (validated): `createReportRequestSchema`
    - `inputMode`, `listing`, `dates`, `discountPolicy`, optional `listingUrl`
  - Response shape:
    - `{ id: string, shareId: string, status: "queued" | "ready" }`
- `GET /api/r/{shareId}`
  - Response shape:
    - `{ id, shareId, status, coreVersion, inputAddress, inputAttributes, inputDateStart, inputDateEnd, discountPolicy, resultSummary, resultCalendar, createdAt, errorMessage, workerAttempts }`
- `GET /api/reports/{id}`
  - Same report response shape as share route.

### Not yet implemented in this repo (target contracts)

- `GET /api/listings`
  - Target response:
    - `{ listings: SavedListing[] }`
- `POST /api/listings`
  - Target request:
    - `{ name, inputAddress, inputAttributes, defaultDiscountPolicy? }`
  - Target response:
    - `{ id, ...savedListing }`
- `POST /api/reports/{id}/rerun` (or `POST /api/listings/{id}/rerun`)
  - Target behavior:
    - create new queued `pricing_reports` row from prior listing/report inputs
  - Target response:
    - `{ id, shareId, status: "queued" | "ready" }`

## 6) Operational Concerns

- `Rate limiting`
  - `POST /api/reports`: in-memory per-IP, 10 req/min per server instance.
- `Hard limits`
  - Date range: 1..180 nights (schema validation).
  - Worker scrape caps via env:
    - `MAX_RUNTIME_SECONDS` (default 180)
    - `MAX_SCROLL_ROUNDS` (default 12)
    - `MAX_CARDS` (default 80)
    - `SCRAPE_RATE_LIMIT_SECONDS` (default 1.0)
- `Queue timing`
  - Poll interval (`WORKER_POLL_SECONDS` default 5).
  - Heartbeat (`WORKER_HEARTBEAT_SECONDS` default 10).
  - Stale lease threshold (`WORKER_STALE_MINUTES` default 15).
- `Caching policy`
  - Canonical input hash key for dedupe.
  - TTL in `pricing_cache.expires_at` (default 24h).
  - API may return immediate `ready` from cache; worker also reads/writes cache.
- `Idempotency and safety`
  - Token-scoped DB updates prevent non-owner worker completion/failure writes.
  - Cache upsert on `cache_key` deduplicates equivalent inputs.
- `Observability`
  - `result_core_debug` stores source, cache flags, errors, worker host/version, timings (`total_ms`), and scrape metrics.
  - Worker logs to console + rotating file.

## 7) Security

- `Where keys live`
  - Browser: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
  - Server/worker only: `SUPABASE_SERVICE_ROLE_KEY`.
- `What never goes to client`
  - Service role key.
  - Worker internals, queue claim tokens, and privileged DB access paths.
- `Practices`
  - Validate all request payloads with Zod.
  - Keep RLS enabled; prefer API-mediated access.
  - Keep public share reads in server routes (no wide-open public SELECT policy).

## 8) Windows Runbook Summary

- `Start Chrome with CDP`
  - Close Chrome, then run:
  - `chrome.exe --remote-debugging-port=9222 --user-data-dir=%USERPROFILE%\chrome-cdp-profile`
- `Run worker manually`
  - `python -m worker.main`
- `Run as service (NSSM)`
  - Install service pointing to Python, working dir repo root, args `-m worker.main`.
  - Configure env vars (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CDP_URL`).
  - Configure stdout/stderr log files and rotation in NSSM.
  - Start/stop/status via `nssm start|stop|status HostRevenueWorker`.
- `Built-in rotating logs`
  - `worker/logs/worker.log` (5 MB x 5 backups) via Python `RotatingFileHandler`.
  - Optional NSSM stdout/stderr logs in `worker/logs/`.

## Acceptance Criteria

- Architecture doc explains all core components and boundaries: Vercel API, Supabase, Worker, Chrome CDP.
- Data model section includes implemented queue/cache tables and clearly marks `saved_listings`/`listing_reports` as target (not yet migrated), with columns and indexes.
- RLS and service-role usage are explicit, including why public share reads stay server-side.
- Queue lifecycle covers claim, heartbeat lease, stale reclaim, and retry-stop behavior.
- API contracts include current frontend-used report endpoints and target listings/rerun contracts.
- Ops section documents limits, timeouts, caching TTL behavior, idempotent update rules, and `result_core_debug` observability fields.
- Security section clearly states key placement and secret-handling rules.
- Windows runbook summary includes CDP Chrome startup, worker execution, NSSM service mode, and log strategy.

