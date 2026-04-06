from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score

from ml.data import (
    TARGET_COLUMN_NAME,
    fetch_saved_listing_by_id,
    fetch_saved_listing_by_url,
    fetch_training_dataset,
)
from ml.model import _clean_training_frame, build_feature_matrix_df, train_model
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


def get_default_holdout_comp_id() -> Optional[str]:
    value = os.getenv("ML_BACKTEST_HOLDOUT_COMP_ID")
    if not value:
        return None
    value = value.strip()
    return value or None


def _looks_like_uuid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backtest the market-pricing model by holding out one training-house "
            "and comparing predicted future prices with the real observed prices."
        )
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--listing-url", help="Airbnb URL stored in saved_listings.input_attributes.listingUrl")
    group.add_argument(
        "--saved-listing-id",
        help="UUID of a saved listing in Supabase. If omitted, uses ML_DEFAULT_SAVED_LISTING_ID or the built-in default.",
    )
    parser.add_argument(
        "--holdout-comp-id",
        help="Specific comparable house id (airbnb_listing_id) to hold out for backtest.",
    )
    parser.add_argument(
        "--split-mode",
        choices=["holdout_comp", "time"],
        default="holdout_comp",
        help="Backtest mode: hold out one house, or use observed_at_date time split.",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=5,
        help="Minimum number of observed rows required for an auto-selected holdout house.",
    )
    parser.add_argument(
        "--validation-days",
        type=int,
        default=7,
        help="When split-mode=time, use the latest N observed_at_date days as validation.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Max training rows to load from Supabase before selecting the holdout house.",
    )
    parser.add_argument(
        "--output",
        default=str(REPORTS_DIR / "backtest_predictions.csv"),
        help="CSV path for row-level predicted-vs-actual comparisons.",
    )
    parser.add_argument(
        "--summary-output",
        default=str(REPORTS_DIR / "backtest_summary.csv"),
        help="CSV path for aggregated backtest metrics.",
    )
    parser.add_argument(
        "--by-date-output",
        default=str(REPORTS_DIR / "backtest_by_date.csv"),
        help="CSV path for date-level aggregated prediction comparisons.",
    )
    return parser.parse_args()


def _resolve_saved_listing(client, args: argparse.Namespace) -> Dict[str, Any]:
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
        default_saved_listing_id = get_default_saved_listing_id()
        print(f"Using default saved listing id: {default_saved_listing_id}")
        saved_listing = fetch_saved_listing_by_id(client, default_saved_listing_id)
        if not saved_listing:
            raise ValueError(
                f"Default saved listing id {default_saved_listing_id} was not found in Supabase."
            )

    return saved_listing


def _pick_holdout_comp_id(
    df: pd.DataFrame,
    explicit_comp_id: Optional[str],
    min_rows: int,
    strict: bool = False,
) -> str:
    if "airbnb_listing_id" not in df.columns:
        raise RuntimeError("Training dataset is missing 'airbnb_listing_id', so holdout-house backtest cannot run.")

    candidate_counts = (
        df["airbnb_listing_id"]
        .astype(str)
        .value_counts()
        .rename_axis("airbnb_listing_id")
        .reset_index(name="row_count")
    )

    if explicit_comp_id:
        if explicit_comp_id.startswith("sb_secret_") or explicit_comp_id.startswith("sb_publishable_"):
            if strict:
                raise ValueError(
                    "Holdout comp id looks like a Supabase key, not an Airbnb comparable id."
                )
            print("[ML Backtest] Warning: ML_BACKTEST_HOLDOUT_COMP_ID looks like a Supabase key; falling back to auto-select holdout house.")
            explicit_comp_id = None
        elif _looks_like_uuid(explicit_comp_id):
            if strict:
                raise ValueError(
                    "Holdout comp id looks like a saved_listing UUID. Backtest holdout expects a training-house airbnb_listing_id."
                )
            print("[ML Backtest] Warning: ML_BACKTEST_HOLDOUT_COMP_ID looks like a saved_listing UUID; falling back to auto-select holdout house.")
            explicit_comp_id = None

    if explicit_comp_id:
        if explicit_comp_id not in set(candidate_counts["airbnb_listing_id"]):
            if strict:
                raise ValueError(f"Holdout comp id {explicit_comp_id} was not found in the training dataset.")
            print(
                f"[ML Backtest] Warning: ML_BACKTEST_HOLDOUT_COMP_ID={explicit_comp_id} was not found in the training dataset; "
                "falling back to auto-select holdout house."
            )
            explicit_comp_id = None

    if explicit_comp_id:
        return explicit_comp_id

    eligible = candidate_counts[candidate_counts["row_count"] >= min_rows]
    if eligible.empty:
        top_counts = candidate_counts.head(10).to_dict(orient="records")
        raise RuntimeError(
            f"No training houses have at least {min_rows} rows for backtest. Top candidates: {top_counts}"
        )

    return str(eligible.iloc[0]["airbnb_listing_id"])


def _prepare_holdout_features(
    holdout_df: pd.DataFrame,
    feature_columns: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cleaned_holdout_df = _clean_training_frame(holdout_df)
    holdout_matrix = build_feature_matrix_df(cleaned_holdout_df)
    feature_df = holdout_matrix.drop(
        columns=[
            column
            for column in holdout_matrix.columns
            if column == TARGET_COLUMN_NAME or column.startswith("debug_")
        ],
        errors="ignore",
    )
    feature_df = feature_df.reindex(columns=feature_columns, fill_value=0.0)
    aligned_holdout_df = cleaned_holdout_df.loc[holdout_matrix.index].copy()
    return aligned_holdout_df, feature_df


def _split_by_time_window(df: pd.DataFrame, validation_days: int) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    if "observed_at_date" not in df.columns:
        raise RuntimeError("Training dataset is missing 'observed_at_date', so time-split backtest cannot run.")

    observed_series = pd.to_datetime(df["observed_at_date"], errors="coerce")
    if observed_series.isna().all():
        raise RuntimeError("All observed_at_date values are invalid; cannot perform time split.")

    max_observed_at = observed_series.max().normalize()
    validation_start = max_observed_at - pd.Timedelta(days=max(validation_days - 1, 0))
    train_mask = observed_series < validation_start
    validation_mask = observed_series >= validation_start

    train_df = df.loc[train_mask].copy()
    validation_df = df.loc[validation_mask].copy()

    if train_df.empty or validation_df.empty:
        raise RuntimeError(
            "Time-split backtest produced an empty train or validation set. "
            f"validation_days={validation_days}, validation_start={validation_start.date()}, "
            f"max_observed_at={max_observed_at.date()}"
        )

    split_label = f"time_{validation_start.date().isoformat()}_to_{max_observed_at.date().isoformat()}"
    return train_df, validation_df, split_label


def _summarize_backtest(compare_df: pd.DataFrame, evaluation_label: str, split_mode: str) -> pd.DataFrame:
    actual = compare_df["actual_price"].astype(float)
    predicted = compare_df["predicted_price"].astype(float)
    abs_error = (predicted - actual).abs()
    abs_pct_error = compare_df["abs_error_ratio"].astype(float)
    signed_pct_error = compare_df["error_ratio"].astype(float)

    summary = {
        "split_mode": split_mode,
        "evaluation_label": evaluation_label,
        "n_rows": len(compare_df),
        "actual_mean": round(actual.mean(), 4),
        "predicted_mean": round(predicted.mean(), 4),
        "mean_signed_pct_error_pct": round(signed_pct_error.mean() * 100, 4),
        "median_signed_pct_error_pct": round(signed_pct_error.median() * 100, 4),
        "mape_pct": round(mean_absolute_percentage_error(actual, predicted) * 100, 4),
        "median_abs_pct_error_pct": round(abs_pct_error.median() * 100, 4),
        "p90_abs_pct_error_pct": round(abs_pct_error.quantile(0.9) * 100, 4),
        "within_5_pct_ratio": round((abs_pct_error <= 0.05).mean(), 4),
        "within_10_pct_ratio": round((abs_pct_error <= 0.10).mean(), 4),
        "within_20_pct_ratio": round((abs_pct_error <= 0.20).mean(), 4),
        "mae": round(mean_absolute_error(actual, predicted), 4),
        "mape": round(mean_absolute_percentage_error(actual, predicted), 6),
        "r2": round(r2_score(actual, predicted), 6) if len(compare_df) >= 2 else float("nan"),
        "median_abs_error": round(abs_error.median(), 4),
        "max_abs_error": round(abs_error.max(), 4),
        "observed_at_min": compare_df["observed_at_date"].min(),
        "observed_at_max": compare_df["observed_at_date"].max(),
        "price_date_min": compare_df["price_date"].min(),
        "price_date_max": compare_df["price_date"].max(),
    }
    return pd.DataFrame([summary])


def _build_date_level_summary(compare_df: pd.DataFrame) -> pd.DataFrame:
    latest_error_pct = (
        compare_df["error_pct"]
        if "error_pct" in compare_df.columns
        else compare_df["error_ratio"] * 100
    )
    latest_abs_error_pct = (
        compare_df["abs_error_pct"]
        if "abs_error_pct" in compare_df.columns
        else compare_df["abs_error_ratio"] * 100
    )

    grouped = (
        compare_df.groupby("price_date", as_index=False)
        .agg(
            n_rows=("actual_price", "size"),
            actual_avg_price=("actual_price", "mean"),
            predicted_avg_price=("predicted_price", "mean"),
            mean_signed_pct_error_pct=("error_ratio", lambda s: float(np.mean(s) * 100)),
            mape_pct=("abs_error_ratio", lambda s: float(np.mean(s) * 100)),
            max_abs_pct_error_pct=("abs_error_ratio", lambda s: float(np.max(s) * 100)),
            mae=("abs_error", "mean"),
            max_abs_error=("abs_error", "max"),
        )
        .sort_values("price_date")
    )

    latest_rows = (
        compare_df.assign(
            _observed_at_dt=pd.to_datetime(compare_df["observed_at_date"], errors="coerce")
        )
        .assign(
            _latest_error_pct=latest_error_pct,
            _latest_abs_error_pct=latest_abs_error_pct,
        )
        .sort_values(["price_date", "_observed_at_dt"])
        .groupby("price_date", as_index=False)
        .tail(1)
        .loc[:, [
            "price_date",
            "observed_at_date",
            "actual_price",
            "predicted_price",
            "_latest_error_pct",
            "_latest_abs_error_pct",
        ]]
        .rename(
            columns={
                "observed_at_date": "latest_observed_at_date",
                "actual_price": "actual_latest_price",
                "predicted_price": "predicted_latest_price",
                "_latest_error_pct": "latest_error_pct",
                "_latest_abs_error_pct": "latest_abs_error_pct",
            }
        )
    )

    grouped = grouped.merge(latest_rows, on="price_date", how="left")
    for column in [
        "actual_avg_price",
        "predicted_avg_price",
        "mean_signed_pct_error_pct",
        "mape_pct",
        "max_abs_pct_error_pct",
        "mae",
        "max_abs_error",
        "actual_latest_price",
        "predicted_latest_price",
        "latest_error_pct",
        "latest_abs_error_pct",
    ]:
        if column in grouped.columns:
            grouped[column] = grouped[column].round(4)
    return grouped


def main() -> None:
    configure_console_encoding()
    args = parse_args()
    client = get_client()
    saved_listing = _resolve_saved_listing(client, args)

    training_df = fetch_training_dataset(
        client,
        saved_listing_id=saved_listing["id"],
        limit=args.limit,
    )

    if training_df.empty:
        raise RuntimeError("No training rows were loaded from Supabase.")

    if "source_type" in training_df.columns:
        report_rows = training_df[training_df["source_type"] == "report_comp_price_by_date"].copy()
        if not report_rows.empty:
            training_df = report_rows

    if args.split_mode == "time":
        train_df, holdout_df, evaluation_label = _split_by_time_window(training_df, args.validation_days)
    else:
        holdout_comp_id = _pick_holdout_comp_id(
            training_df,
            args.holdout_comp_id or get_default_holdout_comp_id(),
            args.min_rows,
            strict=bool(args.holdout_comp_id),
        )
        holdout_df = training_df[training_df["airbnb_listing_id"].astype(str) == holdout_comp_id].copy()
        train_df = training_df[training_df["airbnb_listing_id"].astype(str) != holdout_comp_id].copy()
        if holdout_df.empty:
            raise RuntimeError(f"No rows found for holdout comp id {holdout_comp_id}.")
        if train_df.empty:
            raise RuntimeError("Training rows are empty after removing the holdout house.")
        evaluation_label = holdout_comp_id

    if train_df.empty or holdout_df.empty:
        raise RuntimeError("Backtest split produced an empty train or evaluation set.")

    model, feature_columns, _, _ = train_model(train_df)
    compare_df, X_holdout = _prepare_holdout_features(holdout_df, feature_columns)
    predicted_prices = np.expm1(model.predict(X_holdout))
    compare_df["actual_price"] = compare_df[TARGET_COLUMN_NAME].astype(float)
    compare_df["predicted_price"] = predicted_prices
    compare_df["error"] = compare_df["predicted_price"] - compare_df["actual_price"]
    compare_df["abs_error"] = compare_df["error"].abs()
    compare_df["error_ratio"] = np.where(
        compare_df["actual_price"] != 0,
        compare_df["error"] / compare_df["actual_price"],
        np.nan,
    )
    compare_df["abs_error_ratio"] = compare_df["error_ratio"].abs()
    compare_df["error_pct"] = compare_df["error_ratio"] * 100
    compare_df["abs_error_pct"] = compare_df["abs_error_ratio"] * 100

    preferred_columns = [
        "airbnb_listing_id",
        "source_report_id",
        "source_listing_id",
        "price_date",
        "observed_at_date",
        "days_until_stay",
        "location_city",
        "location_state",
        "property_type",
        "bedrooms",
        "baths",
        "accommodates",
        "similarity_score",
        "actual_price",
        "predicted_price",
        "error_pct",
        "abs_error_pct",
        "error_ratio",
        "abs_error_ratio",
        "error",
        "abs_error",
    ]
    ordered_columns = preferred_columns + [
        column for column in compare_df.columns if column not in preferred_columns
    ]
    compare_df = compare_df[ordered_columns]

    summary_df = _summarize_backtest(compare_df, evaluation_label, args.split_mode)
    by_date_df = _build_date_level_summary(compare_df)

    output_path = resolve_repo_path(args.output)
    summary_path = resolve_repo_path(args.summary_output)
    by_date_path = resolve_repo_path(args.by_date_output)
    for path in [output_path, summary_path, by_date_path]:
        path.parent.mkdir(parents=True, exist_ok=True)

    compare_df.to_csv(output_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    by_date_df.to_csv(by_date_path, index=False)

    print(f"Backtest split mode: {args.split_mode}")
    print(f"Backtest label: {evaluation_label}")
    print(f"Training rows used: {len(train_df)}")
    print(f"Evaluation rows scored: {len(holdout_df)}")
    print(summary_df.to_string(index=False))
    print(f"Wrote row-level comparison CSV to {output_path.resolve()}")
    print(f"Wrote summary CSV to {summary_path.resolve()}")
    print(f"Wrote date-level summary CSV to {by_date_path.resolve()}")


if __name__ == "__main__":
    main()
