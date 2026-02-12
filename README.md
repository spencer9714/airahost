# Host Revenue Coach

> AI Revenue Advisor for Airbnb hosts.
> Understand your market. Price smarter. Earn more.

## Quick Start

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

The app runs fully with a mocked pricing engine — no external services required.

## Setup Supabase (Optional)

For persistent storage, create a Supabase project and run the migration:

1. Copy `.env.example` to `.env.local` and fill in your Supabase credentials
2. Run `supabase/migrations/001_initial.sql` against your database
3. Restart the dev server

## Project Structure

```
src/
  app/               # Next.js App Router pages + API routes
  components/        # Shared UI components
  core/              # Pricing engine (mock + Python adapter placeholder)
  lib/               # Schemas, Supabase client, utilities
supabase/
  migrations/        # SQL migration files
docs/
  PROJECT_MEMORY.md  # Full project context for future development
  openapi.yaml       # API specification
```

## Pages

| Route          | Description                       |
| -------------- | --------------------------------- |
| `/`            | Landing page                      |
| `/tool`        | Multi-step listing analysis form  |
| `/r/{shareId}` | Shareable revenue report          |
| `/r/demo`      | Seeded demo report                |
| `/dashboard`   | Past reports (placeholder)        |

## API

| Method | Path                | Description                  |
| ------ | ------------------- | ---------------------------- |
| POST   | `/api/reports`      | Generate a pricing report    |
| GET    | `/api/reports/{id}` | Get report by ID             |
| GET    | `/api/r/{shareId}`  | Get report by share link     |
| POST   | `/api/track-market` | Subscribe to market alerts   |

See `docs/openapi.yaml` for full API specification.

## Tech Stack

- Next.js 16 (App Router)
- TypeScript
- Tailwind CSS v4
- Zod (validation)
- Supabase (database + auth)

## Future: Python Pricing Service

The mock pricing core (`src/core/pricingCore.ts`) will be replaced by a Python
service. See `src/core/pythonAdapter.ts` and `docs/PROJECT_MEMORY.md` section 6
for the integration plan.
