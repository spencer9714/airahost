-- 019: Auto-Apply co-host tracking + tighten look-ahead window to 30 days
--
-- Two changes:
--
-- 1. Add auto_apply_cohost_ready column.
--    The product requires Airahost to be added as a co-host on Airbnb before
--    Auto-Apply can write prices. This flag records that the user has
--    completed (or confirmed) that step. It is user-set via the dashboard
--    ("I've added Airahost as co-host"). It does not imply live detection —
--    it is an explicit acknowledgement field.
--
-- 2. Tighten auto_apply_window_end_days to BETWEEN 1 AND 30.
--    The recommendation system only covers the next 30 days. A look-ahead
--    window beyond 30 days was never meaningful and is now rejected at the
--    DB level to match the app-level validation.

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS auto_apply_cohost_ready boolean NOT NULL DEFAULT false;

-- Re-apply the window constraint with the tightened upper bound.
ALTER TABLE saved_listings
  DROP CONSTRAINT IF EXISTS auto_apply_window_end_days_range;

ALTER TABLE saved_listings
  ADD CONSTRAINT auto_apply_window_end_days_range
    CHECK (auto_apply_window_end_days BETWEEN 1 AND 30);
