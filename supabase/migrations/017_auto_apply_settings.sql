-- Migration 017: Auto-Apply settings
--
-- Adds per-listing automation rules for the Auto-Apply feature.
-- These settings define how automated price suggestions are scoped,
-- guarded, and scheduled.
--
-- Phase note: real Airbnb write automation is NOT implemented yet.
-- These columns persist the user's intent so the UI can show a
-- meaningful configuration state and the worker can read rules
-- when the execution layer is added in a future phase.

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS auto_apply_enabled              boolean        NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS auto_apply_window_end_days      integer        NOT NULL DEFAULT 30
    CONSTRAINT auto_apply_window_end_days_range CHECK (auto_apply_window_end_days BETWEEN 1 AND 365),
  ADD COLUMN IF NOT EXISTS auto_apply_scope                text           NOT NULL DEFAULT 'actionable'
    CONSTRAINT auto_apply_scope_values CHECK (auto_apply_scope IN ('actionable', 'all_sellable')),
  ADD COLUMN IF NOT EXISTS auto_apply_min_price_floor      numeric(10,2),
  ADD COLUMN IF NOT EXISTS auto_apply_min_notice_days      integer        NOT NULL DEFAULT 1
    CONSTRAINT auto_apply_min_notice_days_range CHECK (auto_apply_min_notice_days BETWEEN 0 AND 30),
  ADD COLUMN IF NOT EXISTS auto_apply_max_increase_pct     numeric(5,2),
  ADD COLUMN IF NOT EXISTS auto_apply_max_decrease_pct     numeric(5,2),
  ADD COLUMN IF NOT EXISTS auto_apply_skip_unavailable     boolean        NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS auto_apply_last_updated_at      timestamptz;

COMMENT ON COLUMN saved_listings.auto_apply_enabled          IS 'Whether Auto-Apply is currently enabled for this listing.';
COMMENT ON COLUMN saved_listings.auto_apply_window_end_days  IS 'How many days ahead Auto-Apply looks (always starts from today = day 0).';
COMMENT ON COLUMN saved_listings.auto_apply_scope            IS '"actionable": only nights that breach the alert threshold. "all_sellable": every available night in the window.';
COMMENT ON COLUMN saved_listings.auto_apply_min_price_floor  IS 'Minimum nightly price guardrail. finalPrice = max(recommendedDailyPrice, floor). Does not modify the recommendation.';
COMMENT ON COLUMN saved_listings.auto_apply_min_notice_days  IS 'Skip nights whose check-in date is fewer than N days away.';
COMMENT ON COLUMN saved_listings.auto_apply_max_increase_pct IS 'Maximum allowed price increase above recommendedDailyPrice as a percentage. NULL = no cap.';
COMMENT ON COLUMN saved_listings.auto_apply_max_decrease_pct IS 'Maximum allowed price decrease below recommendedDailyPrice as a percentage. NULL = no cap.';
COMMENT ON COLUMN saved_listings.auto_apply_skip_unavailable IS 'Skip nights that are already booked or blocked.';
COMMENT ON COLUMN saved_listings.auto_apply_last_updated_at  IS 'When the user last saved their Auto-Apply settings.';
