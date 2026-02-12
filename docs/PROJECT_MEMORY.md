# PROJECT_MEMORY.md — Host Revenue Coach

> This file is the source of truth for any future developer or AI session
> continuing work on this project. Read it fully before making changes.

---

## 1. North Star

**Product:** Host Revenue Coach — an AI Revenue Advisor for Airbnb hosts.

**Primary promise:** "Make smarter pricing decisions. Earn more with confidence."

**Target user:** Small Airbnb hosts (1–5 listings) who want data-driven pricing
without complexity.

**Phase A goal:** Traffic + email capture + recurring engagement via market tracking.
No real pricing data — uses a deterministic mock engine.

---

## 2. Non-Negotiable API Contract

All endpoints live under `/api`. Zod schemas in `src/lib/schemas.ts` are the
single source of truth for request/response shapes. The OpenAPI spec in
`docs/openapi.yaml` mirrors them.

| Method | Path                  | Purpose                      |
| ------ | --------------------- | ---------------------------- |
| POST   | `/api/reports`        | Create a pricing report      |
| GET    | `/api/reports/{id}`   | Fetch report by internal ID  |
| GET    | `/api/r/{shareId}`    | Fetch report by share ID     |
| POST   | `/api/track-market`   | Subscribe to market alerts   |

**Rules:**
- All request bodies are validated with Zod before processing
- API routes return camelCase JSON (not snake_case)
- Database columns use snake_case
- The share ID is an 8-character alphanumeric string (no ambiguous chars)
- Reports persist to Supabase when configured; the API works without it

---

## 3. Discount Rules Definition

Three stacking modes control how weekly/monthly and non-refundable discounts combine:

| Mode       | Behavior                                                    |
| ---------- | ----------------------------------------------------------- |
| `compound` | `effective = base × (1 - length_discount) × (1 - nr_disc)` |
| `best_only`| Only the largest single discount applies                    |
| `additive` | Discounts add: `effective = base × (1 - (d1 + d2))`        |

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
- **Database:** Supabase (PostgreSQL + RLS)
- **Font:** Geist Sans

### Pages
| Route                 | Purpose                        | Type    |
| --------------------- | ------------------------------ | ------- |
| `/`                   | Landing page with hero + CTA   | Static  |
| `/tool`               | Multi-step listing input form  | Client  |
| `/r/[shareId]`        | Results page (shareable)       | Client  |
| `/r/demo`             | Seeded demo report             | Client  |
| `/dashboard`          | Placeholder for past reports   | Client  |

### Key Files
```
src/
  lib/
    schemas.ts          # Zod schemas — source of truth for types
    supabase.ts         # Supabase client (browser + admin)
    shareId.ts          # Share ID generator
  core/
    pricingCore.ts      # Mock pricing engine (deterministic)
    pythonAdapter.ts    # Placeholder for Python service integration
  components/
    Header.tsx          # Site header with nav
    Footer.tsx          # Site footer
    Card.tsx            # Rounded card component
    Button.tsx          # Button with variants
  app/
    globals.css         # Global styles + Tailwind theme
    layout.tsx          # Root layout with header/footer
    page.tsx            # Landing page
    tool/page.tsx       # Multi-step form
    r/[shareId]/page.tsx # Results page
    dashboard/page.tsx  # Dashboard placeholder
    api/                # Route handlers
```

---

## 5. Database Schema

### `pricing_reports`
| Column            | Type        | Notes                           |
| ----------------- | ----------- | ------------------------------- |
| id                | uuid PK     | Auto-generated                  |
| user_id           | uuid FK     | Nullable, refs auth.users       |
| created_at        | timestamptz | Default now()                   |
| share_id          | text UNIQUE | 8-char alphanumeric             |
| input_address     | text        | Listing address                 |
| input_attributes  | jsonb       | Full ListingInput               |
| input_date_start  | date        |                                 |
| input_date_end    | date        |                                 |
| discount_policy   | jsonb       | Full DiscountPolicy             |
| status            | text        | queued | ready | error          |
| core_version      | text        | e.g. "mock-v1.0.0"             |
| result_summary    | jsonb       | ReportSummary                   |
| result_calendar   | jsonb       | CalendarDay[]                   |
| error_message     | text        | Nullable                        |

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

Migration file: `supabase/migrations/001_initial.sql`

---

## 6. Integration Boundary for Python Core

The mock pricing engine lives in `src/core/pricingCore.ts`. To replace it:

1. Build a Python service that accepts `PricingCoreInput` (JSON) and returns
   `PricingCoreOutput` (JSON). Both interfaces are defined in `pricingCore.ts`.

2. Implement `src/core/pythonAdapter.ts`:
   - HTTP client to call the Python service
   - Error handling + retries
   - Response mapping to `PricingCoreOutput`

3. Update the import in `src/app/api/reports/route.ts`:
   ```ts
   // Change from:
   import { generatePricingReport } from "@/core/pricingCore"
   // To:
   import { generatePricingReport } from "@/core/pythonAdapter"
   ```

4. Set environment variables:
   - `PRICING_SERVICE_URL`
   - `PRICING_SERVICE_API_KEY`

The API contract and frontend remain unchanged.

---

## 7. Security + RLS Notes

- RLS is enabled on both tables
- Authenticated users can only read/write their own rows
- Public access to shared reports uses the **service role key** server-side
  (the `/api/r/{shareId}` route handler), not a public RLS policy
- Anon key is used client-side, service key is server-only
- No secrets in client-side code
- `.env.example` documents required variables
- Market tracking email addresses are stored; handle with care in production

---

## 8. Known Limitations (Phase A)

- **Mock data only:** Pricing is deterministic but not real market data
- **No authentication:** Dashboard is a placeholder; user_id is always null
- **No email sending:** Market tracking saves preferences but doesn't send emails
- **No real geocoding:** Address is stored as-is, no validation or normalization
- **Client-side routing:** Results page fetches from API; if Supabase is not
  configured, only the demo report (`/r/demo`) works
- **No rate limiting:** API routes have no throttling
- **No error boundary:** Client errors aren't caught gracefully

---

## 9. Next Iteration Backlog

### Phase B — Authentication + Persistence
- [ ] Add Supabase Auth (email + social login)
- [ ] Wire user_id into report creation
- [ ] Build real dashboard with past reports list
- [ ] Add report comparison feature

### Phase B — Real Pricing
- [ ] Build Python pricing service
- [ ] Implement pythonAdapter.ts
- [ ] Add real market data sources
- [ ] Geocoding + address normalization

### Phase B — Retention
- [ ] Email service integration (Resend / SendGrid)
- [ ] Weekly market digest emails
- [ ] Under-market price alerts
- [ ] Unsubscribe flow

### Phase C — Growth
- [ ] SEO optimization (meta tags, OG images)
- [ ] Share report via social media
- [ ] Embeddable widget for blogs
- [ ] Referral system
- [ ] Multi-listing portfolio support

### Tech Debt
- [ ] Add rate limiting to API routes
- [ ] Error boundaries on client pages
- [ ] E2E tests (Playwright)
- [ ] Unit tests for pricing core
- [ ] Input sanitization for address field
- [ ] Accessibility audit (WCAG 2.1 AA)
