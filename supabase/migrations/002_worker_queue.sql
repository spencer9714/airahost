-- ============================================================
-- AiraHost — Worker Queue & Caching
-- Migration 002: Add worker queue fields, cache table, atomic
--                claim function, and heartbeat function.
-- ============================================================

-- ------------------------------------------------------------
-- 1) Expand pricing_reports for worker queue
-- ------------------------------------------------------------

-- Allow 'running' status in addition to queued/ready/error
ALTER TABLE pricing_reports
  DROP CONSTRAINT IF EXISTS pricing_reports_status_check;

ALTER TABLE pricing_reports
  ADD CONSTRAINT pricing_reports_status_check
    CHECK (status IN ('queued', 'running', 'ready', 'error'));

-- Worker claim/heartbeat fields
ALTER TABLE pricing_reports
  ADD COLUMN IF NOT EXISTS worker_claimed_at   timestamptz,
  ADD COLUMN IF NOT EXISTS worker_claim_token  uuid,
  ADD COLUMN IF NOT EXISTS worker_heartbeat_at timestamptz,
  ADD COLUMN IF NOT EXISTS worker_attempts     int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS input_listing_url   text,
  ADD COLUMN IF NOT EXISTS result_core_debug   jsonb,
  ADD COLUMN IF NOT EXISTS cache_key           text;

-- ------------------------------------------------------------
-- 2) Indexes for worker queue
-- ------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_reports_status_created
  ON pricing_reports (status, created_at);

CREATE INDEX IF NOT EXISTS idx_reports_heartbeat
  ON pricing_reports (worker_heartbeat_at);

CREATE INDEX IF NOT EXISTS idx_reports_cache_key
  ON pricing_reports (cache_key);

-- ------------------------------------------------------------
-- 3) pricing_cache table
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pricing_cache (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cache_key  text UNIQUE NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  summary    jsonb NOT NULL,
  calendar   jsonb NOT NULL,
  meta       jsonb  -- e.g. { "source": "scrape|mock", "listing_url": "...", "comps_count": 12 }
);

CREATE INDEX IF NOT EXISTS idx_cache_expires
  ON pricing_cache (expires_at);

-- RLS: pricing_cache is only accessed via service role key (worker & server API)
ALTER TABLE pricing_cache ENABLE ROW LEVEL SECURITY;

-- No public policies — service role key bypasses RLS

-- ------------------------------------------------------------
-- 4) Atomic claim function
--    Picks one claimable job and atomically marks it running.
--    Claimable = queued OR (running but heartbeat stale).
--    Uses FOR UPDATE SKIP LOCKED to avoid contention.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION claim_pricing_report(
  p_worker_token  uuid,
  p_stale_minutes int DEFAULT 15
)
RETURNS SETOF pricing_reports
LANGUAGE plpgsql
AS $$
DECLARE
  v_row pricing_reports%ROWTYPE;
BEGIN
  SELECT *
    INTO v_row
    FROM pricing_reports
   WHERE (
           status = 'queued'
           OR (
             status = 'running'
             AND worker_heartbeat_at < now() - (p_stale_minutes || ' minutes')::interval
           )
         )
   ORDER BY created_at ASC
   LIMIT 1
   FOR UPDATE SKIP LOCKED;

  IF NOT FOUND THEN
    RETURN;  -- no rows to claim
  END IF;

  UPDATE pricing_reports
     SET status              = 'running',
         worker_claimed_at   = now(),
         worker_heartbeat_at = now(),
         worker_claim_token  = p_worker_token,
         worker_attempts     = worker_attempts + 1
   WHERE id = v_row.id;

  -- Return the updated row
  RETURN QUERY
    SELECT * FROM pricing_reports WHERE id = v_row.id;
END;
$$;

-- ------------------------------------------------------------
-- 5) Heartbeat function
--    Only updates heartbeat if the caller owns the claim.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION heartbeat_pricing_report(
  p_report_id    uuid,
  p_worker_token uuid
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE pricing_reports
     SET worker_heartbeat_at = now()
   WHERE id                 = p_report_id
     AND worker_claim_token = p_worker_token
     AND status             = 'running';

  RETURN FOUND;
END;
$$;

