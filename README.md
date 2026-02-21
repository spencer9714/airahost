# AiraHost

> AI Revenue Advisor for Airbnb hosts.
> Understand your market. Price smarter. Earn more.

## Quick Start

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

The demo report (`/r/demo`) uses a deterministic pricing engine. Real reports require a running Python worker with Chrome CDP.

## Setup Supabase

1. Copy `.env.example` to `.env.local` and fill in your Supabase credentials
2. Run `supabase/migrations/001_initial.sql` against your database
3. Run `supabase/migrations/002_worker_queue.sql` to add worker queue + cache tables
4. Run `supabase/migrations/003_saved_listings.sql` to add saved listings + listing history tables
5. In Supabase Auth settings, set Site URL and redirect URL to include `http://localhost:3000/auth/callback`
6. Restart the dev server

## Setup Worker

The worker scrapes Airbnb via Playwright CDP for real pricing data.

1. Start Chrome with remote debugging:
   ````powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=$env:USERPROFILE\chrome-cdp-profile
   ```
2. Copy `worker/.env.example` to `worker/.env` and fill in credentials
3. Install Python dependencies: `pip install -r worker/requirements.txt`
4. Run: `python -m worker.main`

See `worker/README.md` for 24/7 operation via NSSM.

## Vercel Deployment

Add these environment variables in the Vercel dashboard:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`

## Project Structure

```
src/
  app/               # Next.js App Router pages + API routes
  components/        # Shared UI components
  core/              # Deterministic pricing engine (demo reports only)
  lib/               # Schemas, Supabase client, utilities
worker/
  main.py            # Long-running Python worker (polls Supabase queue)
  scraper/           # Playwright CDP-based Airbnb scraper
    target_extractor.py   # Extract listing specs from Airbnb pages
    comparable_collector.py # Collect comparable listings from search
    day_query.py          # Day-by-day 1-night price queries
    price_estimator.py    # Orchestrates scraping pipeline
  core/              # Discount calc, caching, DB helpers, pricing engine
supabase/
  migrations/        # SQL migration files (001, 002, 003)
docs/
  PROJECT_MEMORY.md  # Full project context for development
  openapi.yaml       # API specification
```

## Architecture

```
Frontend (Vercel)                  Local Worker (Python)
┌──────────────┐                   ┌──────────────────────┐
│ POST /api/   │  queued job       │  python -m worker    │
│   reports    │ ──────────────►  │                      │
│              │  pricing_reports  │  poll → claim → run  │
│ GET /api/r/  │ ◄──────────────  │  → write results     │
│   {shareId}  │  read results     │                      │
└──────────────┘                   └──────────────────────┘
        │                                   │
        └────────── Supabase DB ────────────┘
```

The frontend creates reports as `queued` jobs. A local Python worker polls the queue, scrapes Airbnb via Chrome CDP (day-by-day 1-night queries for accurate nightly prices), and writes results back to Supabase. If scraping fails, the job is marked as `error` with a user-facing message.

## Scraping Pipeline

1. **Target extraction** -- Navigate to the listing URL, extract specs (location, bedrooms, amenities, etc.) from DOM, JSON-LD, meta tags, and breadcrumbs
2. **Day-by-day queries** -- For each night in the date range, query Airbnb search with 1-night stays to get accurate nightly prices (not inflated total-trip prices)
3. **Comparable filtering** -- Filter search results by similarity to the target listing (property type, capacity, amenities)
4. **Price recommendation** -- Weighted median from top-K similar comparables per day
5. **Interpolation** -- For sampled ranges (>14 nights), interpolate unqueried days from nearest anchors
6. **Discount application** -- Apply weekly/monthly/non-refundable discounts per the user's policy

## Pages

| Route          | Description                       |
| -------------- | --------------------------------- |
| `/`            | Landing page                      |
| `/tool`        | Multi-step listing analysis form  |
| `/r/{shareId}` | Shareable revenue report          |
| `/r/demo`      | Seeded demo report                |
| `/login`       | Email/password auth               |
| `/dashboard`   | Saved listings + report history   |

## API

| Method | Path                         | Description                  |
| ------ | ---------------------------- | ---------------------------- |
| POST   | `/api/reports`               | Create a pricing report      |
| GET    | `/api/reports/{id}`          | Get report by ID             |
| GET    | `/api/r/{shareId}`           | Get report by share link     |
| GET    | `/api/listings`              | Get current user's listings  |
| POST   | `/api/listings`              | Create a saved listing       |
| GET    | `/api/listings/{id}`         | Get listing + linked reports |
| PATCH  | `/api/listings/{id}`         | Update saved listing         |
| DELETE | `/api/listings/{id}`         | Delete saved listing         |
| POST   | `/api/listings/{id}/rerun`   | Re-run queued analysis       |
| POST   | `/api/track-market`          | Subscribe to market alerts   |

See `docs/openapi.yaml` for full API specification.

## Tech Stack

- **Frontend:** Next.js 16 (App Router), TypeScript, Tailwind CSS v4, Zod
- **Database:** Supabase (PostgreSQL + RLS + Auth)
- **Worker:** Python 3.14, Playwright CDP, Supabase client
- **Deployment:** Frontend on Vercel, worker on local Windows machine via NSSM
