-- 020: Co-host verification state machine
--
-- Replace the single boolean auto_apply_cohost_ready with a proper status
-- model that cleanly separates "user said they did it" from "system has
-- actually confirmed it".
--
-- Status values:
--   not_started          — no co-host setup has been initiated
--   invite_opened        — user opened the Airbnb co-host invite page
--   user_confirmed       — user explicitly clicked "I've added Airahost"
--   verification_pending — system has queued / started a verification attempt
--   verified             — system positively confirmed co-host write access
--   verification_failed  — most recent verification attempt failed
--
-- KEY RULE: only "verified" should unlock Auto-Apply execution.
-- "user_confirmed" and "verification_pending" are honest intermediate states
-- that MUST NOT be treated as execution-ready.
--
-- Migration strategy for existing auto_apply_cohost_ready data:
--   true  → user_confirmed  (was self-attested only — not system-verified)
--   false → not_started
--
-- The deprecated auto_apply_cohost_ready column is retained in this
-- migration for one cycle of backward compatibility. It will be dropped
-- in a future migration once all callers use the new status model.

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
  ADD COLUMN IF NOT EXISTS auto_apply_cohost_confirmed_at timestamptz;

ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS auto_apply_cohost_verified_at timestamptz;

-- Human-readable error message from the most recent failed verification.
ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS auto_apply_cohost_verification_error text;

-- How the verification was performed (e.g. 'stub', 'airbnb_api').
ALTER TABLE saved_listings
  ADD COLUMN IF NOT EXISTS auto_apply_cohost_verification_method text;

-- Migrate existing self-attested acknowledgements.
-- These become user_confirmed (not verified) — the old boolean was
-- never backed by a real system check.
UPDATE saved_listings
  SET
    auto_apply_cohost_status = 'user_confirmed',
    auto_apply_cohost_confirmed_at = COALESCE(
      auto_apply_last_updated_at::timestamptz,
      now()
    )
  WHERE auto_apply_cohost_ready = true;

-- Mark old column deprecated.
COMMENT ON COLUMN saved_listings.auto_apply_cohost_ready IS
  'Deprecated (migration 020). Use auto_apply_cohost_status instead. '
  'Kept for backward compatibility — will be dropped in a future migration.';
