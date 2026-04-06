-- ============================================================
-- AiraHost — Migration 016
-- Historical market price observations for ML training
--
-- Goal:
--   Preserve append-only market pricing observations so ML can train on
--   recent market behaviour without relying on mutable snapshot tables.
--
-- One row = one report-observation day:
--   "On observed_at, for stay_date, the system saw this market price
--    and this listing context."
-- ============================================================

CREATE TABLE IF NOT EXISTS market_price_observations (
  id                                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pricing_report_id                  uuid NOT NULL REFERENCES pricing_reports(id) ON DELETE CASCADE,
  saved_listing_id                   uuid REFERENCES saved_listings(id) ON DELETE SET NULL,

  observed_at                        timestamptz NOT NULL,
  stay_date                          date NOT NULL,
  days_until_stay                    int NOT NULL,

  input_mode                         text,
  input_address                      text,
  input_listing_url                  text,

  listing_property_type              text,
  listing_bedrooms                   double precision,
  listing_baths                      double precision,
  listing_accommodates               double precision,
  listing_beds                       double precision,
  country_code                       text,
  listing_timezone                   text,
  target_lat                         double precision,
  target_lng                         double precision,
  amenities                          jsonb NOT NULL DEFAULT '[]'::jsonb,

  base_price                         double precision,
  base_daily_price                   double precision,
  price_after_time_adjustment        double precision,
  effective_daily_price_refundable   double precision,
  effective_daily_price_non_refundable double precision,
  comps_used                         int,
  is_weekend                         boolean NOT NULL DEFAULT false,
  flags                              jsonb NOT NULL DEFAULT '[]'::jsonb,

  source_report_type                 text,
  source_core_version                text,
  report_created_at                  timestamptz NOT NULL,
  report_completed_at                timestamptz,
  report_market_captured_at          timestamptz,

  created_at                         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE market_price_observations IS
  'Append-only historical market price observations used for ML training and retraining windows.';

COMMENT ON COLUMN market_price_observations.observed_at IS
  'When the underlying market data was captured. Prefer pricing_reports.market_captured_at.';

COMMENT ON COLUMN market_price_observations.days_until_stay IS
  'stay_date minus observed_at (in whole days). Used for lead-time learning.';

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_obs_report_stay_unique
  ON market_price_observations (pricing_report_id, stay_date);

CREATE INDEX IF NOT EXISTS idx_market_obs_observed_at
  ON market_price_observations (observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_obs_stay_date
  ON market_price_observations (stay_date);

CREATE INDEX IF NOT EXISTS idx_market_obs_listing_observed
  ON market_price_observations (saved_listing_id, observed_at DESC);

ALTER TABLE market_price_observations ENABLE ROW LEVEL SECURITY;

-- Service role / worker / server-only access. No public policies.

CREATE OR REPLACE FUNCTION ingest_market_price_observations(
  p_report_id uuid
)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  v_row_count integer := 0;
BEGIN
  WITH linked_listing AS (
    SELECT
      pr.id AS pricing_report_id,
      COALESCE(
        pr.listing_id,
        (
          SELECT lr.saved_listing_id
          FROM listing_reports lr
          WHERE lr.pricing_report_id = pr.id
          ORDER BY lr.created_at DESC
          LIMIT 1
        )
      ) AS saved_listing_id
    FROM pricing_reports pr
    WHERE pr.id = p_report_id
  ),
  base_report AS (
    SELECT
      pr.id AS pricing_report_id,
      ll.saved_listing_id,
      pr.report_type,
      pr.core_version,
      pr.created_at AS report_created_at,
      pr.completed_at AS report_completed_at,
      pr.market_captured_at AS report_market_captured_at,
      pr.input_address,
      pr.input_listing_url,
      pr.input_attributes,
      pr.result_calendar,
      COALESCE(pr.market_captured_at, pr.completed_at, pr.created_at) AS observed_at,
      sl.target_lat,
      sl.target_lng,
      sl.listing_timezone
    FROM pricing_reports pr
    LEFT JOIN linked_listing ll
      ON ll.pricing_report_id = pr.id
    LEFT JOIN saved_listings sl
      ON sl.id = ll.saved_listing_id
    WHERE pr.id = p_report_id
      AND pr.status = 'ready'
      AND COALESCE(pr.report_type, 'live_analysis') = 'live_analysis'
      AND jsonb_typeof(pr.result_calendar) = 'array'
  ),
  exploded AS (
    SELECT
      br.pricing_report_id,
      br.saved_listing_id,
      br.observed_at,
      (day->>'date')::date AS stay_date,
      ((day->>'date')::date - timezone('UTC', br.observed_at)::date) AS days_until_stay,
      br.input_attributes->>'inputMode' AS input_mode,
      br.input_address,
      br.input_listing_url,
      COALESCE(
        NULLIF(br.input_attributes->>'propertyType', ''),
        NULLIF(br.input_attributes->>'property_type', ''),
        'unknown'
      ) AS listing_property_type,
      NULLIF(br.input_attributes->>'bedrooms', '')::double precision AS listing_bedrooms,
      NULLIF(
        COALESCE(br.input_attributes->>'bathrooms', br.input_attributes->>'baths'),
        ''
      )::double precision AS listing_baths,
      NULLIF(
        COALESCE(br.input_attributes->>'maxGuests', br.input_attributes->>'guests'),
        ''
      )::double precision AS listing_accommodates,
      NULLIF(br.input_attributes->>'beds', '')::double precision AS listing_beds,
      COALESCE(
        NULLIF(br.input_attributes->>'country_code', ''),
        NULLIF(br.input_attributes->>'countryCode', ''),
        'TW'
      ) AS country_code,
      br.listing_timezone,
      br.target_lat,
      br.target_lng,
      CASE
        WHEN jsonb_typeof(br.input_attributes->'amenities') = 'array'
          THEN br.input_attributes->'amenities'
        ELSE '[]'::jsonb
      END AS amenities,
      NULLIF(day->>'basePrice', '')::double precision AS base_price,
      NULLIF(day->>'baseDailyPrice', '')::double precision AS base_daily_price,
      NULLIF(day->>'priceAfterTimeAdjustment', '')::double precision AS price_after_time_adjustment,
      NULLIF(day->>'effectiveDailyPriceRefundable', '')::double precision AS effective_daily_price_refundable,
      NULLIF(day->>'effectiveDailyPriceNonRefundable', '')::double precision AS effective_daily_price_non_refundable,
      NULLIF(day->>'compsUsed', '')::int AS comps_used,
      COALESCE(NULLIF(day->>'isWeekend', '')::boolean, false) AS is_weekend,
      CASE
        WHEN jsonb_typeof(day->'flags') = 'array'
          THEN day->'flags'
        ELSE '[]'::jsonb
      END AS flags,
      br.report_type AS source_report_type,
      br.core_version AS source_core_version,
      br.report_created_at,
      br.report_completed_at,
      br.report_market_captured_at
    FROM base_report br
    CROSS JOIN LATERAL jsonb_array_elements(br.result_calendar) AS day
    WHERE day ? 'date'
  )
  INSERT INTO market_price_observations (
    pricing_report_id,
    saved_listing_id,
    observed_at,
    stay_date,
    days_until_stay,
    input_mode,
    input_address,
    input_listing_url,
    listing_property_type,
    listing_bedrooms,
    listing_baths,
    listing_accommodates,
    listing_beds,
    country_code,
    listing_timezone,
    target_lat,
    target_lng,
    amenities,
    base_price,
    base_daily_price,
    price_after_time_adjustment,
    effective_daily_price_refundable,
    effective_daily_price_non_refundable,
    comps_used,
    is_weekend,
    flags,
    source_report_type,
    source_core_version,
    report_created_at,
    report_completed_at,
    report_market_captured_at
  )
  SELECT
    pricing_report_id,
    saved_listing_id,
    observed_at,
    stay_date,
    days_until_stay,
    input_mode,
    input_address,
    input_listing_url,
    listing_property_type,
    listing_bedrooms,
    listing_baths,
    listing_accommodates,
    listing_beds,
    country_code,
    listing_timezone,
    target_lat,
    target_lng,
    amenities,
    base_price,
    base_daily_price,
    price_after_time_adjustment,
    effective_daily_price_refundable,
    effective_daily_price_non_refundable,
    comps_used,
    is_weekend,
    flags,
    source_report_type,
    source_core_version,
    report_created_at,
    report_completed_at,
    report_market_captured_at
  FROM exploded
  ON CONFLICT (pricing_report_id, stay_date)
  DO UPDATE SET
    saved_listing_id = EXCLUDED.saved_listing_id,
    observed_at = EXCLUDED.observed_at,
    days_until_stay = EXCLUDED.days_until_stay,
    input_mode = EXCLUDED.input_mode,
    input_address = EXCLUDED.input_address,
    input_listing_url = EXCLUDED.input_listing_url,
    listing_property_type = EXCLUDED.listing_property_type,
    listing_bedrooms = EXCLUDED.listing_bedrooms,
    listing_baths = EXCLUDED.listing_baths,
    listing_accommodates = EXCLUDED.listing_accommodates,
    listing_beds = EXCLUDED.listing_beds,
    country_code = EXCLUDED.country_code,
    listing_timezone = EXCLUDED.listing_timezone,
    target_lat = EXCLUDED.target_lat,
    target_lng = EXCLUDED.target_lng,
    amenities = EXCLUDED.amenities,
    base_price = EXCLUDED.base_price,
    base_daily_price = EXCLUDED.base_daily_price,
    price_after_time_adjustment = EXCLUDED.price_after_time_adjustment,
    effective_daily_price_refundable = EXCLUDED.effective_daily_price_refundable,
    effective_daily_price_non_refundable = EXCLUDED.effective_daily_price_non_refundable,
    comps_used = EXCLUDED.comps_used,
    is_weekend = EXCLUDED.is_weekend,
    flags = EXCLUDED.flags,
    source_report_type = EXCLUDED.source_report_type,
    source_core_version = EXCLUDED.source_core_version,
    report_created_at = EXCLUDED.report_created_at,
    report_completed_at = EXCLUDED.report_completed_at,
    report_market_captured_at = EXCLUDED.report_market_captured_at;

  GET DIAGNOSTICS v_row_count = ROW_COUNT;
  RETURN v_row_count;
END;
$$;

COMMENT ON FUNCTION ingest_market_price_observations(uuid) IS
  'Explodes pricing_reports.result_calendar into append-only market observations for ML training.';
