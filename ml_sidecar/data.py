from __future__ import annotations

import datetime as dt
import math
import os
from typing import Any, Dict, Optional

import pandas as pd
from supabase import Client

TARGET_COLUMN_NAME = "observed_market_price"
VALID_TRAINING_SCOPES = {"global", "listing_local"}

try:
    import holidays as _holidays_pkg

    _HOLIDAY_COUNTRY = os.environ.get("ML_SIDECAR_HOLIDAY_COUNTRY", "US")
    _HOLIDAY_CALENDAR = _holidays_pkg.country_holidays(_HOLIDAY_COUNTRY)
    _HOLIDAYS_AVAILABLE = True
except Exception:
    _HOLIDAY_COUNTRY = None
    _HOLIDAY_CALENDAR = None
    _HOLIDAYS_AVAILABLE = False


def normalize_training_scope(value: Optional[str]) -> str:
    raw = (value or "").strip().lower() or "global"
    if raw not in VALID_TRAINING_SCOPES:
        raise ValueError(
            f"Unsupported training scope '{value}'. "
            f"Expected one of: {', '.join(sorted(VALID_TRAINING_SCOPES))}."
        )
    return raw


def get_default_training_scope() -> str:
    return normalize_training_scope(os.environ.get("ML_SIDECAR_TRAINING_SCOPE", "global"))


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _has_valid_coordinates(lat: Optional[float], lng: Optional[float]) -> bool:
    if lat is None or lng is None:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _normalize_amenities(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _get_holiday_flag(stay_date: dt.date) -> float:
    if _HOLIDAYS_AVAILABLE and _HOLIDAY_CALENDAR is not None:
        return 1.0 if stay_date in _HOLIDAY_CALENDAR else 0.0
    return 0.0


def _compute_date_features(stay_date: dt.date, observation_date: dt.date) -> Dict[str, float]:
    day_of_week = stay_date.weekday()
    day_of_year = stay_date.timetuple().tm_yday
    lead_time_days = max(0, (stay_date - observation_date).days)

    return {
        "day_of_week": float(day_of_week),
        "month": float(stay_date.month),
        "day_of_year": float(day_of_year),
        "dow_sin": math.sin(2 * math.pi * day_of_week / 7),
        "dow_cos": math.cos(2 * math.pi * day_of_week / 7),
        "doy_sin": math.sin(2 * math.pi * day_of_year / 365),
        "doy_cos": math.cos(2 * math.pi * day_of_year / 365),
        "lead_time_days": float(lead_time_days),
        "is_weekend": 1.0 if day_of_week in (4, 5) else 0.0,
        "is_holiday": _get_holiday_flag(stay_date),
    }


def fetch_saved_listing_by_id(client: Client, listing_id: str) -> Optional[Dict[str, Any]]:
    result = (
        client.table("saved_listings")
        .select("id,name,input_address,target_lat,target_lng,input_attributes")
        .eq("id", listing_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def resolve_saved_listing_coordinates(saved_listing: Optional[Dict[str, Any]]) -> Dict[str, float]:
    attrs = (saved_listing or {}).get("input_attributes") or {}

    lat = _safe_float((saved_listing or {}).get("target_lat"))
    lng = _safe_float((saved_listing or {}).get("target_lng"))
    if not _has_valid_coordinates(lat, lng):
        lat = _safe_float(attrs.get("lat"))
        lng = _safe_float(attrs.get("lng"))

    return {
        "lat": float(lat) if _has_valid_coordinates(lat, lng) else 0.0,
        "lng": float(lng) if _has_valid_coordinates(lat, lng) else 0.0,
    }


def extract_listing_features(saved_listing: Dict[str, Any]) -> Dict[str, Any]:
    attrs = saved_listing.get("input_attributes") or {}
    coords = resolve_saved_listing_coordinates(saved_listing)

    return {
        "property_type": (
            _clean_text(attrs.get("propertyType"))
            or _clean_text(attrs.get("property_type"))
            or "unknown"
        ),
        "bedrooms": float(attrs.get("bedrooms") or 0.0),
        "baths": float(attrs.get("bathrooms") or attrs.get("baths") or 0.0),
        "accommodates": float(attrs.get("maxGuests") or attrs.get("guests") or 0.0),
        "beds": float(attrs.get("beds") or 0.0),
        "lat": coords["lat"],
        "lng": coords["lng"],
        "amenities": _normalize_amenities(attrs.get("amenities")),
    }


def _pick_target_price(row: Dict[str, Any]) -> Optional[float]:
    for key in (
        "effective_daily_price_refundable",
        "effective_daily_price_non_refundable",
        "base_daily_price",
        "base_price",
    ):
        value = _safe_float(row.get(key))
        if value is not None and value > 0:
            return value
    return None


def fetch_training_dataset(
    client: Client,
    *,
    saved_listing_id: str,
    limit: int = 5000,
    training_scope: str = "global",
) -> pd.DataFrame:
    training_scope = normalize_training_scope(training_scope)

    listing_rows_result = (
        client.table("saved_listings")
        .select("id,name,input_address,target_lat,target_lng,input_attributes")
        .execute()
    )
    listing_rows = listing_rows_result.data or []
    listing_by_id = {row["id"]: row for row in listing_rows if row.get("id")}

    query = (
        client.table("market_price_observations")
        .select(
            "saved_listing_id,observed_at,stay_date,days_until_stay,"
            "listing_property_type,listing_bedrooms,listing_baths,"
            "listing_accommodates,listing_beds,target_lat,target_lng,"
            "amenities,base_price,base_daily_price,"
            "effective_daily_price_refundable,effective_daily_price_non_refundable,"
            "comps_used,is_weekend"
        )
        .order("observed_at", desc=True)
        .limit(limit)
    )

    if training_scope == "listing_local":
        query = query.eq("saved_listing_id", saved_listing_id)

    result = query.execute()
    rows = result.data or []

    training_rows: list[Dict[str, Any]] = []

    for row in rows:
        row_listing_id = _clean_text(row.get("saved_listing_id"))
        if not row_listing_id:
            continue

        price = _pick_target_price(row)
        if price is None:
            continue

        stay_date_raw = _clean_text(row.get("stay_date"))
        observed_at_raw = _clean_text(row.get("observed_at"))
        if not stay_date_raw or not observed_at_raw:
            continue

        try:
            stay_date = dt.date.fromisoformat(stay_date_raw)
            observation_date = dt.datetime.fromisoformat(
                observed_at_raw.replace("Z", "+00:00")
            ).date()
        except ValueError:
            continue

        listing = listing_by_id.get(row_listing_id) or {}
        listing_features = extract_listing_features(listing)
        row_lat = _safe_float(row.get("target_lat"))
        row_lng = _safe_float(row.get("target_lng"))
        amenities = _normalize_amenities(row.get("amenities")) or listing_features["amenities"]

        normalized_row: Dict[str, Any] = {
            "saved_listing_id": row_listing_id,
            "property_type": _clean_text(row.get("listing_property_type"))
            or listing_features["property_type"]
            or "unknown",
            "bedrooms": _safe_float(row.get("listing_bedrooms")) or listing_features["bedrooms"],
            "baths": _safe_float(row.get("listing_baths")) or listing_features["baths"],
            "accommodates": _safe_float(row.get("listing_accommodates")) or listing_features["accommodates"],
            "beds": _safe_float(row.get("listing_beds")) or listing_features["beds"],
            "comps_used": _safe_float(row.get("comps_used")) or 0.0,
            "lat": float(row_lat) if _has_valid_coordinates(row_lat, row_lng) else listing_features["lat"],
            "lng": float(row_lng) if _has_valid_coordinates(row_lat, row_lng) else listing_features["lng"],
            "amenities": amenities,
            TARGET_COLUMN_NAME: float(price),
            "price_date": stay_date.isoformat(),
            "observation_date": observation_date.isoformat(),
            "row_source": "market_price_observation",
        }
        normalized_row.update(_compute_date_features(stay_date, observation_date))
        training_rows.append(normalized_row)

    df = pd.DataFrame(training_rows)
    print(
        f"[ML Sidecar] Loaded {len(df)} training row(s) "
        f"for scope={training_scope} limit={limit}."
    )
    return df
