-- 008_progress_meta.sql
-- Add lightweight progress tracking to pricing_reports.
--
-- Using a single jsonb column rather than 4 separate columns keeps the
-- schema minimal and allows adding sub-fields (e.g. step_label, substage)
-- in future without another migration.
--
-- Shape written by the worker:
--   {
--     "pct":                 0-100,
--     "stage":               "connecting|extracting_target|searching_comps|...",
--     "message":             "Human-readable status for the frontend",
--     "updated_at":          "ISO-8601 UTC timestamp",
--     "est_seconds_remaining": integer (optional)
--   }

alter table public.pricing_reports
  add column if not exists progress_meta jsonb;

comment on column public.pricing_reports.progress_meta is
  'Worker progress snapshot: {pct, stage, message, updated_at, est_seconds_remaining?}. '
  'Null until the worker first calls update_progress(). '
  'Frontend falls back to spinner when this column is null.';
