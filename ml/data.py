from __future__ import annotations

import datetime
import math
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
import re
from urllib.parse import urlparse

import pandas as pd
from supabase import Client

# ---------------------------------------------------------------------------
# Holiday detection — uses the 'holidays' package when available.
# Country is configurable via ML_HOLIDAY_COUNTRY env var (default: "US").
# ---------------------------------------------------------------------------
try:
    import holidays as _holidays_pkg
    _HOLIDAY_COUNTRY: Optional[str] = os.environ.get("ML_HOLIDAY_COUNTRY", "US")
    _HOLIDAY_CALENDAR = _holidays_pkg.country_holidays(_HOLIDAY_COUNTRY)
    _HOLIDAYS_AVAILABLE = True
except Exception as _e:
    # Catches: ImportError (package missing), KeyError/NotImplementedError (bad country
    # code), and any other runtime error during calendar construction.
    print(
        f"[ML Data] Warning: holiday detection disabled ({type(_e).__name__}: {_e}). "
        "Install the 'holidays' package and set ML_HOLIDAY_COUNTRY to a valid ISO "
        "country code (e.g. 'US', 'GB', 'DE'). Falling back to no-holiday behavior."
    )
    _HOLIDAY_COUNTRY = None
    _HOLIDAY_CALENDAR = None
    _HOLIDAYS_AVAILABLE = False

# 作為訓練目標欄位名稱，與 ml/model.py 保持一致
TARGET_COLUMN_NAME = "last_nightly_price"

# ---------------------------------------------------------------------------
# Normalized-path eligibility thresholds.
#
# All three conditions must pass for _fetch_normalized_training_rows to
# return data.  When any condition fails the function returns ([], reason)
# and fetch_training_dataset falls back to pool_snapshot + report_calendar.
#
# Rationale for defaults:
#   _MIN_NORMALIZED_TARGET_ROWS  = 7  — one week of nightly market snapshots;
#       fewer than this gives no meaningful temporal spread.
#   _MIN_NORMALIZED_UNIQUE_DATES = 7  — same floor; also matches the
#       TimeSeriesSplit minimum in model.py (n_splits+1 = 6 → 7 dates).
#   _MIN_NORMALIZED_COMP_ROWS    = 30 — roughly 5 comps × 6 dates, giving
#       enough pricing variance to train a comp-aware model.
# ---------------------------------------------------------------------------
_MIN_NORMALIZED_TARGET_ROWS  = 7
_MIN_NORMALIZED_UNIQUE_DATES = 7
_MIN_NORMALIZED_COMP_ROWS    = 30

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
        "id,saved_listing_id,airbnb_listing_id,listing_url,title,property_type,bedrooms,baths,accommodates,beds,rating,reviews,similarity_score,pool_score,effective_rank_score,tenure_runs,price_reliability_score,last_nightly_price,status"
    )
    
    if saved_listing_id:
        query = query.eq("saved_listing_id", saved_listing_id)

    result = query.limit(limit).execute()
    rows = result.data or []
    return pd.DataFrame(rows)


def fetch_latest_report_details(client: Client, listing_id: str) -> Optional[Dict[str, Any]]:
    """
    從 pricing_reports 表中獲取該房源最新的計算結果摘要。
    這包含了傳統方法算出來的 nightlyMedian, occupancy, weekdayAvg 等資訊。
    """
    result = client.table("pricing_reports") \
        .select("result_summary, result_calendar, created_at") \
        .eq("listing_id", listing_id) \
        .order("created_at", desc=True) \
        .limit(1).execute()
    
    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


def extract_listing_features(saved_listing: Dict[str, Any]) -> Dict[str, Any]:
    attrs = saved_listing.get("input_attributes") or {}
    return {
        "property_type": attrs.get("propertyType") or attrs.get("property_type") or "unknown",
        "bedrooms": float(attrs.get("bedrooms") or 0),
        "baths": float(attrs.get("bathrooms") or 0),
        "accommodates": float(attrs.get("maxGuests") or attrs.get("guests") or 0),
        "beds": float(attrs.get("beds") or attrs.get("maxGuests") or attrs.get("guests") or 0),
    }


def _get_holiday_flag(dt: datetime.date) -> float:
    """Return 1.0 if dt is a public holiday (via the 'holidays' package), else 0.0."""
    if _HOLIDAYS_AVAILABLE and _HOLIDAY_CALENDAR is not None:
        return 1.0 if dt in _HOLIDAY_CALENDAR else 0.0
    return 0.0


def _compute_date_features(price_dt: datetime.date, observation_dt: datetime.date) -> Dict[str, float]:
    """
    Compute time-aware features for a stay date relative to an observation date.

    Called consistently in both training data preparation and forecast inference
    so that temporal features never diverge between the two paths.

    Args:
        price_dt:       The stay / forecast date.
        observation_dt: When the data was collected (today for pool entries;
                        report.created_at for calendar entries; start_date at
                        inference time).
    """
    dow = price_dt.weekday()            # 0=Mon … 6=Sun
    doy = price_dt.timetuple().tm_yday  # 1–366
    lead = max(0, (price_dt - observation_dt).days)
    return {
        "day_of_week": float(dow),
        "month": float(price_dt.month),
        "day_of_year": float(doy),
        "dow_sin": math.sin(2 * math.pi * dow / 7),
        "dow_cos": math.cos(2 * math.pi * dow / 7),
        "doy_sin": math.sin(2 * math.pi * doy / 365),
        "doy_cos": math.cos(2 * math.pi * doy / 365),
        "lead_time_days": float(lead),
        "is_weekend": 1.0 if dow in (4, 5) else 0.0,
        "is_holiday": _get_holiday_flag(price_dt),
    }


def _safe_float(v: Any) -> Optional[float]:
    """Return float or None for missing / non-numeric values."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Benchmark signal — deferred (Phase 5B+/5C)
# ---------------------------------------------------------------------------
# result_summary.benchmarkPrice is NOT used as a training feature.  It is a
# single scalar per report; using it on comp rows would introduce leakage.
#
# benchmark_price_observations (Phase 5A) provides per-date benchmark prices
# that could be joined onto market_comp_observation rows by stay_date,
# giving each training row a properly aligned benchmark_price feature.
# That join is deferred to Phase 5B+/5C.  See the TODO in the docstring of
# _fetch_normalized_training_rows for the specific join point.
# ---------------------------------------------------------------------------


def _fetch_normalized_training_rows(
    client: Client,
    saved_listing_id: str,
    target_listing: Dict[str, Any],
    sl_meta: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Build training rows from the normalized observation tables.

    Queries target_price_observations (market median per stay_date) and
    market_comp_observations (per-comp nightly price per stay_date), then
    enriches comp rows with structural metadata from comparable_pool_entries.

    Gate
    ----
    All three conditions must pass or the function returns ([], reason):
      - target rows         ≥ _MIN_NORMALIZED_TARGET_ROWS
      - unique target dates ≥ _MIN_NORMALIZED_UNIQUE_DATES
      - comp rows           ≥ _MIN_NORMALIZED_COMP_ROWS

    The pool lookup (comparable_pool_entries) is only fetched after the gate
    passes, avoiding an extra round-trip on the fallback path.

    Returns:
        (rows, fallback_reason)

        rows:            Non-empty list of dicts when the gate passes.
                         Each dict matches the schema expected by
                         fetch_training_dataset / _clean_training_frame.
        fallback_reason: Empty string on success.  Semicolon-separated list
                         of failed conditions when the gate rejects.

    Row sources:
        "target_observation"      — target_price_observations; market median
                                    for the listing's own stay_dates.
        "market_comp_observation" — market_comp_observations; per-comp price
                                    enriched with pool structural metadata.

    TODO (Phase 5B+/5C): join benchmark_price_observations by stay_date onto
    market_comp_observation rows so each training row can carry a
    benchmark_price feature with correct temporal alignment.  The table
    already exists; the join is deferred until the feature proves stable.
    """
    today = datetime.date.today()

    # ── 1. Fetch target_price_observations ────────────────────────────────
    target_res = (
        client.table("target_price_observations")
        .select("stay_date,market_median_price,captured_at")
        .eq("saved_listing_id", saved_listing_id)
        .order("captured_at", desc=True)
        .limit(500)
        .execute()
    )
    target_obs = target_res.data or []

    # Fast-exit if completely empty — skip the comp fetch entirely.
    if not target_obs:
        return [], "no target_price_observations (0 rows)"

    # ── 2. Fetch market_comp_observations ─────────────────────────────────
    comp_res = (
        client.table("market_comp_observations")
        .select("stay_date,comp_airbnb_id,comp_listing_url,nightly_price,similarity_score,captured_at")
        .eq("saved_listing_id", saved_listing_id)
        .order("captured_at", desc=True)
        .limit(2000)
        .execute()
    )
    comp_obs = comp_res.data or []

    # ── 3. Compute gate metrics and print diagnostics ─────────────────────
    n_target = len(target_obs)
    n_comp   = len(comp_obs)

    target_dates: set = {obs["stay_date"] for obs in target_obs if obs.get("stay_date")}
    comp_dates:   set = {obs["stay_date"] for obs in comp_obs  if obs.get("stay_date")}
    n_target_dates = len(target_dates)

    # Comp coverage: how many priced rows land on each stay_date.
    priced_comp_dates = [
        obs["stay_date"] for obs in comp_obs
        if obs.get("stay_date") and obs.get("nightly_price") is not None
    ]
    cpd = Counter(priced_comp_dates)
    if cpd:
        cpd_vals   = sorted(cpd.values())
        cpd_min    = cpd_vals[0]
        cpd_max    = cpd_vals[-1]
        cpd_median = cpd_vals[len(cpd_vals) // 2]
        cpd_str    = f"min={cpd_min}  median={cpd_median}  max={cpd_max} comps/date"
    else:
        cpd_str = "no priced comp rows"

    print(
        f"[ML Data] Normalized path diagnostics:\n"
        f"  target rows    : {n_target}  ({n_target_dates} unique stay_dates)\n"
        f"  comp rows      : {n_comp}  ({len(comp_dates)} unique stay_dates)\n"
        f"  comp/date      : {cpd_str}"
    )

    # ── 4. Gate: all conditions must pass ─────────────────────────────────
    failures: List[str] = []
    if n_target < _MIN_NORMALIZED_TARGET_ROWS:
        failures.append(
            f"target rows {n_target} < {_MIN_NORMALIZED_TARGET_ROWS}"
        )
    if n_target_dates < _MIN_NORMALIZED_UNIQUE_DATES:
        failures.append(
            f"unique stay_dates {n_target_dates} < {_MIN_NORMALIZED_UNIQUE_DATES}"
        )
    if n_comp < _MIN_NORMALIZED_COMP_ROWS:
        failures.append(
            f"comp rows {n_comp} < {_MIN_NORMALIZED_COMP_ROWS}"
        )
    if failures:
        return [], "; ".join(failures)

    # ── 5. Gate passed — fetch pool metadata for comp enrichment ──────────
    meta = sl_meta.get(saved_listing_id, {"lat": 0.0, "lng": 0.0, "amenities": []})

    pool_res = (
        client.table("comparable_pool_entries")
        .select(
            "airbnb_listing_id,listing_url,property_type,bedrooms,baths,"
            "accommodates,beds,rating,reviews,similarity_score,pool_score,"
            "effective_rank_score,price_reliability_score,tenure_runs"
        )
        .eq("saved_listing_id", saved_listing_id)
        .execute()
    )
    pool_by_id: Dict[str, Dict[str, Any]] = {}
    pool_by_url: Dict[str, Dict[str, Any]] = {}
    for p in (pool_res.data or []):
        if p.get("airbnb_listing_id"):
            pool_by_id[str(p["airbnb_listing_id"])] = p
        if p.get("listing_url"):
            pool_by_url[normalize_listing_url(p["listing_url"])] = p

    # ── 6. Build training rows ────────────────────────────────────────────
    rows: List[Dict[str, Any]] = []
    target_feat = extract_listing_features(target_listing)

    for obs in target_obs:
        date_str = obs.get("stay_date")
        price    = obs.get("market_median_price")
        if not date_str or price is None:
            continue
        try:
            stay_dt = datetime.date.fromisoformat(date_str)
        except (ValueError, AttributeError):
            continue
        try:
            obs_dt = datetime.datetime.fromisoformat(
                (obs.get("captured_at") or "").replace("Z", "+00:00")
            ).date()
        except (ValueError, AttributeError):
            obs_dt = today

        row = target_feat.copy()
        row.update({
            "airbnb_listing_id": "target_observation",
            TARGET_COLUMN_NAME: float(price),
            "price_date": date_str,
            "observation_date": obs_dt.isoformat(),
            "row_source": "target_observation",
            "lat": meta["lat"],
            "lng": meta["lng"],
            "amenities": meta.get("amenities") or [],
            # Target-listing observations are maximally similar to themselves.
            "similarity_score": 5.0,
        })
        row.update(_compute_date_features(stay_dt, obs_dt))
        rows.append(row)

    for obs in comp_obs:
        date_str = obs.get("stay_date")
        price    = obs.get("nightly_price")
        if not date_str or price is None:
            continue
        try:
            stay_dt = datetime.date.fromisoformat(date_str)
        except (ValueError, AttributeError):
            continue
        try:
            obs_dt = datetime.datetime.fromisoformat(
                (obs.get("captured_at") or "").replace("Z", "+00:00")
            ).date()
        except (ValueError, AttributeError):
            obs_dt = today

        comp_id   = str(obs.get("comp_airbnb_id") or "")
        comp_url  = obs.get("comp_listing_url") or ""
        pool_entry = (
            pool_by_id.get(comp_id)
            or pool_by_url.get(normalize_listing_url(comp_url))
            or {}
        )
        sim_score = (
            _safe_float(obs.get("similarity_score"))
            or _safe_float(pool_entry.get("similarity_score"))
            or 1.0
        )

        row = {
            "property_type":          pool_entry.get("property_type") or "unknown",
            "bedrooms":               _safe_float(pool_entry.get("bedrooms"))               or 0.0,
            "baths":                  _safe_float(pool_entry.get("baths"))                  or 0.0,
            "accommodates":           _safe_float(pool_entry.get("accommodates"))           or 0.0,
            "beds":                   _safe_float(pool_entry.get("beds"))                   or 0.0,
            "rating":                 _safe_float(pool_entry.get("rating"))                 or 0.0,
            "reviews":                _safe_float(pool_entry.get("reviews"))                or 0.0,
            "tenure_runs":            _safe_float(pool_entry.get("tenure_runs"))            or 0.0,
            "pool_score":             _safe_float(pool_entry.get("pool_score"))             or 0.0,
            "effective_rank_score":   _safe_float(pool_entry.get("effective_rank_score"))   or 0.0,
            "price_reliability_score":_safe_float(pool_entry.get("price_reliability_score"))or 0.0,
            "airbnb_listing_id":      comp_id or "market_comp_observation",
            TARGET_COLUMN_NAME:       float(price),
            "price_date":             date_str,
            "observation_date":       obs_dt.isoformat(),
            "row_source":             "market_comp_observation",
            "lat":                    meta["lat"],
            "lng":                    meta["lng"],
            "amenities":              meta.get("amenities") or [],
            "similarity_score":       sim_score,
        }
        row.update(_compute_date_features(stay_dt, obs_dt))
        rows.append(row)

    return rows, ""


def fetch_training_dataset(client: Client, saved_listing_id: Optional[str] = None, limit: int = 5000) -> pd.DataFrame:
    """
    Assemble the training dataset for the ML pricing model.

    Read path (in priority order):
    ────────────────────────────────────────────────────────────────────────
    1. Normalized (preferred)
       Uses target_price_observations + market_comp_observations only when
       the full eligibility gate in _fetch_normalized_training_rows passes:
         - target rows          ≥ _MIN_NORMALIZED_TARGET_ROWS
         - unique target dates  ≥ _MIN_NORMALIZED_UNIQUE_DATES
         - comp rows            ≥ _MIN_NORMALIZED_COMP_ROWS
       Comp rows are enriched with structural metadata from
       comparable_pool_entries.  Sources: "target_observation",
       "market_comp_observation".

    2. Legacy fallback
       Used when the normalized gate fails or saved_listing_id is absent.
       A. Geo-filtered pool_snapshot rows from comparable_pool_entries
          (all saved listings within ~8 km of target).
       B. report_calendar rows from the listing's most recent pricing_report.
       Sources: "pool_snapshot", "report_calendar".
    ────────────────────────────────────────────────────────────────────────
    """
    import numpy as np

    today = datetime.date.today()

    # ── Shared metadata needed by both paths ──────────────────────────────
    sl_res = client.table("saved_listings").select("id, target_lat, target_lng, input_attributes").execute()
    sl_meta = {
        r["id"]: {
            "lat": r.get("target_lat") or 0.0,
            "lng": r.get("target_lng") or 0.0,
            "amenities": (r.get("input_attributes") or {}).get("amenities", [])
        } for r in (sl_res.data or [])
    }
    target_listing = fetch_saved_listing_by_id(client, saved_listing_id) if saved_listing_id else None

    # ── Path 1: normalized observation tables ─────────────────────────────
    if saved_listing_id and target_listing:
        norm_rows, fallback_reason = _fetch_normalized_training_rows(
            client, saved_listing_id, target_listing, sl_meta
        )
        if norm_rows:
            n_target_r = sum(1 for r in norm_rows if r["row_source"] == "target_observation")
            n_comp_r   = sum(1 for r in norm_rows if r["row_source"] == "market_comp_observation")
            n_dates    = len({r["price_date"] for r in norm_rows if r.get("price_date")})
            print(
                f"[ML Data] Normalized path selected: {len(norm_rows)} rows  "
                f"target_observation={n_target_r}  "
                f"market_comp_observation={n_comp_r}  "
                f"unique_dates={n_dates}"
            )
            return pd.DataFrame(norm_rows)
        print(f"[ML Data] Normalized path rejected ({fallback_reason}); using legacy path.")

    # ── Path 2: legacy fallback ───────────────────────────────────────────
    print(f"[ML Data] 正在從資料庫全表撈取所有競爭者紀錄...")
    pool_df = fetch_comparable_pool_entries(client, saved_listing_id=None, limit=limit)

    t_lat = (target_listing.get("target_lat") or 0.0) if target_listing else 0.0
    t_lng = (target_listing.get("target_lng") or 0.0) if target_listing else 0.0

    expanded_rows = []
    for _, row in pool_df.iterrows():
        d = row.to_dict()
        meta = sl_meta.get(d["saved_listing_id"], {"lat": 0.0, "lng": 0.0, "amenities": []})

        if target_listing:
            dist = np.sqrt((meta["lat"] - t_lat)**2 + (meta["lng"] - t_lng)**2)
            if dist > 0.08:
                continue

        d["price_date"] = today.isoformat()
        d["observation_date"] = today.isoformat()
        d["row_source"] = "pool_snapshot"
        d["lat"], d["lng"], d["amenities"] = meta["lat"], meta["lng"], meta["amenities"]
        d.update(_compute_date_features(today, today))
        expanded_rows.append(d)

    print(f"[ML Data] 地理過濾完成。保留了 {len(expanded_rows)} 筆鄰近地區樣本。")

    # B. report_calendar supplement for the target listing
    if saved_listing_id:
        report = fetch_latest_report_details(client, saved_listing_id)
        if report:
            calendar = report.get("result_calendar") or []
            target_feat = extract_listing_features(target_listing) if target_listing else {}
            meta = sl_meta.get(saved_listing_id, {"lat": 0.0, "lng": 0.0, "amenities": []})

            try:
                report_obs_dt = datetime.datetime.fromisoformat(
                    (report.get("created_at") or "").replace("Z", "+00:00")
                ).date()
            except (ValueError, AttributeError):
                report_obs_dt = today

            print(f"[ML Data] 正在從報表提取 30 天市場趨勢 (作為熱度行為樣本)...")
            for day in calendar:
                price = day.get("basePrice")
                date_str = day.get("date")
                if not price or not date_str:
                    continue
                try:
                    stay_dt = datetime.date.fromisoformat(date_str)
                except (ValueError, AttributeError):
                    continue
                row = target_feat.copy()
                row.update({
                    "airbnb_listing_id": "market_behavior_sample",
                    TARGET_COLUMN_NAME: float(price),
                    "price_date": date_str,
                    "observation_date": report_obs_dt.isoformat(),
                    "row_source": "report_calendar",
                    "lat": meta["lat"], "lng": meta["lng"], "amenities": meta["amenities"],
                    # similarity_score=5.0 encodes that these are direct target-listing
                    # observations, but train_model applies a source weight of 0.5 to
                    # report_calendar rows so they don't dominate the pool_snapshot rows.
                    "similarity_score": 5.0,
                })
                row.update(_compute_date_features(stay_dt, report_obs_dt))
                expanded_rows.append(row)

    if not expanded_rows:
        return pd.DataFrame()

    df = pd.DataFrame(expanded_rows)
    print(f"[ML Data] 資料聚合完成。共計 {len(df)} 筆訓練樣本 (含 350 筆實體房源與市場行為趨勢)。")
    return df
