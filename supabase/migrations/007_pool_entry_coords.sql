-- ============================================================
-- AiraHost — Migration 007
-- Comp coordinates on comparable_pool_entries (Phase 3A)
--
-- Adds approximate lat/lng and distance-to-target to pool entries
-- so that geographic quality of each comp is trackable over time.
--
-- All columns are nullable — coordinates are best-effort and may
-- not be populated for all entries (depends on Airbnb page state).
-- ============================================================

ALTER TABLE comparable_pool_entries
  ADD COLUMN IF NOT EXISTS comp_lat              double precision,
  ADD COLUMN IF NOT EXISTS comp_lng              double precision,
  ADD COLUMN IF NOT EXISTS distance_to_target_km double precision;

-- Index for proximity queries (e.g. "find all pool entries within N km")
CREATE INDEX IF NOT EXISTS idx_pool_entries_distance
  ON comparable_pool_entries (saved_listing_id, distance_to_target_km)
  WHERE distance_to_target_km IS NOT NULL;
