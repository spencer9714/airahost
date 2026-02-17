-- Migration 004: per-user last-minute strategy preferences

CREATE TABLE IF NOT EXISTS user_pricing_preferences (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  pricing_report_id uuid NOT NULL REFERENCES pricing_reports(id) ON DELETE CASCADE,
  mode              text NOT NULL CHECK (mode IN ('auto', 'manual')),
  aggressiveness    int NOT NULL CHECK (aggressiveness >= 0 AND aggressiveness <= 100),
  floor             numeric(4,2) NOT NULL CHECK (floor >= 0.65 AND floor <= 0.90),
  cap               numeric(4,2) NOT NULL CHECK (cap >= 1.00 AND cap <= 1.10),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, pricing_report_id)
);

CREATE INDEX IF NOT EXISTS idx_user_pricing_preferences_user_updated
  ON user_pricing_preferences (user_id, updated_at DESC);

ALTER TABLE user_pricing_preferences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_pricing_preferences_select_own ON user_pricing_preferences;
CREATE POLICY user_pricing_preferences_select_own
  ON user_pricing_preferences FOR SELECT
  USING (auth.uid() = user_id);

DROP POLICY IF EXISTS user_pricing_preferences_insert_own ON user_pricing_preferences;
CREATE POLICY user_pricing_preferences_insert_own
  ON user_pricing_preferences FOR INSERT
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS user_pricing_preferences_update_own ON user_pricing_preferences;
CREATE POLICY user_pricing_preferences_update_own
  ON user_pricing_preferences FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);
