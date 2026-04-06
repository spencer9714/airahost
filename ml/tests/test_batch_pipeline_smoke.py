from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ml.batch_pipeline import _validate_batch_outputs


def _write_csv(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def test_validate_batch_outputs_accepts_expected_artifacts(tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.csv"
    training_matrix_path = tmp_path / "training_matrix.csv"
    metrics_path = tmp_path / "metrics_latest.csv"

    _write_csv(
        predictions_path,
        [{"date": "2026-04-06", "predicted_price": 280.5, "is_weekend": False, "is_holiday": False}],
    )
    _write_csv(
        training_matrix_path,
        [{
            "debug_source_type": "report_comp_price_by_date",
            "debug_price_date": "2026-04-11",
            "debug_observed_at_date": "2026-04-04",
            "last_nightly_price": 293.5,
        }],
    )
    _write_csv(metrics_path, [{"n_samples": 1, "trained_now": True}])

    _validate_batch_outputs(
        {
            "predictions_latest": str(predictions_path),
            "training_matrix_latest": str(training_matrix_path),
            "metrics_latest": str(metrics_path),
        }
    )


def test_validate_batch_outputs_rejects_missing_training_debug_columns(tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.csv"
    training_matrix_path = tmp_path / "training_matrix.csv"
    metrics_path = tmp_path / "metrics_latest.csv"

    _write_csv(
        predictions_path,
        [{"date": "2026-04-06", "predicted_price": 280.5, "is_weekend": False, "is_holiday": False}],
    )
    _write_csv(training_matrix_path, [{"last_nightly_price": 293.5}])
    _write_csv(metrics_path, [{"n_samples": 1, "trained_now": True}])

    with pytest.raises(RuntimeError):
        _validate_batch_outputs(
            {
                "predictions_latest": str(predictions_path),
                "training_matrix_latest": str(training_matrix_path),
                "metrics_latest": str(metrics_path),
            }
        )
