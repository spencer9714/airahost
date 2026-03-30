-- ============================================================
-- AiraHost — Listing Timezone
-- Migration 013: Add listing_timezone to saved_listings so
--                nightly reports can use the listing's local
--                date instead of UTC.
--
-- Populated by the worker when it writes back geocoded coords.
-- Stores an IANA timezone string, e.g. "America/Los_Angeles".
-- NULL means timezone not yet resolved; callers fall back to
-- "America/Los_Angeles" until the worker enriches the row.
-- ============================================================

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS listing_timezone text;
