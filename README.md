# AiraHost

> AI Revenue Advisor for Airbnb hosts.
> Understand your market. Price smarter. Earn more.

## Quick Start

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

The app runs fully with a mocked pricing engine — no external services required.

## Setup Supabase

For persistent storage and the worker queue, create a Supabase project and run the migrations:

1. Copy `.env.example` to `.env.local` and fill in your Supabase credentials
2. Run `supabase/migrations/001_initial.sql` against your database
3. Run `supabase/migrations/002_worker_queue.sql` to add worker queue + cache tables
4. Run `supabase/migrations/003_saved_listings.sql` to add saved listings + listing history tables
5. In Supabase Auth settings, set Site URL and redirect URL to include `http://localhost:3000/auth/callback`
6. Restart the dev server

## Project Structure

```
src/
  app/               # Next.js App Router pages + API routes
  components/        # Shared UI components
  core/              # Mock pricing engine (used for demo reports)
  lib/               # Schemas, Supabase client, utilities
worker/
  main.py            # Long-running Python worker (polls Supabase queue)
  scraper/           # Playwright-based Airbnb price estimator (CDP)
  core/              # Discount calc, mock fallback, caching, DB helpers
supabase/
  migrations/        # SQL migration files
docs/
  PROJECT_MEMORY.md  # Full project context for future development
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

The frontend creates reports as `queued` jobs. A local Python worker polls the queue, processes them (via Playwright scraping or mock fallback), and writes results back to Supabase. The results page polls until ready.

See `worker/README.md` for setup and 24/7 operation instructions.

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

| Method | Path                | Description                  |
| ------ | ------------------- | ---------------------------- |
| POST   | `/api/reports`      | Create a pricing report      |
| GET    | `/api/reports/{id}` | Get report by ID             |
| GET    | `/api/r/{shareId}`  | Get report by share link     |
| GET    | `/api/listings`     | Get current user's listings  |
| POST   | `/api/listings`     | Create a saved listing       |
| GET    | `/api/listings/{id}`| Get listing + linked reports |
| PATCH  | `/api/listings/{id}`| Update saved listing         |
| DELETE | `/api/listings/{id}`| Delete saved listing         |
| POST   | `/api/listings/{id}/rerun` | Re-run queued analysis |
| POST   | `/api/track-market` | Subscribe to market alerts   |

See `docs/openapi.yaml` for full API specification.

## Tech Stack

- **Frontend:** Next.js 16 (App Router), TypeScript, Tailwind CSS v4, Zod
- **Database:** Supabase (PostgreSQL + RLS)
- **Worker:** Python, Playwright, Supabase client
- **Deployment:** Frontend on Vercel, worker on local machine via NSSM

