-- ============================================================
-- AiraHost — Pricing Alert Emails v2
-- Migration 015: Minimum booking nights + URL validation tracking.
--
-- Adds to saved_listings:
--   minimum_booking_nights       — 1–30, controls checkout date for live price capture
--   listing_url_validation_status — "valid" | "invalid" | null (set by worker on capture)
--   listing_url_validated_at     — when the worker last confirmed the URL was reachable
--
-- Adds to pricing_alert_log:
--   booking_nights_basis         — nights used for the successful capture (1 = primary,
--                                   2 = 2-night fallback when min_nights==1, etc.)
-- ============================================================

-- ── saved_listings additions ─────────────────────────────────────────────────

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS minimum_booking_nights      integer      NOT NULL DEFAULT 1
                           CONSTRAINT saved_listings_min_booking_nights_range
                             CHECK (minimum_booking_nights BETWEEN 1 AND 30),
  ADD COLUMN IF NOT EXISTS listing_url_validation_status text,
  ADD COLUMN IF NOT EXISTS listing_url_validated_at      timestamptz;

-- ── pricing_alert_log addition ───────────────────────────────────────────────
-- booking_nights_basis records how many nights were booked when the live price
-- was captured.  Null for evaluations that never reached the capture phase
-- (e.g. alerts_disabled, no_listing_url).

ALTER TABLE pricing_alert_log
  ADD COLUMN IF NOT EXISTS booking_nights_basis integer;
