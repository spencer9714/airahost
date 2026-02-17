-- ============================================================
-- AiraHost — Migration 005
-- Add per-listing date defaults + direct listing_id on reports
-- ============================================================

-- 1) Add date-mode defaults to saved_listings
ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS default_date_mode text
    NOT NULL DEFAULT 'next_30'
    CHECK (default_date_mode IN ('next_30', 'custom'));

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS default_start_date date;

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS default_end_date date;

-- 2) Add direct listing_id FK on pricing_reports
ALTER TABLE pricing_reports
  ADD COLUMN IF NOT EXISTS listing_id uuid REFERENCES saved_listings(id) ON DELETE SET NULL;

-- 3) Backfill listing_id from existing listing_reports links
UPDATE pricing_reports pr
SET listing_id = lr.saved_listing_id
FROM listing_reports lr
WHERE lr.pricing_report_id = pr.id
  AND pr.listing_id IS NULL;

-- 4) Indexes
CREATE INDEX IF NOT EXISTS idx_pricing_reports_listing_created
  ON pricing_reports (listing_id, created_at DESC)
  WHERE listing_id IS NOT NULL;

-- (user_id, created_at DESC) index already exists from 001_initial

-- 5) RLS: allow users to read reports linked to their listings via listing_id
CREATE POLICY "Users can read reports for own listings"
  ON pricing_reports FOR SELECT
  USING (
    listing_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM saved_listings sl
      WHERE sl.id = pricing_reports.listing_id
        AND sl.user_id = auth.uid()
    )
  );
