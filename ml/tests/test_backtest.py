from __future__ import annotations

import pandas as pd

from ml.backtest import (
    _build_date_level_summary,
    _pick_holdout_comp_id,
    _split_by_time_window,
    _summarize_backtest,
    get_default_holdout_comp_id,
)


def test_pick_holdout_comp_id_prefers_first_eligible_house() -> None:
    df = pd.DataFrame(
        {
            "airbnb_listing_id": ["a", "a", "a", "b", "b", "c"],
            "last_nightly_price": [100, 101, 99, 200, 201, 300],
        }
    )

    assert _pick_holdout_comp_id(df, explicit_comp_id=None, min_rows=2) == "a"
    assert _pick_holdout_comp_id(df, explicit_comp_id="b", min_rows=2) == "b"


def test_summarize_backtest_and_by_date_outputs_expected_metrics() -> None:
    compare_df = pd.DataFrame(
        {
            "price_date": ["2026-04-11", "2026-04-11", "2026-04-12"],
            "observed_at_date": ["2026-04-04", "2026-04-05", "2026-04-04"],
            "actual_price": [100.0, 120.0, 150.0],
            "predicted_price": [110.0, 118.0, 140.0],
            "abs_error": [10.0, 2.0, 10.0],
            "error_ratio": [0.10, -0.0166666667, -0.0666666667],
            "abs_error_ratio": [0.10, 0.0166666667, 0.0666666667],
        }
    )

    summary_df = _summarize_backtest(compare_df, "comp-1", "holdout_comp")
    by_date_df = _build_date_level_summary(compare_df)

    assert summary_df.loc[0, "split_mode"] == "holdout_comp"
    assert summary_df.loc[0, "evaluation_label"] == "comp-1"
    assert summary_df.loc[0, "n_rows"] == 3
    assert float(summary_df.loc[0, "mape_pct"]) == 6.1111
    assert float(summary_df.loc[0, "within_10_pct_ratio"]) == 1.0
    assert float(summary_df.loc[0, "mae"]) == 7.3333
    assert list(by_date_df["price_date"]) == ["2026-04-11", "2026-04-12"]
    assert float(by_date_df.loc[0, "mape_pct"]) == 5.8333
    assert float(by_date_df.loc[0, "actual_avg_price"]) == 110.0
    assert float(by_date_df.loc[0, "actual_latest_price"]) == 120.0
    assert float(by_date_df.loc[0, "predicted_latest_price"]) == 118.0
    assert by_date_df.loc[0, "latest_observed_at_date"] == "2026-04-05"


def test_split_by_time_window_uses_latest_dates_as_validation() -> None:
    df = pd.DataFrame(
        {
            "observed_at_date": [
                "2026-04-01",
                "2026-04-02",
                "2026-04-03",
                "2026-04-04",
                "2026-04-05",
            ],
            "last_nightly_price": [100, 110, 120, 130, 140],
        }
    )

    train_df, validation_df, split_label = _split_by_time_window(df, validation_days=2)

    assert list(train_df["observed_at_date"]) == ["2026-04-01", "2026-04-02", "2026-04-03"]
    assert list(validation_df["observed_at_date"]) == ["2026-04-04", "2026-04-05"]
    assert split_label == "time_2026-04-04_to_2026-04-05"


def test_get_default_holdout_comp_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ML_BACKTEST_HOLDOUT_COMP_ID", "52944275")
    assert get_default_holdout_comp_id() == "52944275"
