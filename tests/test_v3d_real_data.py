from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from scripts.v3d_analysis_pack import validate_run_matrix
from iiot_ai_sensor_gateway.v3d_real_data import (
    _baseline_candidates,
    _even_cap,
    _series_windows,
    build_model,
    regression_metrics,
    select_baselines,
)


class V3DRealDataTests(unittest.TestCase):
    def test_analysis_pack_rejects_duplicate_or_missing_run_combinations(self) -> None:
        runs = []
        metric_values = {
            "mae": 1.0,
            "rmse": 1.0,
            "mase": 1.0,
            "baseline_rmse": 2.0,
            "rmse_skill": 0.5,
        }
        for dataset in (
            "uci_air_quality_360",
            "beijing_multi_site_air_quality_501",
            "intel_lab_sensor_data",
        ):
            for model in ("dlinear", "fits", "lstm", "patchtst"):
                for seed in (42, 43, 44, 45, 46):
                    runs.append(
                        {
                            "schema": "iiot.v3d.forecast_run.v1",
                            "dataset_id": dataset,
                            "model": model,
                            "seed": seed,
                            "epochs_requested": 12,
                            "batch_size": 256,
                            "prepared_sha256": "a" * 64,
                            "source_sha256": "b" * 64,
                            "metrics": {
                                "mean_mase": 1.0,
                                "mean_rmse_skill": 0.5,
                                "target_wins": 2,
                                "target_count": 2,
                                "per_target": {"first": metric_values, "second": metric_values},
                            },
                        }
                    )
        self.assertEqual(validate_run_matrix(runs)["unique_combinations"], 60)
        invalid = list(runs)
        invalid[-1] = dict(runs[0])
        with self.assertRaisesRegex(ValueError, "invalid V3D run matrix"):
            validate_run_matrix(invalid)

    def test_even_cap_is_deterministic_and_keeps_endpoints(self) -> None:
        values = np.arange(100, dtype=np.int64)
        capped = _even_cap(values, 7)
        self.assertEqual(len(capped), 7)
        self.assertEqual(int(capped[0]), 0)
        self.assertEqual(int(capped[-1]), 99)
        np.testing.assert_array_equal(capped, _even_cap(values, 7))

    def test_windows_do_not_cross_split(self) -> None:
        values = np.arange(120, dtype=np.float64).reshape(60, 2)
        X, y = _series_windows(values, 10, 50, lookback=8, horizon=3, limit=0)
        self.assertGreater(len(X), 0)
        np.testing.assert_array_equal(X[0], values[10:18])
        np.testing.assert_array_equal(y[0], values[20])
        np.testing.assert_array_equal(X[-1], values[39:47])
        np.testing.assert_array_equal(y[-1], values[49])

    def test_baseline_selection_uses_validation_target(self) -> None:
        X = np.zeros((10, 12, 1), dtype=np.float64)
        X[:, :, 0] = np.arange(12)
        y = np.full((10, 1), 11.0)
        selection = select_baselines(X, y, seasonal_period=8, horizon=1, target_names=("x",))
        self.assertEqual(selection["x"], "last_value")
        self.assertIn("drift", _baseline_candidates(X, seasonal_period=8, horizon=1))

    def test_regression_metrics_uses_train_scale_and_baseline(self) -> None:
        actual = np.asarray([[1.0], [2.0], [3.0]])
        prediction = np.asarray([[1.0], [2.0], [2.0]])
        baseline = np.asarray([[0.0], [1.0], [2.0]])
        metrics = regression_metrics(actual, prediction, baseline, np.asarray([1.0]), ("x",))
        self.assertAlmostEqual(metrics["per_target"]["x"]["mae"], 1.0 / 3.0)
        self.assertAlmostEqual(metrics["per_target"]["x"]["mase"], 1.0 / 3.0)
        self.assertTrue(metrics["per_target"]["x"]["beats_baseline"])

    def test_all_models_have_expected_shape(self) -> None:
        torch = __import__("torch")
        batch = torch.zeros((4, 32, 2), dtype=torch.float32)
        for name in ("dlinear", "fits", "lstm", "patchtst"):
            with self.subTest(model=name):
                model, metadata = build_model(name, 32, 2)
                output = model(batch)
                self.assertEqual(tuple(output.shape), (4, 2))
                self.assertIn("architecture", metadata)
                self.assertTrue(torch.isfinite(output).all())


if __name__ == "__main__":
    unittest.main()
