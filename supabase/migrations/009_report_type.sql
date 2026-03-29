-- Migration 009: Add report_type to pricing_reports
--
-- Tags every report as either:
--   'live_analysis'      — on-demand, freshly scraped Airbnb data
--   'forecast_snapshot'  — generated from stored market data (no live scrape)
--
-- All existing rows default to 'live_analysis'.
-- Future forecast generation logic will write 'forecast_snapshot' reports.

ALTER TABLE pricing_reports
  ADD COLUMN IF NOT EXISTS report_type text NOT NULL DEFAULT 'live_analysis';

ALTER TABLE pricing_reports
  ADD CONSTRAINT pricing_reports_report_type_check
    CHECK (report_type IN ('live_analysis', 'forecast_snapshot'));

CREATE INDEX IF NOT EXISTS idx_pricing_reports_report_type
  ON pricing_reports (report_type);

COMMENT ON COLUMN pricing_reports.report_type IS
  'live_analysis = on-demand report from freshly scraped Airbnb data; '
  'forecast_snapshot = generated from stored market data without live scraping';
