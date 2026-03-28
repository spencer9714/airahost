-- Migration 010: Explicit freshness timestamps on pricing_reports
--
-- Problem: pricing_reports.created_at records when the job was QUEUED,
-- not when results were ready or when market data was captured.
-- This causes freshness to be computed from the wrong origin point, especially
-- for cache-hit reports where market data may be up to 24 h older than created_at.
--
-- Solution: two explicit timestamps with clear semantics.
--
-- completed_at
--   Set when status transitions to 'ready'.
--   For worker-processed jobs: written by complete_job() at scrape finish.
--   For cache-hit reports: written by the API at insert time (job is ready immediately).
--   NULL for queued / running / error reports.
--
-- market_captured_at
--   When the underlying market data was actually collected from Airbnb.
--   For fresh scrapes:       same as completed_at.
--   For cache-hit reports:   the pricing_cache entry's created_at (data may predate
--                            completed_at by up to the cache TTL).
--   For forecast_snapshot:   the source live_analysis report's market_captured_at.
--   NULL until report completes.
--
-- Freshness thresholds (enforced in application code, documented here for reference):
--   fresh    <= 3 days since market_captured_at
--   aging     4–7 days
--   stale    > 7 days
--   missing    market_captured_at IS NULL

ALTER TABLE pricing_reports
  ADD COLUMN IF NOT EXISTS completed_at       timestamptz,
  ADD COLUMN IF NOT EXISTS market_captured_at timestamptz;

COMMENT ON COLUMN pricing_reports.completed_at IS
  'When this report transitioned to status=ready. '
  'NULL for queued/running/error reports.';

COMMENT ON COLUMN pricing_reports.market_captured_at IS
  'When the underlying Airbnb market data was captured. '
  'For fresh scrapes and worker-processed jobs: same as completed_at. '
  'For cache-hit reports: the source pricing_cache entry created_at. '
  'For forecast_snapshot reports: the source live_analysis market_captured_at. '
  'Use this field — not created_at — to compute forecast freshness.';

CREATE INDEX IF NOT EXISTS idx_pricing_reports_market_captured_at
  ON pricing_reports (market_captured_at);

CREATE INDEX IF NOT EXISTS idx_pricing_reports_completed_at
  ON pricing_reports (completed_at);
