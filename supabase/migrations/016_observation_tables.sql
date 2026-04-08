-- ============================================================
-- AiraHost — Phase 5A: Normalized Observation Tables
-- Migration 016: per-date pricing observations from nightly reports.
--
-- Creates three append-only observation tables written by the worker
-- after each successful nightly report.  pricing_reports remains the
-- authoritative user-facing artifact; these tables provide structured
-- time-series data for future ML and report-reuse paths.
--
-- Tables:
--   target_price_observations    — market median + effective price per stay_date
--   benchmark_price_observations — pinned benchmark price vs market per stay_date
--   market_comp_observations     — per-comp nightly price per stay_date
--
-- All tables are:
--   - written by the worker service role (bypasses RLS)
--   - read-accessible to the listing owner via RLS
--   - non-fatal on write failure (worker continues if writes fail)
-- ============================================================

-- ── target_price_observations ───────────────────────────────────────────────
-- One row per (pricing_report × stay_date).
-- Captures the market median and adjusted effective prices computed by the
-- nightly analysis for each day in the 30-day window.

CREATE TABLE IF NOT EXISTS target_price_observations (
  id                          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  saved_listing_id            uuid        NOT NULL REFERENCES saved_listings(id)  ON DELETE CASCADE,
  pricing_report_id           uuid        NOT NULL REFERENCES pricing_reports(id) ON DELETE CASCADE,
  captured_at                 timestamptz NOT NULL,          -- market_captured_at from the report
  stay_date                   date        NOT NULL,

  -- Market price signals (from result_calendar)
  market_median_price         double precision,              -- baseDailyPrice / basePrice
  market_price_adjusted       double precision,              -- priceAfterTimeAdjustment
  effective_price_refundable  double precision,              -- effectiveDailyPriceRefundable
  effective_price_nonrefundable double precision,            -- effectiveDailyPriceNonRefundable

  -- Date metadata
  is_weekend                  boolean,
  day_flags                   jsonb,                         -- ["holiday", "low_confidence", …]

  -- Source tag (always nightly_board_refresh for Phase 5A)
  source_type                 text        NOT NULL DEFAULT 'nightly_board_refresh',

  created_at                  timestamptz NOT NULL DEFAULT now(),

  -- Prevent duplicate rows if the observation writer is re-invoked for the
  -- same report (e.g. retry after a transient failure).
  CONSTRAINT target_price_obs_report_date_uniq
    UNIQUE (saved_listing_id, pricing_report_id, stay_date)
);

CREATE INDEX IF NOT EXISTS target_price_obs_listing_idx
  ON target_price_observations (saved_listing_id, stay_date DESC);

CREATE INDEX IF NOT EXISTS target_price_obs_report_idx
  ON target_price_observations (pricing_report_id);

CREATE INDEX IF NOT EXISTS target_price_obs_captured_idx
  ON target_price_observations (captured_at DESC);

ALTER TABLE target_price_observations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own target price observations"
  ON target_price_observations
  FOR SELECT
  USING (
    saved_listing_id IN (
      SELECT id FROM saved_listings WHERE user_id = auth.uid()
    )
  );

-- Worker uses service role key and bypasses RLS — no INSERT policy needed.

-- ── benchmark_price_observations ────────────────────────────────────────────
-- One row per (pricing_report × stay_date) from the pinned benchmark comp's
-- priceByDate.  Populated when a comparable listing with isPinnedBenchmark=true
-- is present in comparableListings.  Rows are only written for reports that
-- include such a comp; the write path does not check Mode C explicitly.

CREATE TABLE IF NOT EXISTS benchmark_price_observations (
  id                          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  saved_listing_id            uuid        NOT NULL REFERENCES saved_listings(id)  ON DELETE CASCADE,
  pricing_report_id           uuid        NOT NULL REFERENCES pricing_reports(id) ON DELETE CASCADE,
  captured_at                 timestamptz NOT NULL,
  stay_date                   date        NOT NULL,

  -- Benchmark listing price for this stay_date
  benchmark_nightly_price     double precision,
  -- Corresponding market median for the same day (from result_calendar)
  market_median_price         double precision,
  -- Benchmark listing URL for provenance / deduplication
  benchmark_listing_url       text,
  -- Per-date confidence not currently available; reserved for future use
  confidence                  double precision,

  source_type                 text        NOT NULL DEFAULT 'nightly_board_refresh',
  created_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS benchmark_price_obs_listing_idx
  ON benchmark_price_observations (saved_listing_id, stay_date DESC);

CREATE INDEX IF NOT EXISTS benchmark_price_obs_report_idx
  ON benchmark_price_observations (pricing_report_id);

CREATE INDEX IF NOT EXISTS benchmark_price_obs_captured_idx
  ON benchmark_price_observations (captured_at DESC);

ALTER TABLE benchmark_price_observations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own benchmark price observations"
  ON benchmark_price_observations
  FOR SELECT
  USING (
    saved_listing_id IN (
      SELECT id FROM saved_listings WHERE user_id = auth.uid()
    )
  );

-- Worker uses service role key and bypasses RLS — no INSERT policy needed.

-- ── market_comp_observations ─────────────────────────────────────────────────
-- One row per (pricing_report × comp_listing × stay_date).
-- Captures the price each comparable listing advertised on each day observed
-- in the nightly window.  Source data is comparableListings[].priceByDate.

CREATE TABLE IF NOT EXISTS market_comp_observations (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  saved_listing_id     uuid        NOT NULL REFERENCES saved_listings(id)  ON DELETE CASCADE,
  pricing_report_id    uuid        NOT NULL REFERENCES pricing_reports(id) ON DELETE CASCADE,
  captured_at          timestamptz NOT NULL,
  stay_date            date        NOT NULL,

  -- Comp identity (for provenance and future deduplication across reports)
  comp_airbnb_id       text,                                -- room ID extracted from URL
  comp_listing_url     text,

  -- Pricing signal
  nightly_price        double precision,

  -- Comp quality metadata (from comparableListings entry)
  similarity_score     double precision,
  is_pinned_benchmark  boolean     NOT NULL DEFAULT false,  -- true for the Mode-C anchor comp

  source_type          text        NOT NULL DEFAULT 'nightly_board_refresh',
  created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS market_comp_obs_listing_idx
  ON market_comp_observations (saved_listing_id, stay_date DESC);

CREATE INDEX IF NOT EXISTS market_comp_obs_report_idx
  ON market_comp_observations (pricing_report_id);

CREATE INDEX IF NOT EXISTS market_comp_obs_captured_idx
  ON market_comp_observations (captured_at DESC);

CREATE INDEX IF NOT EXISTS market_comp_obs_comp_id_idx
  ON market_comp_observations (comp_airbnb_id)
  WHERE comp_airbnb_id IS NOT NULL;

ALTER TABLE market_comp_observations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own market comp observations"
  ON market_comp_observations
  FOR SELECT
  USING (
    saved_listing_id IN (
      SELECT id FROM saved_listings WHERE user_id = auth.uid()
    )
  );

-- Worker uses service role key and bypasses RLS — no INSERT policy needed.
