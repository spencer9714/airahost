from __future__ import annotations

import datetime
from typing import Any, Dict, Optional, List
import re
from urllib.parse import urlparse

import pandas as pd
try:
    import holidays  # type: ignore
except ImportError:
    holidays = None

from supabase import Client

# 作為訓練目標欄位名稱，與 ml/model.py 保持一致
TARGET_COLUMN_NAME = "last_nightly_price"

COUNTRY_NAME_TO_CODE = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "taiwan": "TW",
    "taiwan, province of china": "TW",
    "tw": "TW",
}

def extract_airbnb_room_id(value: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    match = re.search(r"/rooms/(\d+)", value)
    return match.group(1) if match else None


def normalize_listing_url(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlparse(value)
        path = parsed.path.rstrip("/")
        return path.lower()
    except Exception:
        return value.rstrip("/").lower()


def _matches_listing_url(attrs: Any, listing_url: str) -> bool:
    if not isinstance(attrs, dict):
        return False

    target_room_id = extract_airbnb_room_id(listing_url)
    normalized_target = normalize_listing_url(listing_url)
    candidates = [
        attrs.get("listingUrl"),
        attrs.get("listing_url"),
        attrs.get("input_listing_url"),
    ]

    for value in candidates:
        if not isinstance(value, str):
            continue

        if target_room_id:
            saved_room_id = extract_airbnb_room_id(value)
            if saved_room_id and saved_room_id == target_room_id:
                return True

        if normalize_listing_url(value) == normalized_target:
            return True

    return False


def fetch_saved_listing_by_url(client: Client, listing_url: str) -> Optional[Dict[str, Any]]:
    result = client.table("saved_listings").select("id,name,input_attributes,target_lat,target_lng").execute()
    rows = result.data or []

    for row in rows:
        attrs = row.get("input_attributes") or {}
        if _matches_listing_url(attrs, listing_url):
            return row

    return None


def fetch_saved_listing_by_id(client: Client, listing_id: str) -> Optional[Dict[str, Any]]:
    result = client.table("saved_listings").select("id,name,input_attributes,target_lat,target_lng").eq("id", listing_id).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def fetch_comparable_pool_entries(
    client: Client,
    saved_listing_id: Optional[str] = None,
    limit: int = 5000,
) -> pd.DataFrame:
    # 治本：透過 select 關聯 saved_listings 表格拿到座標 (lat/lng) 與設施 (amenities)
    # 假設資料庫支援透過 .select('*, saved_listings(target_lat, target_lng)') 進行簡單關聯
    # 若不支援，則需分兩次抓取後在 Pandas 中 merge。這裡採用 select 擴展模式。
    query = client.table("comparable_pool_entries").select(
        "id,saved_listing_id,airbnb_listing_id,listing_url,title,property_type,bedrooms,baths,accommodates,beds,rating,reviews,similarity_score,pool_score,effective_rank_score,tenure_runs,price_reliability_score,last_nightly_price,status,first_seen_at,last_seen_at,distance_to_target_km,comp_lat,comp_lng"
    )
    
    if saved_listing_id:
        query = query.eq("saved_listing_id", saved_listing_id)

    result = query.limit(limit).execute()
    rows = result.data or []
    return pd.DataFrame(rows)


def fetch_recent_reports(client: Client, listing_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    從 pricing_reports 表中獲取該房源最近幾次的計算結果。
    這包含了傳統方法算出來的 nightlyMedian, occupancy, weekdayAvg 等資訊。
    """
    result = client.table("pricing_reports") \
        .select("id, result_summary, result_calendar, created_at, completed_at, market_captured_at") \
        .eq("listing_id", listing_id) \
        .order("created_at", desc=True) \
        .limit(limit).execute()
    
    return result.data or []


def fetch_latest_report_details(client: Client, listing_id: str) -> Optional[Dict[str, Any]]:
    reports = fetch_recent_reports(client, listing_id, limit=1)
    return reports[0] if reports else None


def _normalize_country_code(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    upper = text.upper()
    if len(upper) == 2 and upper.isalpha():
        return upper

    return COUNTRY_NAME_TO_CODE.get(text.casefold())


def resolve_country_code(saved_listing: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(saved_listing, dict):
        return None

    attrs = saved_listing.get("input_attributes") or {}
    candidates: List[Any] = [
        saved_listing.get("country_code"),
        saved_listing.get("countryCode"),
        saved_listing.get("country"),
        saved_listing.get("addressCountry"),
        attrs.get("country_code"),
        attrs.get("countryCode"),
        attrs.get("country"),
        attrs.get("addressCountry"),
    ]

    address_value = attrs.get("address")
    if isinstance(address_value, dict):
        candidates.extend(
            [
                address_value.get("country"),
                address_value.get("countryCode"),
                address_value.get("country_code"),
                address_value.get("addressCountry"),
            ]
        )
    else:
        candidates.append(address_value)

    for value in candidates:
        normalized = _normalize_country_code(value)
        if normalized:
            return normalized

    return None


def _clean_location_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    text = value.strip()
    return text or None


def _normalize_state(value: Any) -> Optional[str]:
    text = _clean_location_text(value)
    if not text:
        return None
    return text.upper() if len(text) <= 3 else text


def _normalize_postal_code(value: Any) -> Optional[str]:
    text = _clean_location_text(value)
    if not text:
        return None
    return text.upper()


def _build_postal_prefix(postal_code: Optional[str]) -> Optional[str]:
    if not postal_code:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", postal_code.upper())
    prefix = cleaned[:3]
    return prefix if len(prefix) >= 3 else None


def _build_geo_bucket(lat: Any, lng: Any) -> str:
    try:
        lat_value = float(lat)
        lng_value = float(lng)
    except (TypeError, ValueError):
        return "unknown"

    return f"{round(lat_value, 2):.2f}_{round(lng_value, 2):.2f}"


def _derive_location_parts(location: Optional[str]) -> Dict[str, Optional[str]]:
    text = _clean_location_text(location)
    if not text:
        return {
            "location_city": None,
            "location_state": None,
            "country_code": None,
            "location_country_code": None,
        }

    parts = [part.strip() for part in text.split(",") if part.strip()]
    city = parts[0] if parts else None
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z]{2,3}", parts[1]):
        state = _normalize_state(parts[1])
        country_text = None
    else:
        state = _normalize_state(parts[1]) if len(parts) >= 3 else None
        country_text = parts[-1] if len(parts) >= 2 else None
    country_code = _normalize_country_code(country_text)
    return {
        "location_city": city,
        "location_state": state,
        "country_code": country_code,
        "location_country_code": country_code,
    }


def _extract_report_location_features(report_row: Dict[str, Any]) -> Dict[str, Any]:
    summary = report_row.get("result_summary") or {}
    target_spec = summary.get("targetSpec") if isinstance(summary, dict) else None
    if not isinstance(target_spec, dict):
        return {}

    derived = _derive_location_parts(target_spec.get("location"))
    lat = target_spec.get("lat")
    lng = target_spec.get("lng")
    country_code = _normalize_country_code(
        target_spec.get("countryCode") or target_spec.get("country")
    ) or derived.get("country_code")

    features = {
        "country_code": country_code,
        "location_country_code": country_code or "unknown",
        "location_city": _clean_location_text(target_spec.get("city")) or derived.get("location_city") or "unknown",
        "location_state": _normalize_state(target_spec.get("state")) or derived.get("location_state") or "unknown",
        "location_postal_prefix": _build_postal_prefix(
            _normalize_postal_code(target_spec.get("postalCode") or target_spec.get("postal_code"))
        ) or "unknown",
        "lat": float(lat) if lat not in [None, ""] else 0.0,
        "lng": float(lng) if lng not in [None, ""] else 0.0,
    }
    features["geo_bucket"] = _build_geo_bucket(features["lat"], features["lng"])
    return features


def _merge_listing_location_features(
    primary: Dict[str, Any],
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(primary)
    if not fallback:
        return merged

    for key in ["country_code", "location_country_code", "location_city", "location_state", "location_postal_prefix", "geo_bucket"]:
        current = merged.get(key)
        fallback_value = fallback.get(key)
        if current in [None, "", "unknown"] and fallback_value not in [None, "", "unknown"]:
            merged[key] = fallback_value

    for key in ["lat", "lng"]:
        current = merged.get(key, 0.0)
        fallback_value = fallback.get(key, 0.0)
        try:
            current_value = float(current)
        except (TypeError, ValueError):
            current_value = 0.0
        try:
            candidate_value = float(fallback_value)
        except (TypeError, ValueError):
            candidate_value = 0.0
        if current_value == 0.0 and candidate_value != 0.0:
            merged[key] = candidate_value

    if merged.get("geo_bucket") in [None, "", "unknown"]:
        merged["geo_bucket"] = _build_geo_bucket(merged.get("lat"), merged.get("lng"))

    return merged


def extract_listing_features(saved_listing: Dict[str, Any]) -> Dict[str, Any]:
    attrs = saved_listing.get("input_attributes") or {}
    lat = saved_listing.get("target_lat") or 0.0
    lng = saved_listing.get("target_lng") or 0.0
    postal_code = _normalize_postal_code(
        attrs.get("postalCode") or attrs.get("postal_code")
    )
    country_code = resolve_country_code(saved_listing)
    return {
        "property_type": attrs.get("propertyType") or attrs.get("property_type") or "unknown",
        "bedrooms": float(attrs.get("bedrooms") or 0),
        "baths": float(attrs.get("bathrooms") or 0),
        "accommodates": float(attrs.get("maxGuests") or attrs.get("guests") or 0),
        "beds": float(attrs.get("beds") or attrs.get("maxGuests") or attrs.get("guests") or 0),
        "country_code": country_code,
        "location_country_code": country_code or "unknown",
        "location_city": _clean_location_text(attrs.get("city")) or "unknown",
        "location_state": _normalize_state(attrs.get("state")) or "unknown",
        "location_postal_prefix": _build_postal_prefix(postal_code) or "unknown",
        "geo_bucket": _build_geo_bucket(lat, lng),
        "distance_to_target_km": 0.0,
        "lat": float(lat or 0.0),
        "lng": float(lng or 0.0),
    }


_HOLIDAY_CACHE: Dict[str, Any] = {}

def _get_holiday_flag(dt: datetime.date, country_code: Optional[str] = None) -> float:
    """
    根據房源所在的國家代碼動態判斷日期是否為假日。
    """
    if holidays is None or not country_code:
        return 0.0

    if country_code not in _HOLIDAY_CACHE:
        try:
            # 動態獲取該國家的假日清單並進行快取，優化迴圈執行效能
            _HOLIDAY_CACHE[country_code] = holidays.country_holidays(country_code)
        except Exception:
            return 0.0

    return 1.0 if dt in _HOLIDAY_CACHE[country_code] else 0.0


def _coerce_to_date(value: Any) -> Optional[datetime.date]:
    if value is None:
        return None

    if isinstance(value, datetime.datetime):
        return value.date()

    if isinstance(value, datetime.date):
        return value

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None

        try:
            if "T" in text or text.endswith("Z"):
                return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            return datetime.date.fromisoformat(text)
        except ValueError:
            return None

    return None


def _resolve_report_capture_date(report: Dict[str, Any]) -> Optional[datetime.date]:
    for key in ["market_captured_at", "completed_at", "created_at"]:
        resolved = _coerce_to_date(report.get(key))
        if resolved is not None:
            return resolved
    return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_comp_identifier(comp: Dict[str, Any], report_id: str, index: int) -> str:
    for key in ["id", "url", "title"]:
        value = comp.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        elif value is not None:
            return str(value)
    return f"{report_id}:comp:{index}"


def _extract_comp_location_features(
    comp: Dict[str, Any],
    report_market_features: Dict[str, Any],
) -> Dict[str, Any]:
    derived = _derive_location_parts(comp.get("location"))
    postal_code = _normalize_postal_code(
        comp.get("postalCode") or comp.get("postal_code")
    )
    country_code = _normalize_country_code(
        comp.get("countryCode") or comp.get("country")
    ) or derived.get("country_code")

    raw_lat = comp.get("lat")
    raw_lng = comp.get("lng")
    lat = _safe_float(raw_lat, 0.0) if raw_lat not in [None, ""] else 0.0
    lng = _safe_float(raw_lng, 0.0) if raw_lng not in [None, ""] else 0.0

    features = {
        "country_code": country_code,
        "location_country_code": country_code or "unknown",
        "location_city": _clean_location_text(comp.get("city")) or derived.get("location_city") or "unknown",
        "location_state": _normalize_state(comp.get("state")) or derived.get("location_state") or "unknown",
        "location_postal_prefix": _build_postal_prefix(postal_code) or "unknown",
        "lat": lat,
        "lng": lng,
    }
    features["geo_bucket"] = _build_geo_bucket(features["lat"], features["lng"])
    return _merge_listing_location_features(features, report_market_features)


def _build_report_market_features(
    report: Dict[str, Any],
    sl_meta: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    report_features = _extract_report_location_features(report)
    listing_id = report.get("listing_id")
    listing_features = sl_meta.get(listing_id, {}) if listing_id else {}
    merged = _merge_listing_location_features(report_features, listing_features)
    merged.setdefault("country_code", None)
    merged.setdefault("location_country_code", "unknown")
    merged.setdefault("location_city", "unknown")
    merged.setdefault("location_state", "unknown")
    merged.setdefault("location_postal_prefix", "unknown")
    merged.setdefault("geo_bucket", "unknown")
    merged.setdefault("lat", 0.0)
    merged.setdefault("lng", 0.0)
    return merged


def fetch_recent_ready_reports_for_training(client: Client, limit: int = 5000) -> List[Dict[str, Any]]:
    approx_rows_per_report = 60
    report_limit = max(30, min(250, (max(limit, 1) // approx_rows_per_report) + 20))
    result = client.table("pricing_reports") \
        .select("id, listing_id, report_type, result_summary, created_at, completed_at, market_captured_at") \
        .eq("status", "ready") \
        .order("created_at", desc=True) \
        .limit(report_limit).execute()

    rows: List[Dict[str, Any]] = []
    for row in result.data or []:
        if row.get("report_type") == "forecast_snapshot":
            continue
        summary = row.get("result_summary")
        if not isinstance(summary, dict):
            continue
        if not isinstance(summary.get("comparableListings"), list):
            continue
        rows.append(row)
    return rows


def _is_weekend_date(dt: datetime.date) -> bool:
    return dt.weekday() in [4, 5]


def _get_month_category(dt: datetime.date) -> str:
    return dt.strftime("%b").lower()


def _get_day_of_week_category(dt: datetime.date) -> str:
    return dt.strftime("%a").lower()


def _get_day_of_month_bucket(dt: datetime.date) -> str:
    if dt.day <= 7:
        return "week_1"
    if dt.day <= 14:
        return "week_2"
    if dt.day <= 21:
        return "week_3"
    if dt.day <= 28:
        return "week_4"
    return "month_end"


def _get_lead_time_bucket(days_until_stay: int) -> str:
    if days_until_stay <= 0:
        return "same_day"
    if days_until_stay <= 3:
        return "days_1_3"
    if days_until_stay <= 7:
        return "days_4_7"
    if days_until_stay <= 14:
        return "days_8_14"
    if days_until_stay <= 30:
        return "days_15_30"
    return "days_31_plus"


def _get_holiday_streak_length(dt: datetime.date, country_code: Optional[str] = None) -> int:
    if _get_holiday_flag(dt, country_code) != 1.0:
        return 0

    streak = 1
    cursor = dt - datetime.timedelta(days=1)
    while _get_holiday_flag(cursor, country_code) == 1.0:
        streak += 1
        cursor -= datetime.timedelta(days=1)

    cursor = dt + datetime.timedelta(days=1)
    while _get_holiday_flag(cursor, country_code) == 1.0:
        streak += 1
        cursor += datetime.timedelta(days=1)

    return streak


def _is_long_weekend_date(dt: datetime.date, country_code: Optional[str] = None) -> bool:
    if _get_holiday_flag(dt, country_code) == 1.0:
        return _get_holiday_streak_length(dt, country_code) >= 2

    if not _is_weekend_date(dt):
        return False

    for offset in [-2, -1, 1, 2]:
        if _get_holiday_flag(dt + datetime.timedelta(days=offset), country_code) == 1.0:
            return True
    return False


def _get_holiday_window_type(dt: datetime.date, country_code: Optional[str] = None) -> str:
    is_holiday = _get_holiday_flag(dt, country_code) == 1.0
    prev_holiday = _get_holiday_flag(dt - datetime.timedelta(days=1), country_code) == 1.0
    next_holiday = _get_holiday_flag(dt + datetime.timedelta(days=1), country_code) == 1.0

    if is_holiday:
        return "holiday_run" if _get_holiday_streak_length(dt, country_code) >= 2 else "holiday_single"
    if prev_holiday and next_holiday:
        return "holiday_bridge"
    if next_holiday:
        return "pre_holiday"
    if prev_holiday:
        return "post_holiday"
    if _is_long_weekend_date(dt, country_code):
        return "long_weekend"
    return "regular"


def build_temporal_feature_values(
    *,
    price_date: datetime.date,
    observed_at_date: datetime.date,
    first_seen_date: Optional[datetime.date] = None,
    country_code: Optional[str] = None,
) -> Dict[str, Any]:
    first_seen = first_seen_date or observed_at_date
    days_until_stay = (price_date - observed_at_date).days
    days_since_first_seen = max((observed_at_date - first_seen).days, 0)
    holiday_streak_length = _get_holiday_streak_length(price_date, country_code)

    return {
        "price_date": price_date.isoformat(),
        "observed_at_date": observed_at_date.isoformat(),
        "first_seen_date": first_seen.isoformat(),
        "days_until_stay": float(days_until_stay),
        "days_since_first_seen": float(days_since_first_seen),
        "month": _get_month_category(price_date),
        "day_of_week": _get_day_of_week_category(price_date),
        "day_of_month_bucket": _get_day_of_month_bucket(price_date),
        "lead_time_bucket": _get_lead_time_bucket(days_until_stay),
        "is_weekend": 1.0 if _is_weekend_date(price_date) else 0.0,
        "is_holiday": _get_holiday_flag(price_date, country_code),
        "holiday_streak_length": float(holiday_streak_length),
        "is_long_weekend": 1.0 if _is_long_weekend_date(price_date, country_code) else 0.0,
        "holiday_window_type": _get_holiday_window_type(price_date, country_code),
    }


def fetch_training_dataset(client: Client, saved_listing_id: Optional[str] = None, limit: int = 5000) -> pd.DataFrame:
    """
    優化策略：全量撈取並進行地理精準度過濾。
    
    1. 撈取全資料庫約 350 筆競爭者。
    2. 計算與目標房源的地理距離，剔除極端離群值（>15km）。
    3. 整合 30 天市場行為趨勢樣本。
    """
    # A. 獲取 Saved Listings 的元數據 (座標與設施)，用來為競爭者標註市場位置特徵
    sl_res = client.table("saved_listings").select("id, target_lat, target_lng, input_attributes").execute()
    report_meta = {}
    recent_ready_reports = fetch_recent_ready_reports_for_training(client, limit=limit)
    for report_row in recent_ready_reports:
        listing_id = report_row.get("listing_id")
        if not listing_id or listing_id in report_meta:
            continue
        report_meta[listing_id] = _extract_report_location_features(report_row)

    sl_meta = {}
    for r in (sl_res.data or []):
        listing_features = _merge_listing_location_features(
            extract_listing_features(r),
            report_meta.get(r["id"], {}),
        )
        sl_meta[r["id"]] = {
            **listing_features,
            "lat": listing_features.get("lat", 0.0),
            "lng": listing_features.get("lng", 0.0),
            "amenities": (r.get("input_attributes") or {}).get("amenities", []),
            "country_code": listing_features.get("country_code"),
        }

    target_listing = fetch_saved_listing_by_id(client, saved_listing_id) if saved_listing_id else None

    today = datetime.date.today()
    expanded_rows: List[Dict[str, Any]] = []
    earliest_comp_dates: Dict[tuple[str, datetime.date], datetime.date] = {}

    if recent_ready_reports:
        print(
            f"[ML Data] 正在從 {len(recent_ready_reports)} 份 ready reports 展開其他房源的 future priceByDate 樣本..."
        )

        for report in recent_ready_reports:
            observed_at_date = _resolve_report_capture_date(report) or today
            summary = report.get("result_summary") or {}
            for index, comp in enumerate(summary.get("comparableListings") or []):
                if not isinstance(comp, dict):
                    continue
                comp_id = _extract_comp_identifier(comp, str(report.get("id") or "unknown"), index)
                for date_str in (comp.get("priceByDate") or {}).keys():
                    price_date = _coerce_to_date(date_str)
                    if price_date is None or price_date < observed_at_date:
                        continue
                    key = (comp_id, price_date)
                    previous_earliest = earliest_comp_dates.get(key)
                    if previous_earliest is None or observed_at_date < previous_earliest:
                        earliest_comp_dates[key] = observed_at_date

        for report in recent_ready_reports:
            observed_at_date = _resolve_report_capture_date(report) or today
            summary = report.get("result_summary") or {}
            report_market_features = _build_report_market_features(report, sl_meta)
            report_id = str(report.get("id") or "unknown")

            for index, comp in enumerate(summary.get("comparableListings") or []):
                if not isinstance(comp, dict):
                    continue

                comp_id = _extract_comp_identifier(comp, report_id, index)
                location_features = _extract_comp_location_features(comp, report_market_features)
                similarity = max(_safe_float(comp.get("similarity"), 0.0), 0.0)
                used_in_pricing_days = max(int(_safe_float(comp.get("usedInPricingDays"), 1.0)), 1)
                price_reliability = min(1.0, used_in_pricing_days / 7.0)

                for date_str, raw_price in (comp.get("priceByDate") or {}).items():
                    price_date = _coerce_to_date(date_str)
                    price = _safe_float(raw_price, 0.0)
                    if price_date is None or price_date < observed_at_date or price <= 0:
                        continue

                    row: Dict[str, Any] = {
                        "airbnb_listing_id": comp_id,
                        "saved_listing_id": report.get("listing_id"),
                        "source_type": "report_comp_price_by_date",
                        "source_report_id": report_id,
                        "source_listing_id": report.get("listing_id"),
                        "source_comp_id": comp_id,
                        TARGET_COLUMN_NAME: price,
                        "title": comp.get("title") or "Comparable listing",
                        "listing_url": comp.get("url"),
                        "property_type": comp.get("propertyType") or "unknown",
                        "bedrooms": _safe_float(comp.get("bedrooms"), 0.0),
                        "baths": _safe_float(comp.get("baths"), 0.0),
                        "accommodates": _safe_float(comp.get("accommodates"), 0.0),
                        "beds": _safe_float(comp.get("beds"), 0.0),
                        "rating": _safe_float(comp.get("rating"), 0.0),
                        "reviews": _safe_float(comp.get("reviews"), 0.0),
                        "similarity_score": similarity,
                        "pool_score": similarity,
                        "effective_rank_score": similarity,
                        "tenure_runs": float(used_in_pricing_days),
                        "price_reliability_score": price_reliability,
                        "distance_to_target_km": _safe_float(comp.get("distanceKm"), 0.0),
                        "amenities": [],
                    }
                    row.update(location_features)
                    row.update(
                        build_temporal_feature_values(
                            price_date=price_date,
                            observed_at_date=observed_at_date,
                            first_seen_date=earliest_comp_dates.get((comp_id, price_date), observed_at_date),
                            country_code=location_features.get("country_code"),
                        )
                    )
                    expanded_rows.append(row)
                    if len(expanded_rows) >= limit:
                        break

                if len(expanded_rows) >= limit:
                    break
            if len(expanded_rows) >= limit:
                break

    if expanded_rows:
        df = pd.DataFrame(expanded_rows)
        print(
            f"[ML Data] 已展開 {len(df)} 筆其他房源未來入住日價格樣本，作為主要訓練資料。"
        )
        return df

    # B. 如果暫時沒有 day-level future pricing，就退回 comparable_pool 快照資料
    import numpy as np

    print("[ML Data] 找不到可用的 future priceByDate 樣本，退回 comparable_pool_entries 快照資料...")
    pool_df = fetch_comparable_pool_entries(client, saved_listing_id=None, limit=limit)
    t_lat = (target_listing.get("target_lat") or 0.0) if target_listing else 0.0
    t_lng = (target_listing.get("target_lng") or 0.0) if target_listing else 0.0

    for _, row in pool_df.iterrows():
        d = row.to_dict()
        meta = sl_meta.get(
            d["saved_listing_id"],
            {
                "lat": 0.0,
                "lng": 0.0,
                "amenities": [],
                "location_country_code": "unknown",
                "location_city": "unknown",
                "location_state": "unknown",
                "location_postal_prefix": "unknown",
                "geo_bucket": "unknown",
            },
        )

        if target_listing:
            dist = np.sqrt((meta["lat"] - t_lat) ** 2 + (meta["lng"] - t_lng) ** 2)
            if dist > 0.08:
                continue

        observed_at_date = _coerce_to_date(d.get("last_seen_at")) or today
        first_seen_date = _coerce_to_date(d.get("first_seen_at")) or observed_at_date
        d["lat"], d["lng"], d["amenities"] = meta["lat"], meta["lng"], meta["amenities"]
        d["location_country_code"] = meta.get("location_country_code", "unknown")
        d["location_city"] = meta.get("location_city", "unknown")
        d["location_state"] = meta.get("location_state", "unknown")
        d["location_postal_prefix"] = meta.get("location_postal_prefix", "unknown")
        d["geo_bucket"] = meta.get("geo_bucket", "unknown")
        d["distance_to_target_km"] = float(d.get("distance_to_target_km") or 0.0)
        d["source_type"] = "pool_snapshot"
        d["source_report_id"] = None
        d["source_listing_id"] = d.get("saved_listing_id")
        d["source_comp_id"] = d.get("airbnb_listing_id")
        d.update(
            build_temporal_feature_values(
                price_date=observed_at_date,
                observed_at_date=observed_at_date,
                first_seen_date=first_seen_date,
                country_code=meta.get("country_code"),
            )
        )
        expanded_rows.append(d)

    if not expanded_rows:
        return pd.DataFrame()

    df = pd.DataFrame(expanded_rows)
    print(f"[ML Data] 資料聚合完成。共計 {len(df)} 筆訓練樣本。")
    return df
