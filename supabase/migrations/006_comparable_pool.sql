-- ============================================================
-- AiraHost — Migration 006
-- Comparable Pool (V2 Spec — Phase 2)
--
-- New table: comparable_pool_entries
--   Persistent per-listing pool of structurally-similar comps,
--   evolving incrementally across reruns.
--
-- New columns on saved_listings:
--   Geographic anchor, min-stay intent, and pool-level stats
--   needed for pool evolution and coverage alerting.
-- ============================================================

-- ------------------------------------------------------------
-- 1) New columns on saved_listings
-- ------------------------------------------------------------

-- Geographic anchor (populated from first successful scrape
-- of the target listing's resolved lat/lng)
ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS target_lat  double precision,
  ADD COLUMN IF NOT EXISTS target_lng  double precision;

-- Minimum-stay intent: NULL = 1-night only (default),
-- 2 = operator also wants 2-night comps.
ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS target_min_stay_nights int;

-- Pool-level bookkeeping (updated each time pool is rebuilt)
ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS comp_pool_last_built_at   timestamptz,
  ADD COLUMN IF NOT EXISTS comp_pool_version         int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS comp_pool_target_radius_km double precision,
  ADD COLUMN IF NOT EXISTS comp_pool_active_size      int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS comp_pool_low_coverage     boolean NOT NULL DEFAULT false;

-- ------------------------------------------------------------
-- 2) comparable_pool_entries table
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS comparable_pool_entries (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  saved_listing_id      uuid NOT NULL REFERENCES saved_listings(id) ON DELETE CASCADE,

  -- Airbnb listing identity
  airbnb_listing_id     text NOT NULL,
  listing_url           text,
  title                 text,

  -- Structural match quality (stable, time-invariant)
  similarity_score      double precision NOT NULL,

  -- Pool scoring (V2 Spec §5)
  pool_score            double precision NOT NULL DEFAULT 0,
  tenure_bonus          double precision NOT NULL DEFAULT 0,
  effective_rank_score  double precision NOT NULL DEFAULT 0,

  -- Tenure / stability
  tenure_runs           int NOT NULL DEFAULT 1,  -- # reruns this entry has survived
  status                text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'degraded', 'removed')),

  -- Layer 2 price reliability (accumulated from Layer 1 events)
  price_reliability_score  double precision NOT NULL DEFAULT 1.0,
  outlier_count            int NOT NULL DEFAULT 0,
  total_observations       int NOT NULL DEFAULT 0,

  -- Minimum-stay discovery flags (populated best-effort from scraping)
  appears_in_1night_search  boolean NOT NULL DEFAULT false,
  appears_in_2night_search  boolean NOT NULL DEFAULT false,
  observed_min_stay_nights  int,                      -- min stay actually seen

  -- Last known listing attributes (snapshot, refreshed on each rerun)
  last_nightly_price    double precision,
  property_type         text,
  bedrooms              int,
  baths                 double precision,
  accommodates          int,
  beds                  int,
  location              text,
  rating                double precision,
  reviews               int,

  -- Timestamps
  first_seen_at         timestamptz NOT NULL DEFAULT now(),
  last_seen_at          timestamptz NOT NULL DEFAULT now(),
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);

-- One active entry per (listing, airbnb_listing_id)
-- (removed entries can be re-inserted; only one non-removed record at a time)
CREATE UNIQUE INDEX IF NOT EXISTS idx_pool_entries_listing_airbnb
  ON comparable_pool_entries (saved_listing_id, airbnb_listing_id)
  WHERE status != 'removed';

-- Fast lookups for pool queries
CREATE INDEX IF NOT EXISTS idx_pool_entries_listing_rank
  ON comparable_pool_entries (saved_listing_id, effective_rank_score DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_pool_entries_listing_status
  ON comparable_pool_entries (saved_listing_id, status);

-- ------------------------------------------------------------
-- 3) Auto-update updated_at on comparable_pool_entries
-- ------------------------------------------------------------

-- Reuse the function created in 003 (update_updated_at_column)

CREATE TRIGGER set_pool_entries_updated_at
  BEFORE UPDATE ON comparable_pool_entries
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- ------------------------------------------------------------
-- 4) RLS — comparable_pool_entries
--    Users may only read/write entries for their own listings.
--    The worker uses the service role key (bypasses RLS).
-- ------------------------------------------------------------

ALTER TABLE comparable_pool_entries ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own pool entries"
  ON comparable_pool_entries FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM saved_listings sl
      WHERE sl.id = comparable_pool_entries.saved_listing_id
        AND sl.user_id = auth.uid()
    )
  );

-- Insert/update/delete is worker-only via service role key;
-- no user-facing policies needed for write operations.
