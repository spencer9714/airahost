# PROJECT_MEMORY.md — AiraHost

> This file is the source of truth for any future developer or AI session
> continuing work on this project. Read it fully before making changes.

---

## 1. North Star

**Product:** AiraHost — an AI Revenue Advisor for Airbnb hosts.

**Primary promise:** "Make smarter pricing decisions. Earn more with confidence."

**Target user:** Small Airbnb hosts (1–5 listings) who want data-driven pricing
without complexity.

**Phase A goal:** Traffic + email capture + recurring engagement via market tracking.

**Current state:** Worker queue architecture is live. Reports are queued in Supabase
and processed by a local Python worker (Playwright scraping or mock fallback).

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
- **Worker:** Python (Playwright, Supabase client, pydantic)
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
    pricingCore.ts      # Mock pricing engine (used by demo page)
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
    r/[shareId]/page.tsx # Results page (polls for worker results)
    dashboard/page.tsx  # Dashboard placeholder
    api/                # Route handlers (queue-based)
worker/
  main.py              # Long-running worker (polls Supabase queue)
  __main__.py          # Entrypoint for python -m worker
  requirements.txt     # Python dependencies
  .env.example         # Worker env var docs
  core/
    db.py              # Supabase client helpers (claim, heartbeat, complete)
    cache.py           # Cache key computation + read/write
    discounts.py       # Discount logic (mirrors pricingCore.ts)
    mock_core.py       # Mock fallback when scraping unavailable
  scraper/
    price_estimator.py # Playwright CDP-based Airbnb scraper
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
| core_version         | text        | e.g. "mock-v1.0.0"                    |
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
- `claim_pricing_report(worker_token, stale_minutes)` — atomically claims
  the next queued job using `FOR UPDATE SKIP LOCKED`; also reclaims stale
  running jobs whose heartbeat expired
- `heartbeat_pricing_report(report_id, worker_token)` — updates heartbeat
  timestamp; only succeeds if the caller owns the claim token

Migration files:
- `supabase/migrations/001_initial.sql` — base tables
- `supabase/migrations/002_worker_queue.sql` — worker columns, cache table, functions

---

## 6. Worker Queue Architecture

Reports flow through a queue-based pipeline:

```
POST /api/reports → insert row (status=queued) → return { id, shareId, status }
                                                         │
Python worker polls Supabase ◄─────────────────────────┘
  claim_pricing_report() (atomic, skip locked)
  → status=running, heartbeat thread starts
  → Mode 1: Playwright CDP scrape (if listingUrl provided)
  → Mode 2: Mock fallback (deterministic hash-based)
  → complete_job() → status=ready, results written
  → fail_job() on error → status=error, worker_attempts++

GET /r/{shareId} ← frontend polls every 2s until ready/error
```

**Key design decisions:**
- Worker runs locally (not serverless) for Playwright browser access
- Atomic claim via `FOR UPDATE SKIP LOCKED` prevents duplicate processing
- Heartbeat thread (every 60s) keeps lease alive; stale jobs reclaimed after 15min
- Cache layer: identical inputs hit `pricing_cache` table (24h TTL) and skip worker
- Rate limiting: IP-based in-memory throttle on POST /api/reports (10 req/min)
- Mock pricing engine (`src/core/pricingCore.ts`) is still used for `/r/demo`

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
- Market tracking email addresses are stored; handle with care in production
- API rate limiting: in-memory IP-based throttle (10 req/min on POST /api/reports)

---

## 8. Known Limitations (Phase A)

- **Scraping depends on local Chrome:** Worker needs a running Chrome instance
  with remote debugging enabled for real Airbnb data; falls back to mock otherwise
- **No authentication:** Dashboard is a placeholder; user_id is always null
- **No email sending:** Market tracking saves preferences but doesn't send emails
- **No real geocoding:** Address is stored as-is, no validation or normalization
- **Client-side routing:** Results page fetches from API; if Supabase is not
  configured, only the demo report (`/r/demo`) works
- **No error boundary:** Client errors aren't caught gracefully
- **Single worker:** No horizontal scaling; one worker instance processes all jobs

---

## 9. Next Iteration Backlog

### Phase B — Authentication + Persistence
- [ ] Add Supabase Auth (email + social login)
- [ ] Wire user_id into report creation
- [ ] Build real dashboard with past reports list
- [ ] Add report comparison feature

### Phase B — Data Quality
- [ ] Improve scraper reliability (handle CAPTCHAs, anti-bot)
- [ ] Add real market data sources beyond Airbnb search
- [ ] Geocoding + address normalization
- [ ] Comp-set quality scoring

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
- [ ] Error boundaries on client pages
- [ ] E2E tests (Playwright)
- [ ] Unit tests for pricing core + worker
- [ ] Input sanitization for address field
- [ ] Accessibility audit (WCAG 2.1 AA)
- [ ] Horizontal worker scaling (multiple instances)

# Role 
你是一名資深全棧產品工程師 (Senior Full-Stack Product Engineer)，擅長 Next.js (App Router)、Supabase Auth、Postgres schema 設計，以及 SaaS dashboard UX。

# Project Context
我們正在開發一個 Airbnb 房東定價工具（Host Revenue Coach）。

現有架構：
- 前端：Next.js App Router + TypeScript + Tailwind，部署在 Vercel
- DB：Supabase Postgres + Auth
- Worker：本機 Python Worker（Playwright + CDP）從 Supabase queue 執行報告計算
- pricing_reports table 已存在，儲存每次分析結果（queued -> running -> ready）

目前功能：
- 未登入使用者可以輸入地址 + 房源屬性，產生一份 pricing report（透過 worker 計算）
- 有 shareable results page: /r/{shareId}

--------------------------------------------------
GOAL OF THIS ITERATION (VERY IMPORTANT)
--------------------------------------------------
我們要從「一次性查價工具」升級為「可回訪的房東控制台（Dashboard）」。

核心目標：
1) 讓使用者登入
2) 能保存自己的房源（saved listings）
3) 能查看歷史分析報告
4) 能對同一房源一鍵重新執行分析 (re-run)
5) 提升留存與回訪頻率

====================================================
PRODUCT UX REQUIREMENTS
====================================================

登入後的主流程：
1. 使用者登入
2. 進入 /dashboard
3. 看到：
   - Saved Listings（已保存房源）
   - 每個房源最新分析結果（價格區間、趨勢）
   - Recent Reports（歷史報告）
4. 可：
   - 新增一個 listing
   - 點擊 listing 查看詳情
   - Re-run analysis（重新排隊跑 worker）

Dashboard 應該是「決策控制台」，而不是單純的歷史列表。

====================================================
TASK A — Supabase Auth Integration
====================================================

使用 Supabase Auth（email magic link 或 email/password 簡單即可）。

要求：
- 在 Next.js 中建立 auth flow
- 未登入用戶仍可使用免費查價（現有功能保持）
- 但只有登入用戶可以：
  - 保存 listing
  - 查看 dashboard
  - 查看歷史報告

需要實作：
- /login page
- /dashboard page（protected route）
- middleware 或 server component 檢查 session
- Header 右上角：
  - 未登入：Login
  - 已登入：User menu + Logout

====================================================
TASK B — Database Schema (New Tables)
====================================================

新增兩個核心 table（Supabase migration SQL）：

1) saved_listings
用途：一個 user 可保存多個房源
--------------------------------
id uuid pk default gen_random_uuid()
user_id uuid references auth.users(id) on delete cascade
name text not null
address text not null
attributes jsonb not null
created_at timestamptz default now()

Index:
- (user_id, created_at desc)

RLS:
- user 只能 select/insert/update/delete 自己的 listings


2) listing_reports
用途：連接某個 listing 與多次 pricing_reports 分析
--------------------------------
id uuid pk default gen_random_uuid()
listing_id uuid references saved_listings(id) on delete cascade
report_id uuid references pricing_reports(id) on delete cascade
created_at timestamptz default now()

Index:
- (listing_id, created_at desc)

RLS:
- user 只能讀取屬於自己 listing 的 reports（透過 join saved_listings.user_id）

====================================================
TASK C — API Routes (Next.js App Router)
====================================================

在 /src/app/api 下新增：

1) POST /api/listings
- 建立一個 saved listing
- body: { name, address, attributes }
- 需要登入
- 回傳 listingId

2) GET /api/listings
- 取得目前 user 的所有 saved listings
- 包含最新一筆 report（如果存在）

3) POST /api/listings/{id}/rerun
- 對該 listing 重新建立一筆 pricing_reports（status='queued'）
- 將 listing 的 address/attributes 帶入
- 回傳新的 reportId

4) GET /api/listings/{id}/reports
- 取得該 listing 的歷史報告列表（join pricing_reports）
- 按 created_at desc

====================================================
TASK D — Dashboard UI (CRITICAL)
====================================================

建立頁面： /dashboard

UI 風格：延續 Airbnb-like clean minimal design（大量留白、卡片、柔和邊框）

版面分區：

1) Welcome Header
- "Welcome back, {user email}"
- 小字：Track your listings & optimize pricing

2) Saved Listings Section
卡片列表：
每張卡片顯示：
- Listing name
- Address（略模糊）
- Latest recommended price range（min–max）
- Last analyzed time
- 小趨勢文案（如：Market trending up/down，暫用 placeholder）

按鈕：
- “View details”
- “Re-run analysis”

+ 一個「Add New Listing」卡片（打開 modal）

3) Recent Reports Section
列表顯示最近 5 筆 reports：
- date
- listing name
- median price
- link: View report (/r/{shareId})

====================================================
TASK E — Add New Listing Flow
====================================================

在 Dashboard 中：
點擊「Add New Listing」打開 modal：
表單欄位：
- Listing name
- Address
- Property attributes（沿用 tool page 的欄位結構）

提交後：
1) 建立 saved_listing
2) 同時呼叫 POST /api/reports 建立第一筆分析（status=queued）
3) 關閉 modal 並刷新 dashboard

====================================================
TASK F — Re-run Analysis
====================================================

每個 listing 卡片有「Re-run analysis」按鈕：
流程：
1) 呼叫 POST /api/listings/{id}/rerun
2) 新增一筆 pricing_reports（queued）
3) UI 顯示：
   - “Re-analyzing…”
   - 下次刷新後顯示最新結果

====================================================
TASK G — Access Control Rules
====================================================

- 未登入：
  - 可使用 /tool 產生一次性報告
  - 但不能存 listing / 看 dashboard
- 登入後：
  - 可保存 listing
  - 可看 dashboard 與歷史 reports

在 results page (/r/{shareId})：
- 若該 report 屬於當前 user，可顯示 “Save to my listings” 按鈕
- 未登入顯示 CTA：Login to track this listing

====================================================
TASK H — Deliverables
====================================================

請輸出：
1) 簡短架構說明（Auth + Listings + Reports 關係）
2) 新增/修改的 file tree
3) Supabase migration SQL（saved_listings + listing_reports + RLS）
4) 所有 API routes 完整實作
5) Dashboard page React components（卡片列表 + modal + rerun 按鈕）
6) Login page + auth integration
7) Header auth state UI
8) README：如何設定 Supabase Auth + 測試登入流程

Quality bar:
- UX 必須直覺、乾淨、SaaS 等級
- TypeScript 嚴格型別
- 不要引入大型 UI framework，使用 Tailwind + 自建元件
- 代碼需清楚註解，方便後續 iteration

