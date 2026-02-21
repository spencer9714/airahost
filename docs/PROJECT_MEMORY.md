# PROJECT_MEMORY.md — AiraHost

> This file is the source of truth for any future developer or AI session
> continuing work on this project. Read it fully before making changes.

---

## 1. North Star

**Product:** AiraHost — an AI Revenue Advisor for Airbnb hosts.

**Primary promise:** "Make smarter pricing decisions. Earn more with confidence."

**Target user:** Small Airbnb hosts (1-5 listings) who want data-driven pricing
without complexity.

**Current state:** Full scraping pipeline is live. Reports are queued in Supabase
and processed by a local Python worker via Playwright CDP. Day-by-day 1-night
queries produce accurate nightly prices. No mock fallback — scrape failures
produce user-facing error messages. Last-minute strategy can be configured on
`/tool` (Advanced options), is stored in `pricing_reports.input_attributes`,
and is shown on the report page inside "How your discounts work."

---

## 2. Non-Negotiable API Contract

All endpoints live under `/api`. Zod schemas in `src/lib/schemas.ts` are the
single source of truth for request/response shapes. The OpenAPI spec in
`docs/openapi.yaml` mirrors them.

| Method | Path                       | Purpose                      |
| ------ | -------------------------- | ---------------------------- |
| POST   | `/api/reports`             | Create a pricing report      |
| GET    | `/api/reports/{id}`        | Fetch report by internal ID  |
| GET    | `/api/reports/{id}/save`   | Check dashboard saved status |
| POST   | `/api/reports/{id}/save`   | Save report to dashboard     |
| GET    | `/api/reports/{id}/strategy` | Get strategy preference    |
| POST   | `/api/reports/{id}/strategy`| Save strategy preference    |
| GET    | `/api/r/{shareId}`         | Fetch report by share ID     |
| GET    | `/api/listings`            | Get user's saved listings    |
| POST   | `/api/listings`            | Create a saved listing       |
| GET    | `/api/listings/{id}`       | Get listing + linked reports |
| PATCH  | `/api/listings/{id}`       | Update saved listing         |
| DELETE | `/api/listings/{id}`       | Delete saved listing         |
| POST   | `/api/listings/{id}/rerun` | Re-run queued analysis       |
| POST   | `/api/track-market`        | Subscribe to market alerts   |

**Rules:**
- All request bodies are validated with Zod before processing
- API routes return camelCase JSON (not snake_case)
- Database columns use snake_case
- The share ID is an 8-character alphanumeric string (no ambiguous chars)
- Reports persist to Supabase; the demo report (`/r/demo`) uses `pricingCore.ts`
- Signed-out users can create reports; signed-in users can additionally save listings

---

## 3. Discount Rules Definition

Three stacking modes control how weekly/monthly and non-refundable discounts combine:

| Mode       | Behavior                                                    |
| ---------- | ----------------------------------------------------------- |
| `compound` | `effective = base * (1 - length_discount) * (1 - nr_disc)` |
| `best_only`| Only the largest single discount applies                    |
| `additive` | Discounts add: `effective = base * (1 - (d1 + d2))`        |

All modes respect `maxTotalDiscountPct` as a hard cap.

- Weekly discount: applies when stay >= 7 nights
- Monthly discount: applies when stay >= 28 nights (overrides weekly)
- Non-refundable discount: only applied when `refundable` is false

---

## 4. Current Implementation Summary

### Tech Stack
- **Framework:** Next.js 16 (App Router) with TypeScript
- **Styling:** Tailwind CSS v4
- **Validation:** Zod v4
- **Database:** Supabase (PostgreSQL + RLS + Auth)
- **Worker:** Python 3.14 (Playwright, Supabase client)
- **Font:** Geist Sans

### Pages
| Route                 | Purpose                        | Type    |
| --------------------- | ------------------------------ | ------- |
| `/`                   | Landing page with hero + CTA   | Static  |
| `/tool`               | Multi-step listing input form  | Client  |
| `/r/[shareId]`        | Results page (shareable)       | Client  |
| `/r/demo`             | Seeded demo report             | Client  |
| `/login`              | Email/password auth            | Client  |
| `/dashboard`          | Saved listings + report history| Client  |
| `/profile`            | User profile settings          | Client  |

### Key Files
```
src/
  lib/
    schemas.ts          # Zod schemas -- source of truth for types
    supabase.ts         # Supabase client (browser + admin)
    supabaseServer.ts   # Server-side Supabase client (auth context)
    shareId.ts          # Share ID generator
    cacheKey.ts         # Cache key computation
  core/
    pricingCore.ts      # Deterministic pricing engine (demo page only)
  components/
    Header.tsx          # Site header with auth-aware nav
    Footer.tsx          # Site footer
    Card.tsx            # Rounded card component (responsive padding)
    Button.tsx          # Button with variants
    UserMenu.tsx        # Signed-in user dropdown
    SignOutButton.tsx   # Sign out action
  app/
    globals.css         # Global styles + Tailwind theme
    layout.tsx          # Root layout with header/footer
    page.tsx            # Landing page
    tool/page.tsx       # Multi-step form (mobile-responsive)
    r/[shareId]/page.tsx # Results page (polls for worker results)
    dashboard/page.tsx  # Saved listings + report history
    login/page.tsx      # Auth page
    auth/callback/      # OAuth callback handler
    api/                # Route handlers (queue-based)
    middleware.ts       # Route protection for /dashboard
worker/
  main.py              # Long-running worker (polls Supabase queue)
  __main__.py          # Entrypoint for python -m worker
  requirements.txt     # Python dependencies
  .env.example         # Worker env var docs
  core/
    db.py              # Supabase client helpers (claim, heartbeat, complete)
    cache.py           # Cache key computation + read/write
    discounts.py       # Discount logic (mirrors pricingCore.ts)
    pricing_engine.py  # Weighted-median price recommendation
    similarity.py      # Listing similarity scoring
  scraper/
    target_extractor.py      # Extract listing specs from Airbnb pages
    comparable_collector.py  # Collect comparable listings from search
    day_query.py             # Day-by-day 1-night price queries
    price_estimator.py       # Orchestrates scraping pipeline
```

---

## 5. Database Schema

### `pricing_reports`
| Column               | Type        | Notes                                  |
| -------------------- | ----------- | -------------------------------------- |
| id                   | uuid PK     | Auto-generated                         |
| user_id              | uuid FK     | Nullable, refs auth.users              |
| created_at           | timestamptz | Default now()                          |
| share_id             | text UNIQUE | 8-char alphanumeric                    |
| input_address        | text        | Listing address                        |
| input_listing_url    | text        | Airbnb listing URL (nullable)          |
| input_attributes     | jsonb       | Full ListingInput                      |
| input_date_start     | date        |                                        |
| input_date_end       | date        |                                        |
| discount_policy      | jsonb       | Full DiscountPolicy                    |
| status               | text        | queued | running | ready | error       |
| core_version         | text        | e.g. "1.0.0+scrape"                   |
| result_summary       | jsonb       | ReportSummary                          |
| result_calendar      | jsonb       | CalendarDay[]                          |
| result_core_debug    | jsonb       | Debug info from worker (nullable)      |
| error_message        | text        | Nullable                               |
| cache_key            | text        | SHA-256 of canonical input (nullable)  |
| worker_claimed_at    | timestamptz | When a worker claimed this job         |
| worker_claim_token   | uuid        | Unique token for claim ownership       |
| worker_heartbeat_at  | timestamptz | Last heartbeat from worker             |
| worker_attempts      | int         | Number of processing attempts          |

### `pricing_cache`
| Column      | Type        | Notes                              |
| ----------- | ----------- | ---------------------------------- |
| cache_key   | text PK     | SHA-256 hash of canonical input    |
| created_at  | timestamptz | Default now()                      |
| expires_at  | timestamptz | TTL (default 24h)                  |
| summary     | jsonb       | Cached ReportSummary               |
| calendar    | jsonb       | Cached CalendarDay[]               |
| core_debug  | jsonb       | Cached debug info                  |

### `saved_listings`
| Column               | Type        | Notes                              |
| -------------------- | ----------- | ---------------------------------- |
| id                   | uuid PK     | Auto-generated                     |
| user_id              | uuid FK     | refs auth.users, cascade delete    |
| name                 | text        | User-defined listing name          |
| input_address        | text        | Address                            |
| input_attributes     | jsonb       | ListingInput                       |
| default_discount_policy | jsonb    | Default policy (nullable)          |
| created_at           | timestamptz | Default now()                      |
| updated_at           | timestamptz | Default now()                      |

### `listing_reports`
| Column               | Type        | Notes                              |
| -------------------- | ----------- | ---------------------------------- |
| id                   | uuid PK     | Auto-generated                     |
| saved_listing_id     | uuid FK     | refs saved_listings, cascade       |
| pricing_report_id    | uuid FK     | refs pricing_reports, cascade      |
| trigger              | text        | manual | rerun | scheduled         |
| created_at           | timestamptz | Default now()                      |

### `market_tracking_preferences`
| Column              | Type        | Notes                         |
| ------------------- | ----------- | ----------------------------- |
| id                  | uuid PK     | Auto-generated                |
| user_id             | uuid FK     | Nullable                      |
| email               | text        | Nullable                      |
| address             | text        |                               |
| notify_weekly       | boolean     | Default false                 |
| notify_under_market | boolean     | Default false                 |
| created_at          | timestamptz | Default now()                 |

### Postgres Functions
- `claim_pricing_report(worker_token, stale_minutes)` -- atomically claims
  the next queued job using `FOR UPDATE SKIP LOCKED`; also reclaims stale
  running jobs whose heartbeat expired
- `heartbeat_pricing_report(report_id, worker_token)` -- updates heartbeat
  timestamp; only succeeds if the caller owns the claim token

Migration files:
- `supabase/migrations/001_initial.sql` -- base tables
- `supabase/migrations/002_worker_queue.sql` -- worker columns, cache table, functions
- `supabase/migrations/003_saved_listings.sql` -- saved listings, listing_reports, RLS
- `supabase/migrations/004_user_pricing_preferences.sql` -- per-user strategy preferences

---

## 6. Worker Pipeline

Reports flow through a queue-based pipeline:

```
POST /api/reports -> insert row (status=queued) -> return { id, shareId, status }
                                                         |
Python worker polls Supabase <---------------------------+
  claim_pricing_report() (atomic, skip locked)
  -> status=running, heartbeat thread starts
  -> Step 1: Extract target listing specs (DOM, JSON-LD, meta tags, breadcrumbs)
  -> Step 2: Day-by-day 1-night search queries (accurate nightly prices)
  -> Step 3: Filter comparables by similarity, recommend weighted-median price
  -> Step 4: Interpolate unsampled days (for ranges >14 nights)
  -> Step 5: Apply discount policy per day
  -> complete_job() -> status=ready, results written
  -> fail_job() on error -> status=error, user-facing error message

GET /r/{shareId} <- frontend polls every 2s until ready/error
```

**Key design decisions:**
- Worker runs locally (not serverless) for Playwright browser access
- Atomic claim via `FOR UPDATE SKIP LOCKED` prevents duplicate processing
- Heartbeat thread keeps lease alive; stale jobs reclaimed after 15min
- Cache layer: identical inputs hit `pricing_cache` table (24h TTL) and skip worker
- Rate limiting: IP-based in-memory throttle on POST /api/reports (10 req/min)
- No mock fallback: scrape failures produce error status with user-facing message
- Demo report (`/r/demo`) uses `src/core/pricingCore.ts` (deterministic, no worker)

**Day-by-day querying:** Airbnb search cards display total trip prices for
multi-night stays. We query 1-night at a time (checkin=day_i, checkout=day_i+1)
so cards show actual nightly prices. For ranges >14 nights, we sample ~20
evenly-spaced dates and interpolate the rest.

---

## 7. Security + RLS Notes

- RLS is enabled on all tables
- Authenticated users can only read/write their own rows
- Public access to shared reports uses the **service role key** server-side
  (the `/api/r/{shareId}` route handler), not a public RLS policy
- The Python worker also uses the **service role key** to claim/update jobs
- Anon key is used client-side, service key is server-only
- No secrets in client-side code
- `.env.example` documents required variables (both root and `worker/.env.example`)
- Signed-out users can create reports (user_id = null)
- Signed-in users can additionally save listings and view dashboard
- Middleware protects `/dashboard` route (redirects to `/login`)

---

## 8. Known Limitations

- **Scraping depends on local Chrome:** Worker needs a running Chrome instance
  with remote debugging enabled; no fallback if CDP is unavailable
- **No email sending:** Market tracking saves preferences but doesn't send emails
- **No real geocoding:** Address is stored as-is, no validation or normalization
- **No error boundary:** Client errors aren't caught gracefully
- **Single worker:** No horizontal scaling; one worker instance processes all jobs
- **Anti-bot risk:** Frequent Airbnb scraping may trigger CAPTCHAs

---

## 9. Remaining Work

### Observability + Guardrails
- [ ] Standardize debug payload shape across all scrape modes
- [ ] Add health check endpoint for worker/CDP readiness
- [ ] Surface "no comps found" reason in debug and user-safe message

### Testing
- [ ] Unit tests for `daterange_nights`, `compute_sample_dates`, `interpolate_missing_days`
- [ ] Integration test: submit 7-night URL report, verify per-day price variation
- [ ] E2E test: full flow from `/tool` to `/r/{shareId}`

### Data Quality
- [ ] Improve scraper reliability (handle CAPTCHAs, anti-bot)
- [ ] Geocoding + address normalization
- [ ] Comp-set quality scoring

### Retention
- [ ] Email service integration (Resend / SendGrid)
- [ ] Weekly market digest emails
- [ ] Under-market price alerts

---

## 10. Failure Modes & Fixes

- **CDP not reachable:** Worker marks job as error with "Service is busy" message. Start Chrome with `--remote-debugging-port=9222`.
- **Stale jobs stuck in running:** Heartbeat + stale window reclaims via `claim_pricing_report`.
- **No comps found:** Day marked as `missing_data`, interpolated from neighbors. If all days fail, job marked as error.
- **Supabase RLS errors:** Validate policies; route uses auth-context client for user tables.
- **Vercel env missing:** Set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` in Vercel dashboard.

---
