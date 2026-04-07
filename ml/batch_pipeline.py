"""
AiraHost ML 批次處理與開發參考程式

本程式示範了完整的 ML 工作流，可作為自動化批次腳本使用，也可作為前端對接 ML 功能的範本。
流程包含：
1. 初始化 Supabase 連線。
2. 獲取目標房源與競爭者歷史資料。
3. 模型管理：支援「直接預測」或「重新訓練」。
4. 產出未來 30 天的報價建議報表。
"""

import os
import argparse
import sys
import subprocess
from pathlib import Path

# 確保專案根目錄在 Python 路徑中，以便直接執行此腳本時能找到 'ml' 套件
if __name__ == "__main__" and __package__ is None:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import date, datetime, timedelta
from xgboost import XGBRegressor
from ml.supabase_client import get_client
from ml.data import (
    fetch_saved_listing_by_url,
    fetch_training_dataset,
    extract_listing_features,
    fetch_latest_report_details,
    TARGET_COLUMN_NAME,
    fetch_saved_listing_by_id,
    _compute_date_features,
    _HOLIDAYS_AVAILABLE,
    _HOLIDAY_COUNTRY,
)
from ml.model import (
    train_model,
    forecast_prices,
    build_default_numeric_features,
    build_feature_matrix_df,
    build_target_row,
    AMENITIES_LIST,
    _apply_forecast_guardrail,
)

def plot_feature_importance(importances: pd.Series):
    """將特徵重要性繪製成圖表並儲存，便於開發者確認模型邏輯。"""
    try:
        plt.figure(figsize=(10, 8))
        importances.sort_values().plot(kind='barh', color='skyblue')
        plt.title('XGBoost Feature Importance')
        plt.xlabel('Importance Score')
        plt.grid(axis='x', linestyle='--', alpha=0.7)
        plt.tight_layout()
        plt.savefig('ml/reports/feature_importance.png')
        print("狀態: 特徵重要性圖表已儲存至 ml/reports/feature_importance.png")
    except Exception as e:
        print(f"警告: 繪製圖表時發生錯誤: {e}")

import statistics # 導入 statistics 模組用於平均值計算
def update_metrics_history(listing_id: str, metrics: dict, n_samples: int):
    """將訓練指標記錄到 CSV 中，以便長期追蹤模型表現。"""
    metrics_path = "ml/reports/metrics_history.csv"
    new_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "listing_id": listing_id,
        "mae": round(metrics["mae"], 2),
        "mae_std": round(metrics.get("mae_std", 0), 2),
        "mape": round(metrics["mape"], 4),
        "r2": round(metrics["r2"], 4),
        "q2": round(metrics["q2"], 4),
        "r2_std": round(metrics.get("r2_std", 0), 4),
        "cv_strategy": metrics.get("cv_strategy", "unknown"),
        "cv_time_safe": metrics.get("cv_time_safe", True),
        "n_samples": n_samples,
    }
    
    df = pd.DataFrame([new_entry])
    # 如果檔案不存在則寫入標題，否則直接附加在後方
    if not os.path.exists(metrics_path):
        df.to_csv(metrics_path, index=False)
    else:
        df.to_csv(metrics_path, mode='a', header=False, index=False)
    print(f"狀態: 訓練指標已更新至 {metrics_path}")

def execute_batch_workflow(listing_url: str, force_train: bool = False):
    print(f"--- 啟動 AiraHost ML 批次流程: {listing_url} ---")
    
    # 0. 確保報告資料夾存在
    Path("ml/reports").mkdir(parents=True, exist_ok=True)

    # 1. 環境初始化
    client = get_client()
    model_file = "ml/reports/saved_model.json"

    # 2. 資料獲取
    # 取得房源本身的物理特徵 (如臥室數量、床位等)
    listing = fetch_saved_listing_by_url(client, listing_url)
    if not listing:
        print(f"錯誤: 在 saved_listings 表中找不到 URL: {listing_url}")
        return
    
    print(f"狀態: 成功定位房源! ID: {listing['id']}, 名稱: {listing.get('name')}")
    target_listing_features = extract_listing_features(listing)

    # 2.1 檢查 pricing_reports 表中是否已有現成的數據
    # 確實檢查資料庫：該表使用 'listing_id' 關聯 saved_listings
    # 我們可以檢查是否有最近的報告 (例如，過去一天內)
    today_iso = date.today().isoformat()
    report_info = fetch_latest_report_details(client, listing["id"])
    
    # 檢查報表是否足夠新 (例如，今天生成)
    is_report_fresh = False
    if report_info and report_info.get("created_at"):
        report_created_date = datetime.fromisoformat(report_info["created_at"].replace("Z", "+00:00")).date().isoformat()
        if report_created_date == today_iso:
            is_report_fresh = True

    # 取得競爭者池的歷史資料 (用於訓練或建立特徵矩陣結構)
    # fetch_training_dataset 會優先從 report_info 中提取數據
    training_df = fetch_training_dataset(client, saved_listing_id=listing["id"])

    # --- 資料源對齊與診斷 ---
    print("\n--- 資料源對齊診斷 ---")
    if report_info:
        summary = report_info.get("result_summary")
        if report_info and report_info.get("result_summary"):
            summary = report_info["result_summary"]
            print(f"\n--- 前端報表數據對照 (來自 pricing_reports) ---")
            print(f"報表日期: {report_info.get('created_at')}")
            print(f"報表建議價 (nightlyMedian): ${summary.get('nightlyMedian'):.2f}")
            print(f"報表平日平均: ${summary.get('weekdayAvg'):.2f}")
            print(f"報表週末平均: ${summary.get('weekendAvg'):.2f}")
            print(f"報表競爭者數量 (usedForPricing): {summary.get('compsSummary', {}).get('usedForPricing')}")
            
            # 統計前端報表中的實際每日價格樣本數
            frontend_daily_samples = 0
            frontend_unique_comps = 0
            frontend_weekday_prices = []
            frontend_weekend_prices = []

            if summary.get("comparableListings"):
                frontend_unique_comps = len(summary["comparableListings"])
                for comp in summary["comparableListings"]:
                    if comp.get("priceByDate"):
                        for date_str, price in comp["priceByDate"].items():
                            if price is not None:
                                frontend_daily_samples += 1
                                dt = datetime.fromisoformat(date_str).date()
                                if dt.weekday() in [4, 5]: # Friday=4, Saturday=5
                                    frontend_weekend_prices.append(price)
                                else:
                                    frontend_weekday_prices.append(price)

            print(f"  前端報表展開後每日價格樣本數: {frontend_daily_samples}")
            print(f"  前端報表唯一競爭者數量: {frontend_unique_comps}")
            if frontend_weekday_prices:
                print(f"  前端報表平日平均價格 (展開後): ${statistics.mean(frontend_weekday_prices):.2f}")
            if frontend_weekend_prices:
                print(f"  前端報表週末平均價格 (展開後): ${statistics.mean(frontend_weekend_prices):.2f}")
        else:
            print("前端報表存在，但 result_summary 為空。")
    else:
        print("資料庫中沒有找到今日的前端報表 (pricing_reports)。")

    print(f"\n--- ML Training Data Diagnostics ---")
    print(f"  Total rows     : {len(training_df)}")
    print(f"  Holiday support: {'on (' + str(_HOLIDAY_COUNTRY) + ')' if _HOLIDAYS_AVAILABLE else 'off (fallback — pip install holidays)'}")

    if "airbnb_listing_id" in training_df.columns:
        print(f"  Unique comps   : {training_df['airbnb_listing_id'].nunique()}")

    # Source balance
    if "row_source" in training_df.columns:
        for src, grp in training_df.groupby("row_source", sort=True):
            print(f"  Source '{src}': {len(grp)} rows")
    else:
        print("  Source field not present (row_source missing).")

    # Date coverage
    if "price_date" in training_df.columns and not training_df.empty:
        n_unique = training_df["price_date"].nunique()
        print(f"  Date coverage  : {n_unique} unique dates  ({training_df['price_date'].min()} → {training_df['price_date'].max()})")

    # Observation recency
    if "observation_date" in training_df.columns and not training_df.empty:
        today_d = date.today()
        obs_series = pd.to_datetime(training_df["observation_date"], errors="coerce").dt.date
        days_old = obs_series.apply(
            lambda d: (today_d - d).days if isinstance(d, date) else 0
        ).fillna(0)
        print(f"  Obs recency    : mean={days_old.mean():.1f}d  oldest={int(days_old.max())}d")

    if not training_df.empty and "is_weekend" in training_df.columns and TARGET_COLUMN_NAME in training_df.columns:
        weekday_avg_ml = training_df[training_df["is_weekend"] == 0][TARGET_COLUMN_NAME].mean()
        weekend_avg_ml = training_df[training_df["is_weekend"] == 1][TARGET_COLUMN_NAME].mean()
        if pd.notna(weekday_avg_ml):
            print(f"  Weekday avg    : ${weekday_avg_ml:.2f}")
        if pd.notna(weekend_avg_ml):
            print(f"  Weekend avg    : ${weekend_avg_ml:.2f}")
    print("--------------------------------------------------")

    # 如果有新鮮報表且不強制訓練，則跳過訓練和預測
    if is_report_fresh and not force_train:
        print(f"狀態: 房源 ID {listing['id']} 已有今日報表存在，且未強制訓練，跳過 ML 預測。")
        return # 直接結束流程

    # 如果資料庫中沒有競爭者資料，直接呼叫專案既有的爬蟲流程
    if training_df.empty:
        print(f"狀態: 房源 {listing['id']} 尚無競爭者資料，正在啟動專案爬蟲流程...")
        
        # 取得專案根目錄 (airahost)，確保爬蟲指令能在正確的環境下執行
        project_root = Path(__file__).resolve().parents[1]
        
        print(f"提示: ML 查詢 ID {listing['id']} 的訓練資料時回傳為空。")
        # 呼叫專案既有的 worker 模組進行爬取。
        # 這是維持原定專案流程的做法：不重寫邏輯，直接執行專案核心的 scrape-comparables 指令。
        try:
            subprocess.run(
                [sys.executable, "-m", "worker.main", "scrape-comparables", "--id", listing["id"]],
                cwd=str(project_root),
                check=True
            )
            print("狀態: 爬蟲作業執行成功。")
            
            # 爬蟲完成後，重新獲取資料，這時資料庫應該已經由 worker 填入資料了
            training_df = fetch_training_dataset(client, saved_listing_id=listing["id"])
            if training_df.empty:
                print("錯誤: 爬蟲已執行但資料庫仍無資料，請檢查該房源在 Airbnb 上是否有效。")
                return
        except Exception as e:
            print(f"錯誤: 無法啟動專案爬蟲模組，請檢查 worker 配置。細節: {e}")
            return

    print(f"狀態: 已載入 {len(training_df)} 筆訓練樣本 (含歷史價格波動)。")
    
    # 輸出訓練集 CSV 供你檢查，這會包含所有從資料庫讀到的欄位
    raw_data_path = f"ml/reports/training_data_dump_{listing['id']}.csv"
    training_df.to_csv(raw_data_path, index=False)
    print(f"狀態: 原始訓練資料已匯出至 {raw_data_path}，請開啟此檔案檢查欄位與價格是否正確。")

    # 輸出特徵工程後的矩陣 (X + y)，這包含 One-hot 編碼與設施展開後的最終訓練資料樣子
    processed_matrix_df = build_feature_matrix_df(training_df)
    processed_path = f"ml/reports/processed_feature_matrix_{listing['id']}.csv"
    processed_matrix_df.to_csv(processed_path, index=False)
    print(f"狀態: 特徵工程後的訓練矩陣已匯出至 {processed_path}。")

    # 3. 模型處理邏輯
    # 判斷是否需要重新訓練，或是直接從硬碟載入已訓練好的 JSON 模型
    if force_train or not os.path.exists(model_file):
        print("狀態: 重新訓練模式 - 正在從資料庫學習市場規律...")
        model, feature_columns, importances, metrics = train_model(training_df)
        plot_feature_importance(importances)
        update_metrics_history(listing["id"], metrics, len(training_df))

        # Post-training diagnostics
        print(f"\n--- Model Training Summary ---")
        print(f"  Validation : {metrics.get('cv_strategy', 'unknown')}")
        if not metrics.get("cv_time_safe", True):
            print("  Warning    : degraded validation — Q2/MAE may be overoptimistic.")
        hs = metrics.get("holiday_support", {})
        print(f"  Holidays   : {'on (' + str(hs.get('country')) + ')' if hs.get('available') else 'off (fallback)'}")
        ws = metrics.get("weight_stats", {})
        if "by_source" in ws:
            for src, info in sorted(ws["by_source"].items()):
                print(f"  Weight [{src}]: n={info['n']}  mean={info['mean_weight']:.3f}  max={info['max_weight']:.3f}")
        dc = metrics.get("date_coverage", {})
        if "min_date" in dc:
            print(f"  Dates      : {dc['unique_dates']} unique  ({dc['min_date']} → {dc['max_date']})")
        print("--------------------------------------------------")

        # 存下交叉驗證詳細細節
        cv_path = f"ml/reports/cv_details_{listing['id']}_{today_iso}.csv"
        pd.DataFrame(metrics["fold_details"]).to_csv(cv_path, index=False)
        print(f"狀態: 交叉驗證詳細數據已存至 {cv_path}")

        model.save_model(model_file)
        print(f"訓練完成! 模型已存至 {model_file}")
    else:
        print(f"狀態: 直接預測模式 - 正在載入現有模型: {model_file}")
        # 即使是載入模型，我們仍需透過 build_feature_matrix_df 獲取特徵欄位清單(含 One-hot 欄位)
        matrix = build_feature_matrix_df(training_df)
        feature_columns = [c for c in matrix.columns if c != "last_nightly_price"]
        
        model = XGBRegressor()
        try:
            model.load_model(model_file)
            # 檢查模型特徵數量是否匹配
            if len(model.feature_names_in_) != len(feature_columns):
                raise ValueError("模型特徵數量不匹配")
            print(f"狀態: 直接預測模式 - 成功載入現有模型: {model_file}")
        except Exception as e:
            print(f"警告: 現有模型與當前特徵不相符 ({e})。正在強制重新訓練...")
            model, feature_columns, importances, metrics = train_model(training_df)
            plot_feature_importance(importances)
            update_metrics_history(listing["id"], metrics, len(training_df))
            
            # 存下交叉驗證詳細細節
            cv_path = f"ml/reports/cv_details_{listing['id']}_{today_iso}.csv"
            pd.DataFrame(metrics["fold_details"]).to_csv(cv_path, index=False)
            print(f"狀態: 交叉驗證詳細數據已存至 {cv_path}")

            model.save_model(model_file)
            print(f"重新訓練完成! 模型已更新。")

    # 4. 預測準備
    # 注入目標房源的精確位置與設施，讓模型知道是在「哪個地區」預測
    target_listing_features["lat"] = listing.get("target_lat") or 0.0
    target_listing_features["lng"] = listing.get("target_lng") or 0.0

    # 提取設施特徵
    input_attrs = listing.get("input_attributes") or {}
    target_amenities = input_attrs.get("amenities") or []
    if isinstance(target_amenities, list):
        for a in AMENITIES_LIST:
            target_listing_features[f"has_{a}"] = 1.0 if a in target_amenities else 0.0

    # 如果目標房源有缺失的數值特徵，則使用訓練集的平均值/中位數補齊
    defaults = build_default_numeric_features(training_df)
    for key, val in defaults.items():
        if key not in target_listing_features:
            target_listing_features[key] = val

    # 5. 執行未來三十天預測
    # 治本：改為先建構 30 筆 X 資料樣子，存成 CSV 供你檢查
    forecast_rows_x = []
    start_dt = date.today()
    for i in range(30):
        current_date = start_dt + timedelta(days=i)
        day_features = target_listing_features.copy()
        date_feats = _compute_date_features(current_date, start_dt)
        day_features.update(date_feats)

        row_df = build_target_row(day_features, feature_columns)
        row_df["date"] = current_date.isoformat()
        forecast_rows_x.append(row_df)

    # 合併為預測矩陣
    X_forecast_all = pd.concat(forecast_rows_x, ignore_index=True)
    
    # 執行預測並套用 guardrail (cap ±30% day-to-day swings)
    preds_y_raw = np.expm1(model.predict(X_forecast_all.drop(columns=["date"])))
    preds_y_bounded = _apply_forecast_guardrail(preds_y_raw.tolist())
    guardrail_flags = [abs(b - r) > 0.01 for b, r in zip(preds_y_bounded, preds_y_raw)]
    X_forecast_all["predicted_price_raw"] = preds_y_raw
    X_forecast_all["predicted_price"] = preds_y_bounded
    X_forecast_all["guardrail_applied"] = guardrail_flags
    n_guardrailed = sum(guardrail_flags)
    if n_guardrailed:
        print(f"[ML] Forecast guardrail clipped {n_guardrailed}/30 day(s) (±30% window median cap).")

    # 存下用來預測的 X 和 吐出的 Y，讓你檢查模型是怎麼算的
    forecast_input_path = f"ml/reports/prediction_input_matrix_{listing['id']}.csv"
    X_forecast_all.to_csv(forecast_input_path, index=False)
    print(f"狀態: 預測專用的 30 天特徵矩陣(X+Y)已匯出至 {forecast_input_path}")

    # 轉換格式供報表產出使用
    forecast_data = X_forecast_all.to_dict(orient="records")

    # 6. 報表產出
    # 將結果轉換為 DataFrame 並儲存為 CSV，同時在控制台顯示
    report_df = pd.DataFrame(forecast_data)
    
    # 檔名包含房源 ID 與日期，避免多次執行時被覆蓋
    today_str = date.today().isoformat()
    output_path = f"ml/reports/forecast_{listing['id']}_{today_str}.csv"
    report_df.to_csv(output_path, index=False)
    
    print(f"\n批次處理成功! 建議報表已存至: {output_path}")
    print("前五日預覽:")
    print(report_df.head())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AiraHost ML 批次處理工具")
    parser.add_argument(
        "--listing-url", 
        default="https://www.airbnb.com/rooms/1623403688220154475",
        help="要預測的 Airbnb 房源 URL"
    )
    parser.add_argument(
        "--retrain", 
        action="store_true", 
        help="強制重新訓練模型，即使已有存檔"
    )
    args = parser.parse_args()
    
    execute_batch_workflow(args.listing_url, force_train=args.retrain)
    print("\n[提示] 如需查看特徵權重排名，請檢查 ml/reports/feature_importance.png")