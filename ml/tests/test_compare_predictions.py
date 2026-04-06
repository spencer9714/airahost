from __future__ import annotations

import pandas as pd

from ml.compare_predictions import compare_prediction_dates


def test_compare_prediction_dates_builds_summary_and_driver_delta() -> None:
    explanations_df = pd.DataFrame(
        [
            {
                "date": "2026-04-06",
                "predicted_price": 280.0,
                "baseline_price": 300.0,
                "active_feature_count": 3,
            },
            {
                "date": "2026-04-13",
                "predicted_price": 250.0,
                "baseline_price": 300.0,
                "active_feature_count": 3,
            },
        ]
    )
    contributions_df = pd.DataFrame(
        [
            {
                "date": "2026-04-06",
                "feature_rank": 1,
                "feature": "day_of_week_mon",
                "feature_value": 1.0,
                "contribution_log": -0.01,
                "abs_contribution_log": 0.01,
                "contribution_multiplier": 0.99,
                "direction": "push_down",
                "baseline_price": 300.0,
            },
            {
                "date": "2026-04-06",
                "feature_rank": 2,
                "feature": "lead_time_bucket_days_1_3",
                "feature_value": 0.0,
                "contribution_log": 0.02,
                "abs_contribution_log": 0.02,
                "contribution_multiplier": 1.02,
                "direction": "push_up",
                "baseline_price": 300.0,
            },
            {
                "date": "2026-04-13",
                "feature_rank": 1,
                "feature": "day_of_week_mon",
                "feature_value": 1.0,
                "contribution_log": -0.03,
                "abs_contribution_log": 0.03,
                "contribution_multiplier": 0.97,
                "direction": "push_down",
                "baseline_price": 300.0,
            },
            {
                "date": "2026-04-13",
                "feature_rank": 2,
                "feature": "lead_time_bucket_days_4_7",
                "feature_value": 1.0,
                "contribution_log": -0.04,
                "abs_contribution_log": 0.04,
                "contribution_multiplier": 0.96,
                "direction": "push_down",
                "baseline_price": 300.0,
            },
        ]
    )
    input_matrix_df = pd.DataFrame(
        [
            {
                "date": "2026-04-06",
                "day_of_week_mon": 1.0,
                "lead_time_bucket_days_1_3": 1.0,
                "lead_time_bucket_days_4_7": 0.0,
            },
            {
                "date": "2026-04-13",
                "day_of_week_mon": 1.0,
                "lead_time_bucket_days_1_3": 0.0,
                "lead_time_bucket_days_4_7": 1.0,
            },
        ]
    )

    summary_df, drivers_df = compare_prediction_dates(
        explanations_df,
        contributions_df,
        "2026-04-06",
        "2026-04-13",
        input_matrix_df=input_matrix_df,
    )

    assert summary_df.iloc[0]["price_delta_b_minus_a"] == -30.0
    assert round(summary_df.iloc[0]["price_delta_pct_vs_a"], 4) == round((-30.0 / 280.0) * 100.0, 4)

    top_driver = drivers_df.iloc[0]
    assert top_driver["feature"] == "lead_time_bucket_days_4_7"
    assert top_driver["delta_direction"] == "pushes_date_b_down"
    assert top_driver["feature_value_a"] == 0.0
    assert top_driver["feature_value_b"] == 1.0
