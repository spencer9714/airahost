from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "ml" / "reports"
DEFAULT_MANIFEST_PATH = REPORTS_DIR / "batch_pipeline_result.json"
DEFAULT_SUMMARY_OUTPUT = REPORTS_DIR / "prediction_date_comparison_summary.csv"
DEFAULT_DRIVERS_OUTPUT = REPORTS_DIR / "prediction_date_comparison_drivers.csv"


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _resolve_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _load_artifact_paths(manifest_path: Path) -> Dict[str, Path]:
    artifact_paths = {
        "prediction_explanations": REPORTS_DIR / "prediction_explanations.csv",
        "prediction_feature_contributions": REPORTS_DIR / "prediction_feature_contributions.csv",
        "forecast_input_matrix": None,
    }
    if not manifest_path.exists():
        return artifact_paths

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts", {})
    for key in list(artifact_paths.keys()):
        resolved = _resolve_path(artifacts.get(key))
        if resolved is not None:
            artifact_paths[key] = resolved
    return artifact_paths


def _require_single_row(df: pd.DataFrame, date_value: str, *, label: str) -> pd.Series:
    matched = df[df["date"] == date_value]
    if matched.empty:
        raise ValueError(f"Date {date_value} was not found in {label}.")
    if len(matched) > 1:
        raise ValueError(f"Date {date_value} appeared multiple times in {label}.")
    return matched.iloc[0]


def compare_prediction_dates(
    explanations_df: pd.DataFrame,
    contributions_df: pd.DataFrame,
    date_a: str,
    date_b: str,
    *,
    input_matrix_df: pd.DataFrame | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    row_a = _require_single_row(explanations_df, date_a, label="prediction_explanations.csv")
    row_b = _require_single_row(explanations_df, date_b, label="prediction_explanations.csv")

    price_a = float(row_a["predicted_price"])
    price_b = float(row_b["predicted_price"])
    price_delta = price_b - price_a
    price_delta_pct_vs_a = (price_delta / price_a * 100.0) if price_a else 0.0

    summary_row = {
        "date_a": date_a,
        "predicted_price_a": price_a,
        "baseline_price_a": float(row_a.get("baseline_price", 0.0)),
        "date_b": date_b,
        "predicted_price_b": price_b,
        "baseline_price_b": float(row_b.get("baseline_price", 0.0)),
        "price_delta_b_minus_a": price_delta,
        "price_delta_pct_vs_a": price_delta_pct_vs_a,
        "active_feature_count_a": int(row_a.get("active_feature_count", 0)),
        "active_feature_count_b": int(row_b.get("active_feature_count", 0)),
    }

    contrib_a = contributions_df[contributions_df["date"] == date_a].copy()
    contrib_b = contributions_df[contributions_df["date"] == date_b].copy()

    if contrib_a.empty or contrib_b.empty:
        raise ValueError("Contribution rows were not found for one of the selected dates.")

    contrib_a = contrib_a.rename(
        columns={
            "feature_rank": "feature_rank_a",
            "contribution_log": "contribution_log_a",
            "abs_contribution_log": "abs_contribution_log_a",
            "contribution_multiplier": "contribution_multiplier_a",
            "direction": "direction_a",
        }
    )
    contrib_b = contrib_b.rename(
        columns={
            "feature_rank": "feature_rank_b",
            "contribution_log": "contribution_log_b",
            "abs_contribution_log": "abs_contribution_log_b",
            "contribution_multiplier": "contribution_multiplier_b",
            "direction": "direction_b",
        }
    )

    merged = contrib_a[
        [
            "feature",
            "feature_rank_a",
            "contribution_log_a",
            "abs_contribution_log_a",
            "contribution_multiplier_a",
            "direction_a",
        ]
    ].merge(
        contrib_b[
            [
                "feature",
                "feature_rank_b",
                "contribution_log_b",
                "abs_contribution_log_b",
                "contribution_multiplier_b",
                "direction_b",
            ]
        ],
        on="feature",
        how="outer",
    )

    for column in [
        "feature_rank_a",
        "feature_rank_b",
        "contribution_log_a",
        "abs_contribution_log_a",
        "contribution_multiplier_a",
        "contribution_log_b",
        "abs_contribution_log_b",
        "contribution_multiplier_b",
    ]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(0.0)

    merged["direction_a"] = merged["direction_a"].fillna("neutral")
    merged["direction_b"] = merged["direction_b"].fillna("neutral")
    merged["delta_contribution_log_b_minus_a"] = merged["contribution_log_b"] - merged["contribution_log_a"]
    merged["abs_delta_contribution_log"] = merged["delta_contribution_log_b_minus_a"].abs()
    merged["delta_multiplier_b_vs_a"] = merged["delta_contribution_log_b_minus_a"].apply(lambda v: math.exp(v))
    merged["delta_direction"] = merged["delta_contribution_log_b_minus_a"].apply(
        lambda v: "pushes_date_b_up" if v > 0 else ("pushes_date_b_down" if v < 0 else "no_change")
    )

    value_lookup_a: Dict[str, Any] = {}
    value_lookup_b: Dict[str, Any] = {}
    if input_matrix_df is not None and not input_matrix_df.empty:
        input_row_a = _require_single_row(input_matrix_df, date_a, label="forecast input matrix")
        input_row_b = _require_single_row(input_matrix_df, date_b, label="forecast input matrix")
        value_lookup_a = input_row_a.to_dict()
        value_lookup_b = input_row_b.to_dict()

    merged["feature_value_a"] = merged["feature"].map(lambda feature: value_lookup_a.get(feature))
    merged["feature_value_b"] = merged["feature"].map(lambda feature: value_lookup_b.get(feature))
    merged["feature_value_changed"] = merged.apply(
        lambda row: str(row["feature_value_a"]) != str(row["feature_value_b"]),
        axis=1,
    )

    merged = merged.sort_values(
        ["abs_delta_contribution_log", "abs_contribution_log_b", "abs_contribution_log_a"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    return pd.DataFrame([summary_row]), merged


def print_comparison_report(summary_df: pd.DataFrame, drivers_df: pd.DataFrame, *, top_n: int) -> None:
    summary = summary_df.iloc[0]
    print("")
    print("=== PREDICTION DATE COMPARISON ===")
    print(
        f"{summary['date_a']}: ${summary['predicted_price_a']:.2f}  ->  "
        f"{summary['date_b']}: ${summary['predicted_price_b']:.2f}"
    )
    print(
        f"Delta: ${summary['price_delta_b_minus_a']:+.2f} "
        f"({summary['price_delta_pct_vs_a']:+.2f}%)"
    )
    print("")

    pushes_up = drivers_df[drivers_df["delta_contribution_log_b_minus_a"] > 0].head(top_n)
    pushes_down = drivers_df[drivers_df["delta_contribution_log_b_minus_a"] < 0].head(top_n)

    print(f"Top drivers pushing {summary['date_b']} higher than {summary['date_a']}:")
    if pushes_up.empty:
        print("  none")
    else:
        for _, row in pushes_up.iterrows():
            print(
                "  "
                f"{row['feature']}: delta_log={row['delta_contribution_log_b_minus_a']:+.4f}, "
                f"value {row['feature_value_a']} -> {row['feature_value_b']}"
            )

    print("")
    print(f"Top drivers pushing {summary['date_b']} lower than {summary['date_a']}:")
    if pushes_down.empty:
        print("  none")
    else:
        for _, row in pushes_down.iterrows():
            print(
                "  "
                f"{row['feature']}: delta_log={row['delta_contribution_log_b_minus_a']:+.4f}, "
                f"value {row['feature_value_a']} -> {row['feature_value_b']}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare why two forecast dates have different predicted prices.")
    parser.add_argument("date_a", help="Reference forecast date in YYYY-MM-DD format.")
    parser.add_argument("date_b", help="Comparison forecast date in YYYY-MM-DD format.")
    parser.add_argument("--top", type=int, default=10, help="How many top changing drivers to print.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Batch pipeline manifest path.")
    parser.add_argument(
        "--summary-output",
        default=str(DEFAULT_SUMMARY_OUTPUT),
        help="CSV path for the comparison summary output.",
    )
    parser.add_argument(
        "--drivers-output",
        default=str(DEFAULT_DRIVERS_OUTPUT),
        help="CSV path for the feature-driver comparison output.",
    )
    return parser.parse_args()


def main() -> None:
    configure_console_encoding()
    args = parse_args()

    manifest_path = _resolve_path(args.manifest)
    if manifest_path is None:
        raise ValueError("Manifest path could not be resolved.")

    artifact_paths = _load_artifact_paths(manifest_path)
    explanations_path = artifact_paths["prediction_explanations"]
    contributions_path = artifact_paths["prediction_feature_contributions"]
    input_matrix_path = artifact_paths.get("forecast_input_matrix")

    if explanations_path is None or not explanations_path.exists():
        raise FileNotFoundError("prediction_explanations.csv was not found. Run the batch pipeline first.")
    if contributions_path is None or not contributions_path.exists():
        raise FileNotFoundError("prediction_feature_contributions.csv was not found. Run the batch pipeline first.")

    explanations_df = pd.read_csv(explanations_path)
    contributions_df = pd.read_csv(contributions_path)
    input_matrix_df = pd.read_csv(input_matrix_path) if input_matrix_path and input_matrix_path.exists() else None

    summary_df, drivers_df = compare_prediction_dates(
        explanations_df,
        contributions_df,
        args.date_a,
        args.date_b,
        input_matrix_df=input_matrix_df,
    )

    summary_output = _resolve_path(args.summary_output)
    drivers_output = _resolve_path(args.drivers_output)
    if summary_output is None or drivers_output is None:
        raise ValueError("Output path could not be resolved.")

    summary_output.parent.mkdir(parents=True, exist_ok=True)
    drivers_output.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(summary_output, index=False)
    drivers_df.to_csv(drivers_output, index=False)

    print(f"Wrote summary comparison to {summary_output}")
    print(f"Wrote driver comparison to {drivers_output}")
    print_comparison_report(summary_df, drivers_df, top_n=args.top)


if __name__ == "__main__":
    main()
