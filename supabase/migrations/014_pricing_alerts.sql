-- ============================================================
-- AiraHost — Pricing Alert Emails
-- Migration 014: Per-listing alert activation + alert log.
--
-- Adds:
--   saved_listings.pricing_alerts_enabled  — per-listing opt-in flag
--   saved_listings.last_alert_sent_at      — cooldown / dedupe state
--   saved_listings.last_alert_direction    — "PRICED_HIGH" | "PRICED_LOW"
--   saved_listings.last_alert_live_price   — price at last alert (for $3 change check)
--   saved_listings.last_alert_report_id    — FK to the report that triggered last alert
--   saved_listings.last_live_price_status  — most recent alert-pass capture status
--
-- Creates:
--   pricing_alert_log — immutable record of every alert evaluation outcome
-- ============================================================

-- ── Alert state on saved_listings ───────────────────────────────────────────

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS pricing_alerts_enabled  boolean       NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS last_alert_sent_at      timestamptz,
  ADD COLUMN IF NOT EXISTS last_alert_direction    text,
  ADD COLUMN IF NOT EXISTS last_alert_live_price   double precision,
  ADD COLUMN IF NOT EXISTS last_alert_report_id    uuid          REFERENCES pricing_reports(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS last_live_price_status  text;

-- ── Alert log ───────────────────────────────────────────────────────────────
-- One row per evaluation outcome (both sent and suppressed).
-- Immutable — never updated after insert, only appended.

CREATE TABLE IF NOT EXISTS pricing_alert_log (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  saved_listing_id     uuid        NOT NULL REFERENCES saved_listings(id) ON DELETE CASCADE,
  pricing_report_id    uuid        NOT NULL REFERENCES pricing_reports(id) ON DELETE CASCADE,
  evaluated_at         timestamptz NOT NULL DEFAULT now(),
  sent_at              timestamptz,
  alert_direction      text,                      -- "PRICED_HIGH" | "PRICED_LOW" | null
  live_price           double precision,
  live_price_status    text,                      -- available_1_night | available_2_night_only | ...
  market_median        double precision,
  recommended_price    double precision,
  vs_recommended_pct   double precision,
  vs_market_pct        double precision,
  suppressed           boolean     NOT NULL DEFAULT false,
  suppression_reason   text,                      -- reason when suppressed=true
  email_sent_to        text,
  email_provider_id    text,                      -- Resend message ID for delivery tracking
  evaluation_date_basis text                      -- the report's input_date_start used as date basis
);

-- ── RLS: users can only read their own alert log rows ───────────────────────

ALTER TABLE pricing_alert_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read their own alert log"
  ON pricing_alert_log
  FOR SELECT
  USING (
    saved_listing_id IN (
      SELECT id FROM saved_listings WHERE user_id = auth.uid()
    )
  );

-- Worker uses service role key and bypasses RLS — no INSERT policy needed.

-- ── Index for per-listing log reads ─────────────────────────────────────────

CREATE INDEX IF NOT EXISTS pricing_alert_log_listing_idx
  ON pricing_alert_log (saved_listing_id, evaluated_at DESC);
