-- Migration 011: Add source_report_id lineage for forecast_snapshot reports
--
-- Allows forecast_snapshot reports to reference the live_analysis report they
-- were derived from.  This enables:
--   • Correct freshness inheritance (market_captured_at propagation)
--   • Audit trail: which live scrape underpins each forecast
--   • Future regeneration: re-derive forecast from same source without re-scraping
--
-- source_report_id
--   Self-referencing FK → pricing_reports.id.
--   NULL for live_analysis reports (they are their own source).
--   Set at insert time when creating a forecast_snapshot job via the API.
--   ON DELETE SET NULL: deleting a source report orphans but does not cascade.

ALTER TABLE pricing_reports
  ADD COLUMN IF NOT EXISTS source_report_id uuid
    REFERENCES pricing_reports(id) ON DELETE SET NULL;

COMMENT ON COLUMN pricing_reports.source_report_id IS
  'For forecast_snapshot reports: the live_analysis pricing_report this snapshot '
  'was derived from.  NULL for live_analysis reports.  Used for freshness '
  'inheritance (market_captured_at) and lineage tracking.';

-- Partial index — only forecast_snapshot rows will ever have a non-null value.
CREATE INDEX IF NOT EXISTS idx_pricing_reports_source_report_id
  ON pricing_reports (source_report_id)
  WHERE source_report_id IS NOT NULL;
