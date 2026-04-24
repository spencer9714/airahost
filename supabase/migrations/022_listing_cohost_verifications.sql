-- 022: Dedicated co-host verification cache table + saved_listings mirror fields
--
-- This migration establishes the durable cache model for co-host verification.
--
-- Design decisions:
-- 1. Supabase is the primary cache.
-- 2. listing_cohost_verifications is the source-of-truth table for one
--    listing/co-host-account verification snapshot.
-- 3. saved_listings keeps a lightweight mirrored summary for cheap dashboard
--    reads and Auto-Apply gating.

-- ── saved_listings mirror fields ──────────────────────────────────────────

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS auto_apply_cohost_status text NOT NULL DEFAULT 'not_started'
    CHECK (auto_apply_cohost_status IN (
      'not_started',
      'invite_opened',
      'user_confirmed',
      'verification_pending',
      'verified',
      'verification_failed'
    ));

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS auto_apply_cohost_verified_at timestamptz;

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS auto_apply_cohost_last_checked_at timestamptz;

COMMENT ON COLUMN saved_listings.auto_apply_cohost_status IS
  'Mirrored co-host verification summary status used for fast dashboard reads and Auto-Apply gating.';

COMMENT ON COLUMN saved_listings.auto_apply_cohost_verified_at IS
  'Timestamp of the most recent successful co-host verification with full access confirmed.';

COMMENT ON COLUMN saved_listings.auto_apply_cohost_last_checked_at IS
  'Timestamp of the most recent co-host verification attempt, regardless of success or failure.';

-- ── Source-of-truth table ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS listing_cohost_verifications (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  saved_listing_id     uuid NOT NULL REFERENCES saved_listings(id) ON DELETE CASCADE,
  user_id              uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  airbnb_listing_id    text NOT NULL,
  cohost_user_id       text NOT NULL,
  cohost_email         text NOT NULL,

  status               text NOT NULL
                       CHECK (status IN (
                         'not_started',
                         'invite_opened',
                         'user_confirmed',
                         'verification_pending',
                         'verified',
                         'verification_failed'
                       )),
  has_full_access      boolean NOT NULL DEFAULT false,
  permissions_label    text,
  payouts_label        text,
  primary_host_label   text,

  verification_method  text,
  error_code           text,
  error_message        text,
  last_checked_at      timestamptz,
  verified_at          timestamptz,
  raw_details          jsonb NOT NULL DEFAULT '{}'::jsonb,

  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_listing_cohost_verifications_unique
  ON listing_cohost_verifications (saved_listing_id, airbnb_listing_id, cohost_user_id);

CREATE INDEX IF NOT EXISTS idx_listing_cohost_verifications_listing_updated
  ON listing_cohost_verifications (saved_listing_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_listing_cohost_verifications_user_updated
  ON listing_cohost_verifications (user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_listing_cohost_verifications_status_checked
  ON listing_cohost_verifications (status, last_checked_at DESC);

ALTER TABLE listing_cohost_verifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own cohost verifications"
  ON listing_cohost_verifications FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own cohost verifications"
  ON listing_cohost_verifications FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own cohost verifications"
  ON listing_cohost_verifications FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own cohost verifications"
  ON listing_cohost_verifications FOR DELETE
  USING (auth.uid() = user_id);

DROP TRIGGER IF EXISTS set_listing_cohost_verifications_updated_at
  ON listing_cohost_verifications;

CREATE TRIGGER set_listing_cohost_verifications_updated_at
  BEFORE UPDATE ON listing_cohost_verifications
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();
