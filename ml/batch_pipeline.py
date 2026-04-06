"""
AiraHost ML 批次處理與開發參考程式

本程式示範了完整的 ML 工作流，可作為自動化批次腳本使用，也可作為前端對接 ML 功能的範本。
流程包含：
1. 初始化 Supabase 連線。
2. 獲取目標房源與競爭者歷史資料。
3. 模型管理：支援「直接預測」或「重新訓練」。
4. 產出未來 30 天的報價建議報表。
"""

import argparse
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

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
    build_temporal_feature_values
)
from ml.model import (
    train_model, 
    build_default_numeric_features,
    build_feature_matrix_df,
    build_feature_description_df,
    build_target_row, # 導入目標列建構器
    AMENITIES_LIST,
    build_feature_importance_report,
    build_prediction_explanation_frames,
    write_model_tree_dump,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "ml" / "reports"
FALLBACK_SAVED_LISTING_ID = "bdef28dc-2134-40b8-875d-350f7c28a0fe"
MANIFEST_FILENAME = "batch_pipeline_result.json"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _report_path(filename: str) -> Path:
    return REPORTS_DIR / filename


def get_default_saved_listing_id() -> str:
    return os.getenv("ML_DEFAULT_SAVED_LISTING_ID", FALLBACK_SAVED_LISTING_ID)


def _resolve_saved_listing(
    client,
    *,
    listing_url: Optional[str] = None,
    saved_listing_id: Optional[str] = None,
) -> Dict[str, Any]:
    listing = None

    if listing_url:
        listing = fetch_saved_listing_by_url(client, listing_url)
        if not listing:
            raise ValueError(f"Cannot find saved listing with URL: {listing_url}")

    if saved_listing_id:
        listing = fetch_saved_listing_by_id(client, saved_listing_id)
        if not listing:
            raise ValueError(f"Cannot find saved listing with id: {saved_listing_id}")

    if not listing:
        default_saved_listing_id = get_default_saved_listing_id()
        print(f"狀態: 未指定 listing，使用預設 saved listing id: {default_saved_listing_id}")
        listing = fetch_saved_listing_by_id(client, default_saved_listing_id)
        if not listing:
            raise ValueError(
                f"Default saved listing id {default_saved_listing_id} was not found in Supabase."
            )

    return listing


def _write_metrics_snapshot(
    path: Path,
    *,
    listing_id: str,
    model_mode: str,
    trained_now: bool,
    metrics: Optional[Dict[str, Any]],
    n_samples: int,
) -> None:
    row: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "listing_id": listing_id,
        "model_mode": model_mode,
        "trained_now": trained_now,
        "n_samples": n_samples,
    }
    if metrics:
        row.update({
            "mae": round(metrics["mae"], 4),
            "mae_std": round(metrics.get("mae_std", 0.0), 4),
            "mape": round(metrics["mape"], 6),
            "q2": round(metrics["q2"], 6),
            "r2": round(metrics["r2"], 6),
            "r2_std": round(metrics.get("r2_std", 0.0), 6),
        })
    pd.DataFrame([row]).to_csv(path, index=False)
    print(f"狀態: 最新訓練摘要已存至 {_display_path(path)}")


def _validate_batch_outputs(artifacts: Dict[str, str]) -> None:
    predictions_path = Path(artifacts["predictions_latest"])
    training_matrix_path = Path(artifacts["training_matrix_latest"])
    metrics_path = Path(artifacts["metrics_latest"])

    for path in [predictions_path, training_matrix_path, metrics_path]:
        if not path.exists():
            raise RuntimeError(f"Expected artifact was not created: {path}")

    predictions_df = pd.read_csv(predictions_path)
    expected_prediction_columns = {"date", "predicted_price", "is_weekend", "is_holiday"}
    if not expected_prediction_columns.issubset(predictions_df.columns):
        raise RuntimeError("predictions.csv is missing expected forecast columns.")
    if predictions_df.empty:
        raise RuntimeError("predictions.csv is empty.")

    training_df = pd.read_csv(training_matrix_path)
    expected_training_columns = {
        "debug_source_type",
        "debug_price_date",
        "debug_observed_at_date",
        TARGET_COLUMN_NAME,
    }
    if not expected_training_columns.issubset(training_df.columns):
        raise RuntimeError("training_matrix.csv is missing expected debug columns.")
    if training_df.empty:
        raise RuntimeError("training_matrix.csv is empty.")

    metrics_df = pd.read_csv(metrics_path)
    if metrics_df.empty:
        raise RuntimeError("metrics_latest.csv is empty.")


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def plot_feature_importance(importances: pd.Series):
    """將特徵重要性繪製成圖表並儲存，便於開發者確認模型邏輯。"""
    output_path = _report_path("feature_importance.png")
    try:
        plt.figure(figsize=(10, 8))
        importances.sort_values().plot(kind='barh', color='skyblue')
        plt.title('XGBoost Feature Importance')
        plt.xlabel('Importance Score')
        plt.grid(axis='x', linestyle='--', alpha=0.7)
        plt.tight_layout()
        plt.savefig(output_path)
        print(f"狀態: 特徵重要性圖表已儲存至 {_display_path(output_path)}")
    except Exception as e:
        print(f"警告: 繪製圖表時發生錯誤: {e}")

import statistics # 導入 statistics 模組用於平均值計算
def update_metrics_history(listing_id: str, metrics: dict, n_samples: int):
    """將訓練指標記錄到 CSV 中，以便長期追蹤模型表現。"""
    metrics_path = _report_path("metrics_history.csv")
    new_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "listing_id": listing_id,
        "mae": round(metrics["mae"], 2),
        "mae_std": round(metrics.get("mae_std", 0), 2),
        "mape": round(metrics["mape"], 4),
        "r2": round(metrics["r2"], 4),
        "q2": round(metrics["q2"], 4),
        "r2_std": round(metrics.get("r2_std", 0), 4),
        "n_samples": n_samples
    }
    
    df = pd.DataFrame([new_entry])
    # 如果檔案不存在則寫入標題，否則直接附加在後方
    if not metrics_path.exists():
        df.to_csv(metrics_path, index=False)
    else:
        df.to_csv(metrics_path, mode='a', header=False, index=False)
    print(f"狀態: 訓練指標已更新至 {_display_path(metrics_path)}")

def execute_batch_workflow(
    *,
    listing_url: Optional[str] = None,
    saved_listing_id: Optional[str] = None,
    force_train: bool = True,
    smoke_test: bool = False,
) -> Dict[str, Any]:
    listing_url = saved_listing_id or listing_url or get_default_saved_listing_id()
    print(f"--- 啟動 AiraHost ML 批次流程: {listing_url} ---")
    
    # 0. 確保報告資料夾存在
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 環境初始化
    client = get_client()
    model_file = _report_path("saved_model.json")
    latest_training_dump_path = _report_path("training_data_dump.csv")
    training_matrix_latest_path = _report_path("training_matrix.csv")
    feature_descriptions_path = _report_path("feature_descriptions.csv")
    feature_importance_csv_path = _report_path("feature_importance.csv")
    feature_importance_detail_path = _report_path("feature_importance_detailed.csv")
    metrics_latest_path = _report_path("metrics_latest.csv")
    predictions_latest_path = _report_path("predictions.csv")
    prediction_summary_path = _report_path("prediction_explanations.csv")
    prediction_contribs_path = _report_path("prediction_feature_contributions.csv")
    model_tree_dump_path = _report_path("model_tree_dump.txt")
    manifest_path = _report_path(MANIFEST_FILENAME)

    # 2. 資料獲取
    # 取得房源本身的物理特徵 (如臥室數量、床位等)
    listing = _resolve_saved_listing(
        client,
        listing_url=listing_url if listing_url and listing_url.startswith("http") else None,
        saved_listing_id=saved_listing_id or (listing_url if listing_url and not listing_url.startswith("http") else None),
    )
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

    print(f"\nML 訓練資料集 (training_df) 數據:")
    print(f"  ML 訓練樣本總數: {len(training_df)}")
    if "airbnb_listing_id" in training_df.columns:
        print(f"  ML 訓練集唯一競爭者數量: {training_df['airbnb_listing_id'].nunique()}")
    
    if not training_df.empty and "is_weekend" in training_df.columns and TARGET_COLUMN_NAME in training_df.columns:
        weekday_avg_ml = training_df[training_df["is_weekend"] == 0][TARGET_COLUMN_NAME].mean()
        weekend_avg_ml = training_df[training_df["is_weekend"] == 1][TARGET_COLUMN_NAME].mean()
        if pd.notna(weekday_avg_ml):
            print(f"  ML 訓練集平日平均價格: ${weekday_avg_ml:.2f}")
        if pd.notna(weekend_avg_ml):
            print(f"  ML 訓練集週末平均價格: ${weekend_avg_ml:.2f}")
    else:
        print(f"  ML 訓練資料中缺少必要欄位 ('is_weekend' 或 '{TARGET_COLUMN_NAME}') 或資料為空，無法進行平日/週末統計。")
    print("--------------------------------------------------")

    # 如果有新鮮報表且不強制訓練，則跳過訓練和預測
    if False and is_report_fresh and not force_train:
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
    raw_data_path = _report_path(f"training_data_dump_{listing['id']}.csv")
    training_df.to_csv(raw_data_path, index=False)
    training_df.to_csv(latest_training_dump_path, index=False)
    print(f"狀態: 原始訓練資料已匯出至 {_display_path(raw_data_path)}，請開啟此檔案檢查欄位與價格是否正確。")

    # 輸出特徵工程後的矩陣 (X + y)，這包含 One-hot 編碼與設施展開後的最終訓練資料樣子
    processed_matrix_df = build_feature_matrix_df(training_df)
    processed_path = _report_path(f"processed_feature_matrix_{listing['id']}.csv")
    processed_matrix_df.to_csv(processed_path, index=False)
    processed_matrix_df.to_csv(training_matrix_latest_path, index=False)
    build_feature_description_df(list(processed_matrix_df.columns)).to_csv(feature_descriptions_path, index=False)
    print(f"狀態: 特徵工程後的訓練矩陣已匯出至 {_display_path(processed_path)}。")

    # 診斷：檢查訓練集是否包含週末資料並與前端報表對比
    if not training_df.empty and "is_weekend" in training_df.columns:
        # 確保 TARGET_COLUMN_NAME 在 training_df 中
        if "last_nightly_price" in training_df.columns:
            weekday_avg_ml = training_df[training_df["is_weekend"] == 0]["last_nightly_price"].mean()
            weekend_avg_ml = training_df[training_df["is_weekend"] == 1]["last_nightly_price"].mean()
            
            print(f"\n--- ML 訓練資料集統計 ---")
            print(f"訓練樣本總數: {len(training_df)}")
            if pd.notna(weekday_avg_ml):
                print(f"訓練集平日平均價格: ${weekday_avg_ml:.2f}")
            if pd.notna(weekend_avg_ml):
                print(f"訓練集週末平均價格: ${weekend_avg_ml:.2f}")
            print(f"--------------------------------------------------")
        else:
            print("警告: 訓練資料中缺少 'last_nightly_price' 欄位，無法進行平日/週末統計。")
    else:
        print("警告: 訓練資料中沒有 'is_weekend' 欄位，或資料為空。無法進行平日/週末統計。")

    # 3. 模型處理邏輯
    # 判斷是否需要重新訓練，或是直接從硬碟載入已訓練好的 JSON 模型
    trained_now = False
    model_mode = "retrain"
    metrics = None
    if force_train or not model_file.exists():
        print("狀態: 重新訓練模式 - 正在從資料庫學習市場規律...")
        model, feature_columns, importances, metrics = train_model(training_df)
        trained_now = True
        plot_feature_importance(importances)
        update_metrics_history(listing["id"], metrics, len(training_df))
        
        # 存下交叉驗證詳細細節
        cv_path = _report_path(f"cv_details_{listing['id']}_{today_iso}.csv")
        pd.DataFrame(metrics["fold_details"]).to_csv(cv_path, index=False)
        print(f"狀態: 交叉驗證詳細數據已存至 {_display_path(cv_path)}")

        model.save_model(str(model_file))
        print(f"訓練完成! 模型已存至 {_display_path(model_file)}")
    else:
        model_mode = "reuse_model"
        print(f"狀態: 直接預測模式 - 正在載入現有模型: {_display_path(model_file)}")
        # 即使是載入模型，我們仍需透過 build_feature_matrix_df 獲取特徵欄位清單(含 One-hot 欄位)
        matrix = build_feature_matrix_df(training_df)
        feature_columns = [
            c for c in matrix.columns
            if c != "last_nightly_price" and not c.startswith("debug_")
        ]
        
        model = XGBRegressor()
        try:
            model.load_model(str(model_file))
            # 檢查模型特徵數量是否匹配
            if len(model.feature_names_in_) != len(feature_columns):
                raise ValueError("模型特徵數量不匹配")
            print(f"狀態: 直接預測模式 - 成功載入現有模型: {_display_path(model_file)}")
        except Exception as e:
            print(f"警告: 現有模型與當前特徵不相符 ({e})。正在強制重新訓練...")
            model, feature_columns, importances, metrics = train_model(training_df)
            trained_now = True
            model_mode = "retrain_after_mismatch"
            plot_feature_importance(importances)
            update_metrics_history(listing["id"], metrics, len(training_df))
            
            # 存下交叉驗證詳細細節
            cv_path = _report_path(f"cv_details_{listing['id']}_{today_iso}.csv")
            pd.DataFrame(metrics["fold_details"]).to_csv(cv_path, index=False)
            print(f"狀態: 交叉驗證詳細數據已存至 {_display_path(cv_path)}")

            model.save_model(str(model_file))
            print(f"重新訓練完成! 模型已更新。")

    if not trained_now:
        importances = pd.Series(model.feature_importances_, index=feature_columns).sort_values(ascending=False)

    feature_importance_df = build_feature_importance_report(model, feature_columns)
    feature_importance_df.to_csv(feature_importance_detail_path, index=False)
    feature_importance_df.loc[:, ["feature", "importance"]].to_csv(feature_importance_csv_path, index=False)
    write_model_tree_dump(model, model_tree_dump_path)
    _write_metrics_snapshot(
        metrics_latest_path,
        listing_id=listing["id"],
        model_mode=model_mode,
        trained_now=trained_now,
        metrics=metrics,
        n_samples=len(training_df),
    )

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
    country_code = target_listing_features.get("country_code")
    for i in range(30):
        current_date = start_dt + timedelta(days=i)
        day_features = target_listing_features.copy()
        day_features.update(
            build_temporal_feature_values(
                price_date=current_date,
                observed_at_date=start_dt,
                first_seen_date=start_dt,
                country_code=country_code,
            )
        )
        
        # 轉換為模型認識的特徵矩陣樣子
        row_df = build_target_row(day_features, feature_columns)
        row_df["date"] = current_date.isoformat()
        forecast_rows_x.append(row_df)

    # 合併為預測矩陣
    X_forecast_all = pd.concat(forecast_rows_x, ignore_index=True)
    
    # 執行預測並將結果 y 填回矩陣中
    # 治本修正：將預測出的對數值還原為原始美金金額
    preds_y = np.expm1(model.predict(X_forecast_all.drop(columns=["date"])))
    X_forecast_all["predicted_price"] = preds_y

    explanation_summary_df, explanation_contribs_df = build_prediction_explanation_frames(
        model,
        X_forecast_all.drop(columns=["date", "predicted_price"]),
        metadata=X_forecast_all[["date", "predicted_price"]],
        top_n=5,
    )
    explanation_summary_df.to_csv(prediction_summary_path, index=False)
    explanation_contribs_df.to_csv(prediction_contribs_path, index=False)

    # 存下用來預測的 X 和 吐出的 Y，讓你檢查模型是怎麼算的
    forecast_input_path = _report_path(f"prediction_input_matrix_{listing['id']}.csv")
    X_forecast_all.to_csv(forecast_input_path, index=False)
    print(f"狀態: 預測專用的 30 天特徵矩陣(X+Y)已匯出至 {_display_path(forecast_input_path)}")

    # 轉換格式供報表產出使用
    forecast_data = X_forecast_all.to_dict(orient="records")

    # 6. 報表產出
    # 將結果轉換為 DataFrame 並儲存為 CSV，同時在控制台顯示
    report_df = pd.DataFrame(forecast_data)
    
    # 檔名包含房源 ID 與日期，避免多次執行時被覆蓋
    today_str = date.today().isoformat()
    output_path = _report_path(f"forecast_{listing['id']}_{today_str}.csv")
    report_df.to_csv(output_path, index=False)
    report_df.to_csv(predictions_latest_path, index=False)

    artifacts = {
        "training_dump_latest": str(latest_training_dump_path),
        "training_dump_archive": str(raw_data_path),
        "training_matrix_latest": str(training_matrix_latest_path),
        "training_matrix_archive": str(processed_path),
        "feature_descriptions": str(feature_descriptions_path),
        "feature_importance_csv": str(feature_importance_csv_path),
        "feature_importance_detailed_csv": str(feature_importance_detail_path),
        "feature_importance_plot": str(_report_path("feature_importance.png")),
        "metrics_latest": str(metrics_latest_path),
        "metrics_history": str(_report_path("metrics_history.csv")),
        "model": str(model_file),
        "model_tree_dump": str(model_tree_dump_path),
        "forecast_input_matrix": str(forecast_input_path),
        "prediction_explanations": str(prediction_summary_path),
        "prediction_feature_contributions": str(prediction_contribs_path),
        "predictions_latest": str(predictions_latest_path),
        "predictions_archive": str(output_path),
    }

    if smoke_test:
        _validate_batch_outputs(artifacts)
        print("狀態: smoke test 通過，主要輸出檔案與欄位皆已確認。")

    result_manifest: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "listing_id": listing["id"],
        "listing_name": listing.get("name"),
        "listing_url": listing_url if listing_url and listing_url.startswith("http") else None,
        "saved_listing_id": saved_listing_id or listing["id"],
        "trained_now": trained_now,
        "model_mode": model_mode,
        "smoke_test": smoke_test,
        "n_samples": len(training_df),
        "artifacts": artifacts,
    }
    if metrics:
        result_manifest["metrics"] = {
            "mae": metrics["mae"],
            "mae_std": metrics.get("mae_std"),
            "mape": metrics["mape"],
            "q2": metrics["q2"],
            "r2": metrics["r2"],
            "r2_std": metrics.get("r2_std"),
        }
    manifest_path.write_text(
        json.dumps(result_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    print(f"\n批次處理成功! 建議報表已存至: {_display_path(output_path)}")
    print("前五日預覽:")
    print(report_df.head())
    print(f"最新批次輸出摘要: {_display_path(manifest_path)}")
    return result_manifest

if __name__ == "__main__":
    configure_console_encoding()
    parser = argparse.ArgumentParser(description="AiraHost ML 批次處理工具")
    parser.add_argument(
        "--listing-url", 
        default=None,
        help="要預測的 Airbnb 房源 URL"
    )
    parser.add_argument(
        "--retrain", 
        action="store_true", 
        help="強制重新訓練模型，即使已有存檔"
    )
    parser.add_argument(
        "--saved-listing-id",
        default=None,
        help="saved_listings çš„ UUID"
    )
    parser.add_argument(
        "--reuse-model",
        dest="retrain",
        action="store_false",
        help="å¦‚æžœå·²æœ‰ saved_model.jsonï¼Œç›´æŽ¥è¼‰å…¥æ¨¡åž‹ä¾†é æ¸¬"
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="åŸ·è¡Œå®Œæ•´æ‰¹æ¬¡æµç¨‹å¾Œï¼Œé¡å¤–æª¢æŸ¥è¼¸å‡ºæª”æ¡ˆèˆ‡æ ¸å¿ƒæ¬„ä½"
    )
    parser.set_defaults(retrain=True)
    args = parser.parse_args()
    
    execute_batch_workflow(
        listing_url=args.listing_url,
        saved_listing_id=args.saved_listing_id,
        force_train=args.retrain,
        smoke_test=args.smoke_test,
    )
    print(f"\n[提示] 如需查看特徵權重排名，請檢查 {_display_path(_report_path('feature_importance.png'))}")
