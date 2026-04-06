from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from ml.data import (
    extract_listing_features,
    fetch_saved_listing_by_id,
    fetch_saved_listing_by_url,
    fetch_training_dataset,
)
from ml.model import (
    build_default_numeric_features,
    build_feature_description_df,
    build_feature_matrix_df,
    forecast_prices,
    train_model,
)
from ml.supabase_client import get_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "ml" / "reports"
FALLBACK_SAVED_LISTING_ID = "bdef28dc-2134-40b8-875d-350f7c28a0fe"


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def resolve_repo_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def get_default_saved_listing_id() -> str:
    return os.getenv("ML_DEFAULT_SAVED_LISTING_ID", FALLBACK_SAVED_LISTING_ID)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train an XGB model from Supabase and export 30-day price suggestions.")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--listing-url", help="Airbnb URL stored in saved_listings.input_attributes.listingUrl")
    group.add_argument(
        "--saved-listing-id",
        help="UUID of a saved listing in Supabase. If omitted, uses ML_DEFAULT_SAVED_LISTING_ID or the built-in default.",
    )
    parser.add_argument("--output", default=str(REPORTS_DIR / "predictions.csv"), help="CSV output path")
    parser.add_argument("--horizon", type=int, default=30, help="Forecast horizon in days")
    parser.add_argument("--start-date", help="Forecast start date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--limit", type=int, default=5000, help="Max training rows to load from Supabase")
    parser.add_argument(
        "--dump-training-csv",
        help="Write the training feature matrix and target values to a CSV file.",
    )
    parser.add_argument(
        "--dump-feature-csv",
        help="Write a CSV describing each model feature.",
    )
    parser.add_argument(
        "--dump-importance-csv",
        help="Write a CSV containing feature importance scores.",
    )
    parser.add_argument(
        "--dump-metrics-csv",
        help="Write training metrics (MAE, time) to a CSV file.",
    )
    parser.add_argument(
        "--save-model",
        help="Save the trained XGBoost model to a file (JSON format).",
    )
    parser.add_argument(
        "--train-only",
        action="store_true",
        help="Train the model and optionally save it, then exit without generating forecast output.",
    )
    return parser.parse_args()


def build_forecast_df(predictions_data: List[Dict[str, Any]], start_date: date) -> pd.DataFrame:
    rows = []
    for item in predictions_data:
        # 確保日期格式正確，價格四捨五入，並包含 is_weekend 和 is_holiday 旗標
        rows.append({
            "date": item["date"],
            "predicted_price": round(item["predicted_price"], 2),
            "is_weekend": item["is_weekend"],
            "is_holiday": item["is_holiday"],
        })
    return pd.DataFrame(rows)


def print_text_report(forecast_df: pd.DataFrame) -> None:
    """以 ASCII 表格形式在控制台輸出預測報表。"""
    print("\n" + " " * 12 + "=== 30-DAY PRICING FORECAST ===")
    print("-" * 62)
    print(f"{'Date':<12} | {'Day':<10} | {'Category':<12} | {'Suggested Price':>15}")
    print("-" * 62)
    for _, row in forecast_df.iterrows():
        dt = date.fromisoformat(row["date"])
        day_name = dt.strftime("%A")
        # 邏輯與 model.py 一致：週五 (4) 與 週六 (5) 視為週末溢價日
        is_we = row["is_weekend"] # 從 DataFrame 取得 is_weekend 旗標
        is_holiday = row["is_holiday"] # 從 DataFrame 取得 is_holiday 旗標
        tag = "HOLIDAY 🎄" if is_holiday else ("WEEKEND ★" if is_we else "Weekday")
        price = f"${row['predicted_price']:,.2f}"
        print(f"{row['date']:<12} | {day_name:<10} | {tag:<12} | {price:>15}")
    print("-" * 62)

    avg_total = forecast_df["predicted_price"].mean()
    avg_weekday = forecast_df[forecast_df["is_weekend"] == False]["predicted_price"].mean()
    avg_weekend = forecast_df[forecast_df["is_weekend"] == True]["predicted_price"].mean()

    print(f"{'Average (Total):':<25} ${avg_total:>15,.2f}")
    if pd.notna(avg_weekday):
        print(f"{'Average (Weekday):':<25} ${avg_weekday:>15,.2f}")
    if pd.notna(avg_weekend):
        print(f"{'Average (Weekend):':<25} ${avg_weekend:>15,.2f}")

    if pd.notna(avg_weekday) and pd.notna(avg_weekend) and avg_weekday != 0:
        diff = avg_weekend - avg_weekday
        pct = (diff / avg_weekday) * 100
        print(f"{'Weekend Premium:':<25} {f'${diff:+,.2f} ({pct:+.1f}%)':>16}")
    print("")


def main() -> None:
    configure_console_encoding()
    args = parse_args()
    client = get_client()
    default_saved_listing_id = get_default_saved_listing_id()

    saved_listing = None
    if args.listing_url:
        saved_listing = fetch_saved_listing_by_url(client, args.listing_url)
        if not saved_listing:
            raise ValueError(f"Cannot find saved listing with URL: {args.listing_url}")

    if args.saved_listing_id:
        saved_listing = fetch_saved_listing_by_id(client, args.saved_listing_id)
        if not saved_listing:
            raise ValueError(f"Cannot find saved listing with id: {args.saved_listing_id}")

    if not saved_listing:
        if default_saved_listing_id:
            print(f"Using default saved listing id: {default_saved_listing_id}")
            saved_listing = fetch_saved_listing_by_id(client, default_saved_listing_id)
            if not saved_listing:
                raise ValueError(
                    f"Default saved listing id {default_saved_listing_id} was not found in Supabase."
                )

    if not saved_listing:
        raise ValueError(
            "No saved listing specified and no default is configured. "
            "Set --listing-url, --saved-listing-id, or ML_DEFAULT_SAVED_LISTING_ID."
        )

    target_features = extract_listing_features(saved_listing)
    training_df = fetch_training_dataset(
        client,
        saved_listing_id=saved_listing["id"],
        limit=args.limit,
    )

    if training_df.empty:
        raise RuntimeError(
            "No comparable pool entries available for training on this saved listing. "
            "Check that comparable_pool_entries exist for the listing in Supabase."
        )

    if args.dump_training_csv:
        training_matrix_df = build_feature_matrix_df(training_df)
        training_csv_path = resolve_repo_path(args.dump_training_csv)
        training_csv_path.parent.mkdir(parents=True, exist_ok=True)
        training_matrix_df.to_csv(training_csv_path, index=False)
        print(f"Wrote training feature matrix to {training_csv_path.resolve()}")

    if args.dump_feature_csv:
        training_matrix_df = build_feature_matrix_df(training_df)
        feature_description_df = build_feature_description_df(list(training_matrix_df.columns))
        feature_csv_path = resolve_repo_path(args.dump_feature_csv)
        feature_csv_path.parent.mkdir(parents=True, exist_ok=True)
        feature_description_df.to_csv(feature_csv_path, index=False)
        print(f"Wrote feature description CSV to {feature_csv_path.resolve()}")

    start_train = time.time()
    model, feature_columns, importances, metrics = train_model(training_df)
    train_duration = time.time() - start_train

    print(f"Trained XGBoost model on {len(training_df)} comparable rows.")

    if args.dump_metrics_csv:
        metrics_df = pd.DataFrame([{
            "timestamp": datetime.now().isoformat(),
            "mae": round(metrics["mae"], 4),
            "mae_std": round(metrics.get("mae_std", 0.0), 4),
            "mape": round(metrics["mape"], 6),
            "q2": round(metrics["q2"], 6),
            "r2": round(metrics["r2"], 6),
            "r2_std": round(metrics.get("r2_std", 0.0), 6),
            "training_time_seconds": round(train_duration, 4),
            "n_samples": len(training_df)
        }])
        metrics_path = resolve_repo_path(args.dump_metrics_csv)
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_df.to_csv(metrics_path, index=False)
        print(f"Wrote training metrics to {metrics_path.resolve()}")

    if args.dump_importance_csv:
        importance_df = importances.reset_index()
        importance_df.columns = ["feature", "importance"]
        importance_path = resolve_repo_path(args.dump_importance_csv)
        importance_path.parent.mkdir(parents=True, exist_ok=True)
        importance_df.to_csv(importance_path, index=False)
        print(f"Wrote feature importance CSV to {importance_path.resolve()}")

    if args.save_model:
        model_path = resolve_repo_path(args.save_model)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(model_path))
        print(f"Saved trained model to {model_path.resolve()}")

    if args.train_only:
        return
    default_numeric = build_default_numeric_features(training_df)
    for key, default_value in default_numeric.items():
        if key not in target_features:
            target_features[key] = default_value

    start = date.fromisoformat(args.start_date) if args.start_date else date.today()
    
    predictions_data = forecast_prices( # 變數名稱變更以反映新的返回類型
        model, 
        feature_columns, 
        target_features, 
        start_date=start, 
        horizon=args.horizon
    )

    forecast_df = build_forecast_df(predictions_data, start) # 傳遞新的資料結構

    out_path = resolve_repo_path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    forecast_df.to_csv(out_path, index=False)

    print(f"Saved forecast CSV to {out_path.resolve()}")

    # 在控制台印出純文字版報表
    print_text_report(forecast_df)


if __name__ == "__main__":
    main()
