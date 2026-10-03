from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from iiot_ai_sensor_gateway.forecast_evaluator_v2 import (
    DATASET_SCHEMA,
    build_forecast_dataset_v2,
    evaluate_baselines_v2,
    export_direct_horizon_datasets,
    load_forecast_dataset_v2,
)


FEATURES = ("temperature_c", "humidity_pct", "aux")
TARGETS = ("temperature_c", "humidity_pct")


def _write_windows(path: Path, *, groups: int = 2, points: int = 180, window: int = 8) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[str] = []
    for group in range(groups):
        values = []
        for index in range(points):
            offset = group * 0.03
            values.append(
                [
                    0.25 + offset + index * 0.001,
                    0.55 + offset + ((index % 20) - 10) * 0.002,
                    0.1 + ((index + group) % 7) * 0.01,
                ]
            )
        for index in range(window - 1, points):
            end = start + timedelta(minutes=index)
            begin = start + timedelta(minutes=index - window + 1)
            record = {
                "gateway_id": "gw-test",
                "node_id": f"Node{group + 1}",
                "room_id": f"room-{group + 1}",
                "start_timestamp": begin.isoformat(),
                "end_timestamp": end.isoformat(),
                "feature_names": list(FEATURES),
                "shape": [window, len(FEATURES)],
                "x": values[index - window + 1 : index + 1],
            }
            rows.append(json.dumps(record, separators=(",", ":")) + "\n")
    path.write_text("".join(rows), encoding="utf-8")


class ForecastEvaluatorV2Tests(unittest.TestCase):
    def test_builds_contiguous_multihorizon_dataset_with_independent_seasonality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            windows = root / "windows.jsonl"
            _write_windows(windows)
            dataset = root / "forecast_v2.npz"
            meta = root / "forecast_v2.json"
            result = build_forecast_dataset_v2(
                windows,
                dataset,
                meta,
                target_names=TARGETS,
                horizon_steps=5,
                declared_cadence_sec=60,
                seasonal_period=12,
                normalization_ranges={name: (0.0, 1.0) for name in FEATURES},
            )
            self.assertEqual(result["schema"], DATASET_SCHEMA)
            self.assertEqual(result["horizon_steps"], 5)
            self.assertEqual(result["seasonal_baseline_policy"], "independent_history")
            self.assertEqual(len(result["group_holdouts"]), 2)
            self.assertGreaterEqual(len(result["rolling_origin_folds"]), 1)
            loaded = load_forecast_dataset_v2(dataset)
            self.assertEqual(loaded["Y_train"].shape[1:], (5, 2))
            self.assertEqual(loaded["X_train"].shape[2], 3)
            self.assertTrue(np.all(loaded["seasonal_available_train"]))
            self.assertTrue(np.all(np.isfinite(loaded["mase_scale"])))
            self.assertTrue(np.all(loaded["mase_scale"] > 0))

            train_label_end = max(
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                for row in loaded["label_timestamps_train"]
                for value in row
            )
            val_anchor_start = min(
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                for value in loaded["anchor_timestamp_val"]
            )
            # Anchor is later still; the implementation additionally checks the
            # full input-window start against the previous label boundary.
            self.assertGreater(val_anchor_start, train_label_end)

    def test_baseline_selection_is_validation_only_and_scores_all_horizons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            windows = root / "windows.jsonl"
            _write_windows(windows, groups=1)
            dataset = root / "forecast_v2.npz"
            build_forecast_dataset_v2(
                windows,
                dataset,
                root / "meta.json",
                target_names=TARGETS,
                horizon_steps=4,
                declared_cadence_sec=60,
                seasonal_period=0,
            )
            metrics = evaluate_baselines_v2(dataset)
            self.assertEqual(metrics["baseline_selection_split"], "validation")
            self.assertEqual(set(metrics["splits"]), {"train", "val", "test"})
            self.assertEqual(set(metrics["splits"]["test"]["per_horizon"]), {"1", "2", "3", "4"})
            self.assertEqual(
                metrics["selection"]["applicability"]["seasonal_naive"]["applicable"],
                False,
            )

    def test_exports_direct_horizon_adapter_for_legacy_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            windows = root / "windows.jsonl"
            _write_windows(windows, groups=1)
            dataset = root / "forecast_v2.npz"
            build_forecast_dataset_v2(
                windows,
                dataset,
                root / "meta.json",
                target_names=TARGETS,
                horizon_steps=3,
                declared_cadence_sec=60,
            )
            paths = export_direct_horizon_datasets(dataset, root / "direct")
            self.assertEqual(len(paths), 3)
            for expected_horizon, path in enumerate(paths, start=1):
                data = np.load(path, allow_pickle=False)
                self.assertEqual(data["y_train"].ndim, 2)
                self.assertEqual(int(data["horizon_steps"][0]), expected_horizon)
                self.assertEqual(float(data["horizon_duration_seconds"][0]), expected_horizon * 60.0)

    def test_declared_runtime_cadence_conflict_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            windows = root / "windows.jsonl"
            _write_windows(windows, groups=1)
            with self.assertRaisesRegex(ValueError, "declared cadence"):
                build_forecast_dataset_v2(
                    windows,
                    root / "bad.npz",
                    root / "bad.json",
                    target_names=TARGETS,
                    horizon_steps=3,
                    declared_cadence_sec=300,
                )


if __name__ == "__main__":
    unittest.main()
