-- ============================================================
-- AiraHost — Worker Lane Isolation
-- Migration 012: Add job_lane and target_env columns to
--                pricing_reports, backfill existing rows, and
--                update claim_pricing_report() to filter by lane
--                and environment so interactive and nightly
--                workers only claim their own jobs.
-- ============================================================

-- ------------------------------------------------------------
-- 1) Add job_lane column
--    'interactive' — manual / rerun runs (low-latency lane)
--    'nightly'     — scheduled batch runs
-- ------------------------------------------------------------

ALTER TABLE pricing_reports
  ADD COLUMN IF NOT EXISTS job_lane text NOT NULL DEFAULT 'interactive'
    CHECK (job_lane IN ('interactive', 'nightly'));

-- ------------------------------------------------------------
-- 2) Add target_env column (fixes long-standing API/RPC mismatch)
--    API routes have always written target_env but the column
--    never existed in the schema.
-- ------------------------------------------------------------

ALTER TABLE pricing_reports
  ADD COLUMN IF NOT EXISTS target_env text NOT NULL DEFAULT 'production';

-- ------------------------------------------------------------
-- 3) Backfill job_lane for existing rows
--    Any report linked to a listing_reports row with
--    trigger = 'scheduled' is a nightly job.
--    Everything else stays 'interactive' (the column default).
-- ------------------------------------------------------------

UPDATE pricing_reports pr
   SET job_lane = 'nightly'
  FROM listing_reports lr
 WHERE lr.pricing_report_id = pr.id
   AND lr.trigger = 'scheduled';

-- ------------------------------------------------------------
-- 4) Composite index for efficient lane-filtered queue scans
-- ------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_reports_lane_env_status_created
  ON pricing_reports (job_lane, target_env, status, created_at);

-- ------------------------------------------------------------
-- 5) Update claim_pricing_report() to accept lane + env params
--    and filter on both so each worker only claims its own jobs.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION claim_pricing_report(
  p_worker_token  uuid,
  p_stale_minutes int  DEFAULT 15,
  p_job_lane      text DEFAULT 'interactive',
  p_target_env    text DEFAULT 'production'
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
   WHERE job_lane   = p_job_lane
     AND target_env = p_target_env
     AND (
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
    RETURN;  -- no work available for this lane/env
  END IF;

  UPDATE pricing_reports
     SET status              = 'running',
         worker_claimed_at   = now(),
         worker_heartbeat_at = now(),
         worker_claim_token  = p_worker_token,
         worker_attempts     = worker_attempts + 1
   WHERE id = v_row.id;

  RETURN QUERY
    SELECT * FROM pricing_reports WHERE id = v_row.id;
END;
$$;
