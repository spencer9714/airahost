-- ============================================================
-- AiraHost — Saved Listings & Listing-Report History
-- Migration 003: saved_listings + listing_reports tables,
--                RLS policies, indexes.
-- ============================================================

-- ------------------------------------------------------------
-- 1) saved_listings — per-user listing definitions
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS saved_listings (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name                   text NOT NULL,
  input_address          text NOT NULL,
  input_attributes       jsonb NOT NULL,
  default_discount_policy jsonb,
  last_used_at           timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_saved_listings_user_created
  ON saved_listings (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_saved_listings_user_last_used
  ON saved_listings (user_id, last_used_at DESC);

-- ------------------------------------------------------------
-- 2) listing_reports — many-to-many link between saved
--    listings and generated pricing reports
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS listing_reports (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  saved_listing_id   uuid NOT NULL REFERENCES saved_listings(id) ON DELETE CASCADE,
  pricing_report_id  uuid NOT NULL REFERENCES pricing_reports(id) ON DELETE CASCADE,
  trigger            text NOT NULL CHECK (trigger IN ('manual', 'rerun', 'scheduled')),
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_listing_reports_listing_created
  ON listing_reports (saved_listing_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_reports_unique
  ON listing_reports (saved_listing_id, pricing_report_id);

-- ------------------------------------------------------------
-- 3) RLS — saved_listings
-- ------------------------------------------------------------

ALTER TABLE saved_listings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own listings"
  ON saved_listings FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own listings"
  ON saved_listings FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own listings"
  ON saved_listings FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own listings"
  ON saved_listings FOR DELETE
  USING (auth.uid() = user_id);

-- ------------------------------------------------------------
-- 4) RLS — listing_reports
-- ------------------------------------------------------------

ALTER TABLE listing_reports ENABLE ROW LEVEL SECURITY;

-- Users can read listing_reports for their own saved listings
CREATE POLICY "Users can read own listing reports"
  ON listing_reports FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM saved_listings sl
      WHERE sl.id = listing_reports.saved_listing_id
        AND sl.user_id = auth.uid()
    )
  );

-- Users can insert listing_reports for their own saved listings
CREATE POLICY "Users can insert own listing reports"
  ON listing_reports FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM saved_listings sl
      WHERE sl.id = listing_reports.saved_listing_id
        AND sl.user_id = auth.uid()
    )
  );

-- ------------------------------------------------------------
-- 5) Auto-update updated_at on saved_listings
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_saved_listings_updated_at
  BEFORE UPDATE ON saved_listings
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

