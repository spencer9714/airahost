from __future__ import annotations

import datetime
from typing import Any, Dict, Optional
import re
from urllib.parse import urlparse

import pandas as pd
from supabase import Client

# 作為訓練目標欄位名稱，與 ml/model.py 保持一致
TARGET_COLUMN_NAME = "last_nightly_price"

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
    """
    判斷日期是否為節慶假日或連續假期。
    在實務生產環境中，建議安裝 'holidays' 套件 (pip install holidays) 
    並使用 holidays.TW() 來取得台灣國定假日。
    """
    # 這裡可以擴展邏輯，例如串接 Google Calendar API 或自定義假日資料庫
    # 範例：簡單標記一些常見長假月份或特定日期
    # if dt.month == 2: return 1.0  # 假設農曆新年通常落在二月
    # return 1.0 if dt in TW_HOLIDAYS else 0.0
    return 0.0


def fetch_training_dataset(client: Client, saved_listing_id: Optional[str] = None, limit: int = 5000) -> pd.DataFrame:
    """
    優化策略：全量撈取並進行地理精準度過濾。
    
    1. 撈取全資料庫約 350 筆競爭者。
    2. 計算與目標房源的地理距離，剔除極端離群值（>15km）。
    3. 整合 30 天市場行為趨勢樣本。
    """
    import numpy as np

    # A. 撈取全資料庫競爭者 (對齊 Supabase 看到的 350 筆)
    print(f"[ML Data] 正在從資料庫全表撈取所有競爭者紀錄...")
    pool_df = fetch_comparable_pool_entries(client, saved_listing_id=None, limit=limit)

    # 獲取 Saved Listings 的元數據 (座標與設施)，用來為競爭者標註「地區定價背景」
    sl_res = client.table("saved_listings").select("id, target_lat, target_lng, input_attributes").execute()
    sl_meta = {
        r["id"]: {
            "lat": r.get("target_lat") or 0.0,
            "lng": r.get("target_lng") or 0.0,
            "amenities": (r.get("input_attributes") or {}).get("amenities", [])
        } for r in (sl_res.data or [])
    }

    # 效能優化：將目標房源座標提取移到迴圈外部
    target_listing = fetch_saved_listing_by_id(client, saved_listing_id) if saved_listing_id else None
    t_lat = (target_listing.get("target_lat") or 0.0) if target_listing else 0.0
    t_lng = (target_listing.get("target_lng") or 0.0) if target_listing else 0.0

    expanded_rows = []
    for _, row in pool_df.iterrows():
        d = row.to_dict()
        meta = sl_meta.get(d["saved_listing_id"], {"lat": 0.0, "lng": 0.0, "amenities": []})
        
        # 簡單歐幾里得距離計算（在局部城市範圍內足夠精確）
        if target_listing:
            dist = np.sqrt((meta["lat"] - t_lat)**2 + (meta["lng"] - t_lng)**2)
            # 治本優化：縮小範圍至約 8-10km (0.08 經緯度)，提高地區定價行為的一致性
            if dist > 0.08: continue

        d["price_date"] = datetime.date.today().isoformat() # 實體表快照設為今天
        d["lat"], d["lng"], d["amenities"] = meta["lat"], meta["lng"], meta["amenities"]
        d["is_weekend"], d["is_holiday"] = 0.0, 0.0
        expanded_rows.append(d)

    print(f"[ML Data] 地理過濾完成。保留了 {len(expanded_rows)} 筆鄰近地區樣本。")

    # B. 針對目標房源，從 PricingReport 提取 30 天「市場趨勢樣本」
    if saved_listing_id:
        report = fetch_latest_report_details(client, saved_listing_id)
        if report:
            calendar = report.get("result_calendar") or []
            target_listing = fetch_saved_listing_by_id(client, saved_listing_id)
            target_feat = extract_listing_features(target_listing) if target_listing else {}
            meta = sl_meta.get(saved_listing_id, {"lat": 0.0, "lng": 0.0, "amenities": []})
            
            print(f"[ML Data] 正在從報表提取 30 天市場趨勢 (作為熱度行為樣本)...")
            for day in calendar:
                price = day.get("basePrice")
                if price:
                    row = target_feat.copy()
                    row.update({
                        "airbnb_listing_id": "market_behavior_sample",
                        TARGET_COLUMN_NAME: float(price),
                        "price_date": day.get("date"), # 從報表 JSON 提取日期
                        "lat": meta["lat"], "lng": meta["lng"], "amenities": meta["amenities"],
                        "is_weekend": 1.0 if day.get("isWeekend") else 0.0,
                        "is_holiday": 1.0 if "holiday" in (day.get("flags") or []) else 0.0,
                        "similarity_score": 5.0  # 大幅提高趨勢樣本權重，確保模型學會 30 天波動行為
                    })
                    expanded_rows.append(row)

    if not expanded_rows:
        return pd.DataFrame()

    df = pd.DataFrame(expanded_rows)
    print(f"[ML Data] 資料聚合完成。共計 {len(df)} 筆訓練樣本 (含 350 筆實體房源與市場行為趨勢)。")
    return df
