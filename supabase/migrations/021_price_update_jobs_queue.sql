-- 021: price_update_jobs queue for Airbnb calendar write-back
--
-- Dedicated queue table consumed by the Python auto-apply worker.
-- Jobs are inserted by the Next.js manual-apply API route and claimed
-- atomically via claim_price_update_job() using FOR UPDATE SKIP LOCKED.

CREATE TABLE IF NOT EXISTS price_update_jobs (
  id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id          uuid        NOT NULL REFERENCES saved_listings(id) ON DELETE CASCADE,
  user_id             uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  source_report_id    uuid        REFERENCES pricing_reports(id) ON DELETE SET NULL,

  range_start         date        NOT NULL,
  range_end           date        NOT NULL,
  calendar            jsonb       NOT NULL,
  settings_snapshot   jsonb       NOT NULL DEFAULT '{}'::jsonb,

  status              text        NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'running', 'ready', 'error')),
  worker_claimed_at   timestamptz,
  worker_claim_token  uuid,
  worker_heartbeat_at timestamptz,
  worker_attempts     int         NOT NULL DEFAULT 0,

  result              jsonb,
  error_message       text,
  started_at          timestamptz,
  completed_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_price_update_jobs_status_created
  ON price_update_jobs (status, created_at);

CREATE INDEX IF NOT EXISTS idx_price_update_jobs_listing_created
  ON price_update_jobs (listing_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_price_update_jobs_heartbeat
  ON price_update_jobs (worker_heartbeat_at);

ALTER TABLE price_update_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read their own price_update_jobs"
  ON price_update_jobs FOR SELECT
  USING (user_id = auth.uid());

CREATE OR REPLACE FUNCTION claim_price_update_job(
  p_worker_token  uuid,
  p_stale_minutes int DEFAULT 15
)
RETURNS SETOF price_update_jobs
LANGUAGE plpgsql
AS $$
DECLARE
  v_row price_update_jobs%ROWTYPE;
BEGIN
  SELECT *
    INTO v_row
    FROM price_update_jobs
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
    RETURN;
  END IF;

  UPDATE price_update_jobs
     SET status              = 'running',
         worker_claimed_at   = now(),
         worker_heartbeat_at = now(),
         worker_claim_token  = p_worker_token,
         worker_attempts     = worker_attempts + 1,
         started_at          = COALESCE(started_at, now()),
         updated_at          = now(),
         error_message       = NULL
   WHERE id = v_row.id;

  RETURN QUERY
    SELECT * FROM price_update_jobs WHERE id = v_row.id;
END;
$$;
