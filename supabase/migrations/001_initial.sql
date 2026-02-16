-- ============================================================
-- AiraHost — Initial Schema
-- ============================================================

-- Pricing Reports
create table if not exists pricing_reports (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid references auth.users(id) on delete set null,
  created_at    timestamptz not null default now(),
  share_id      text unique not null,
  input_address text not null,
  input_attributes jsonb not null,
  input_date_start date not null,
  input_date_end   date not null,
  discount_policy  jsonb not null,
  status        text not null default 'queued'
                check (status in ('queued', 'ready', 'error')),
  core_version  text not null,
  result_summary  jsonb,
  result_calendar jsonb,
  error_message text
);

create index if not exists idx_reports_user_created
  on pricing_reports (user_id, created_at desc);

-- Market Tracking Preferences
create table if not exists market_tracking_preferences (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid references auth.users(id) on delete set null,
  email               text,
  address             text not null,
  notify_weekly       boolean not null default false,
  notify_under_market boolean not null default false,
  created_at          timestamptz not null default now()
);

-- ============================================================
-- Row-Level Security
-- ============================================================

alter table pricing_reports enable row level security;
alter table market_tracking_preferences enable row level security;

-- Reports: authenticated users can read their own
create policy "Users can read own reports"
  on pricing_reports for select
  using (auth.uid() = user_id);

-- Reports: authenticated users can insert their own
create policy "Users can insert own reports"
  on pricing_reports for insert
  with check (auth.uid() = user_id or user_id is null);

-- Tracking: users can read their own preferences
create policy "Users can read own tracking prefs"
  on market_tracking_preferences for select
  using (auth.uid() = user_id);

-- Tracking: users can insert their own preferences
create policy "Users can insert own tracking prefs"
  on market_tracking_preferences for insert
  with check (auth.uid() = user_id or user_id is null);

-- NOTE: Public access to shared reports (via /api/r/{shareId})
-- is handled by the API route using the service role key,
-- which bypasses RLS. This is intentional.

