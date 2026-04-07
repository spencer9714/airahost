-- 018: auto_apply_runs and auto_apply_run_nights
--
-- Audit / log tables for manual apply runs.
--
-- execution_mode = 'stub' in phase 1 — real Airbnb write-back is not yet
-- implemented. These tables persist a full record of every manual apply
-- attempt so the next phase (live execution) can reuse the same schema
-- without migration changes.
--
-- Write path:  API route (service role key, bypasses RLS).
-- Read path:   authenticated user via RLS policy on listing ownership.

-- ── Run header ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auto_apply_runs (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id        uuid        NOT NULL REFERENCES saved_listings(id) ON DELETE CASCADE,
  report_id         uuid        REFERENCES pricing_reports(id) ON DELETE SET NULL,
  user_id           uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  -- Selected window
  range_start       date        NOT NULL,
  range_end         date        NOT NULL,

  -- Night counts from execution plan
  total_nights      int         NOT NULL,
  nights_included   int         NOT NULL,
  nights_skipped    int         NOT NULL,
  nights_floored    int         NOT NULL DEFAULT 0,
  nights_capped     int         NOT NULL DEFAULT 0,

  -- Execution state
  -- 'stub'  = no real Airbnb writes (current phase)
  -- 'live'  = real Airbnb write-back (future phase)
  execution_mode    text        NOT NULL DEFAULT 'stub'
                      CONSTRAINT auto_apply_runs_exec_mode
                      CHECK (execution_mode IN ('stub', 'live')),

  -- 'simulated' = stub run, nothing changed
  -- 'success'   = live run, all nights applied
  -- 'partial'   = live run, some nights failed
  -- 'failed'    = live run, all nights failed
  result_status     text        NOT NULL DEFAULT 'simulated'
                      CONSTRAINT auto_apply_runs_result_status
                      CHECK (result_status IN ('simulated', 'success', 'partial', 'failed')),

  -- Frozen copy of settings at execution time
  settings_snapshot jsonb       NOT NULL,

  initiated_at      timestamptz NOT NULL DEFAULT now(),
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS auto_apply_runs_listing_idx
  ON auto_apply_runs (listing_id, initiated_at DESC);

CREATE INDEX IF NOT EXISTS auto_apply_runs_user_idx
  ON auto_apply_runs (user_id, initiated_at DESC);

ALTER TABLE auto_apply_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read their own auto_apply_runs"
  ON auto_apply_runs FOR SELECT
  USING (user_id = auth.uid());

-- ── Per-night audit rows ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auto_apply_run_nights (
  id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id              uuid        NOT NULL REFERENCES auto_apply_runs(id) ON DELETE CASCADE,
  listing_id          uuid        NOT NULL REFERENCES saved_listings(id) ON DELETE CASCADE,

  night_date          date        NOT NULL,

  -- Prices at execution time
  recommended_price   numeric(10,2),   -- canonical rec, never modified
  final_applied_price numeric(10,2),   -- max(rec, floor) + caps
  current_known_price numeric(10,2),   -- live Airbnb price if available (future)

  included            boolean     NOT NULL,
  skip_reason         text,            -- 'notice_window' | 'no_data' | null

  -- 'none' | 'floored' | 'capped_increase' | 'capped_decrease' | 'floored_and_capped'
  guardrails_applied  text        NOT NULL DEFAULT 'none',

  -- 'planned'           = included, ready to apply
  -- 'skipped'           = excluded from execution
  -- 'simulated_success' = stub: would have applied
  -- 'simulated_failure' = stub: would have failed (reserved)
  -- 'success'           = live: applied
  -- 'failed'            = live: failed
  apply_status        text        NOT NULL DEFAULT 'planned'
                        CONSTRAINT auto_apply_run_nights_status
                        CHECK (apply_status IN (
                          'planned', 'skipped',
                          'simulated_success', 'simulated_failure',
                          'success', 'failed'
                        )),

  error_message       text,

  created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS auto_apply_run_nights_run_idx
  ON auto_apply_run_nights (run_id);

CREATE INDEX IF NOT EXISTS auto_apply_run_nights_listing_date_idx
  ON auto_apply_run_nights (listing_id, night_date DESC);

ALTER TABLE auto_apply_run_nights ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read their own auto_apply_run_nights"
  ON auto_apply_run_nights FOR SELECT
  USING (
    listing_id IN (
      SELECT id FROM saved_listings WHERE user_id = auth.uid()
    )
  );
