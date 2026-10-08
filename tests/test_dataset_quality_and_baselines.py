from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from iiot_ai_sensor_gateway.contracts import FeatureVector
from iiot_ai_sensor_gateway.dataset_quality import (
    array_quality_report,
    finite_or_raise,
    infer_cadence,
    select_active_features,
)
from iiot_ai_sensor_gateway.forecast_baselines import (
    baseline_candidates,
    select_baseline_per_target,
)
from iiot_ai_sensor_gateway.forecasting import prepare_forecast_dataset
from iiot_ai_sensor_gateway.normalization import MinMaxNormalizer
from iiot_ai_sensor_gateway.simulator import write_simulation


class DatasetQualityAndBaselineTests(unittest.TestCase):
    @staticmethod
    def _records(interval_sec: int, count: int = 20) -> list[dict]:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            {
                "node_id": "node-a",
                "end_timestamp": (start + timedelta(seconds=index * interval_sec)).isoformat(),
            }
            for index in range(count)
        ]

    def test_cadence_inference_preserves_hourly_horizon_truth(self):
        report = infer_cadence(self._records(3600))
        self.assertEqual(report["cadence_seconds"], 3600.0)
        self.assertEqual(report["cadence_source"], "timestamp_inferred")
        self.assertEqual(5 * report["cadence_seconds"], 5 * 3600)

    def test_cadence_allows_missing_integer_multiple_but_rejects_irregular(self):
        records = self._records(60)
        del records[5]
        report = infer_cadence(records)
        self.assertEqual(report["cadence_seconds"], 60.0)
        self.assertGreaterEqual(report["inferred_missing_steps"], 1)
        irregular = self._records(60)
        irregular[8]["end_timestamp"] = (
            datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=8 * 60 + 17)
        ).isoformat()
        with self.assertRaisesRegex(ValueError, "too irregular"):
            infer_cadence(irregular, max_irregular_fraction=0.01)

    def test_declared_cadence_is_validated_not_trusted_blindly(self):
        with self.assertRaisesRegex(ValueError, "conflicts"):
            infer_cadence(self._records(3600), declared_interval_sec=60)

    def test_cadence_does_not_merge_reused_node_ids_across_gateways(self):
        records = self._records(60)
        for row in records:
            row.update(gateway_id="gateway-a", room_id="room-a")
        second = self._records(60)
        for row in second:
            row.update(gateway_id="gateway-b", room_id="room-b")
            # A distinct source sampled at an offset must not be interleaved
            # into the first source's cadence.
            row["end_timestamp"] = (
                datetime.fromisoformat(row["end_timestamp"]) + timedelta(seconds=30)
            ).isoformat()
        result = infer_cadence(records + second, declared_interval_sec=60)
        self.assertEqual(result["cadence_seconds"], 60.0)
        self.assertEqual(result["observed_delta_count"], 38)
        self.assertEqual(set(result["nodes"]), {"gateway-a/node-a/room-a", "gateway-b/node-a/room-b"})

    def test_active_feature_schema_uses_train_only_and_keeps_targets(self):
        x_train = np.zeros((8, 4, 4), dtype=np.float32)
        x_train[:, :, 1] = np.arange(8, dtype=np.float32)[:, None]
        x_train[:, :, 2] = 0.5  # target stays even when constant
        manifest = select_active_features(
            x_train,
            ("constant", "varying", "target_constant", "also_constant"),
            ("target_constant",),
        )
        self.assertEqual(
            manifest["ordered_features"], ["varying", "target_constant"]
        )
        self.assertIn("constant", manifest["dropped_train_constant_features"])
        self.assertEqual(len(manifest["schema_sha256"]), 64)

    def test_constant_and_boundary_saturated_targets_block_promotion(self):
        arrays = {
            "y_train": np.asarray([[0.0, 0.2], [0.0, 0.3], [0.0, 0.4]], dtype=np.float32),
            "y_val": np.asarray([[0.0, 0.4], [0.0, 0.5]], dtype=np.float32),
            "y_test": np.asarray([[0.0, 1.0], [0.0, 1.0]], dtype=np.float32),
        }
        report = array_quality_report(arrays, ("constant", "saturated"))
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("constant", report["blocked_targets"])
        self.assertIn("saturated", report["blocked_targets"])
        self.assertEqual(report["effective_target_count"], 0)

    def test_seasonal_baseline_is_explicitly_inapplicable_when_window_short(self):
        x = np.arange(3 * 12 * 2, dtype=np.float32).reshape(3, 12, 2)
        candidates = baseline_candidates(
            x,
            np.asarray([0, 1]),
            horizon_steps=5,
            seasonal_period=24,
        )
        self.assertFalse(candidates["seasonal_naive"]["applicable"])
        self.assertIn("requires_history", candidates["seasonal_naive"]["reason"])

    def test_duplicate_baseline_cannot_count_as_independent(self):
        x = np.ones((5, 4, 1), dtype=np.float32)
        actual = np.ones((5, 1), dtype=np.float32)
        candidates = baseline_candidates(
            x,
            np.asarray([0]),
            horizon_steps=1,
            seasonal_period=0,
        )
        selection = select_baseline_per_target(actual, candidates, ("target",))
        duplicate_reasons = [
            item["reason"]
            for item in selection["applicability"].values()
            if item["reason"] and "duplicate_predictions" in item["reason"]
        ]
        self.assertTrue(duplicate_reasons)

    def test_normalizer_reports_clipping_without_hiding_it(self):
        normalizer = MinMaxNormalizer({"temperature_c": (0.0, 10.0)})
        vector = FeatureVector(
            "gw", "node", "room", datetime(2026, 1, 1, tzinfo=UTC),
            {"temperature_c": 20.0},
        )
        normalized = normalizer.normalize(vector)
        self.assertEqual(normalized.values["temperature_c"], 1.0)
        report = normalizer.report()
        self.assertEqual(report["fields"]["temperature_c"]["upper_clipped"], 1)
        self.assertEqual(report["clip_fraction"], 1.0)

    def test_bounded_simulator_is_v3_and_non_degenerate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_simulation(
                Path(temp_dir) / "payloads.jsonl",
                "mixed",
                count=400,
                nodes=1,
                interval_sec=60,
                seed=17,
            )
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(row["v"] == 3 and row["pp"] == "hardware_only" for row in rows))
        for field in ("co", "co2", "pm25"):
            values = [row["s"][field] for row in rows if row["s"][field] is not None]
            self.assertGreater(max(values) - min(values), 0.1)
            self.assertGreater(len(set(values)), 20)
        self.assertLess(max(row["s"]["co"] for row in rows), 20.0)
        self.assertLess(max(row["s"]["pm25"] or 0.0 for row in rows), 100.0)

    def test_prepare_dataset_records_inferred_cadence_and_quality_manifest(self):
        feature_names = ("temperature_c", "humidity_pct", "constant")
        start = datetime(2026, 1, 1, tzinfo=UTC)
        records = []
        for index in range(120):
            timestamp = start + timedelta(hours=index)
            records.append(
                {
                    "gateway_id": "gw",
                    "node_id": "node",
                    "room_id": "room",
                    "start_timestamp": timestamp.isoformat(),
                    "end_timestamp": timestamp.isoformat(),
                    "feature_names": list(feature_names),
                    "shape": [1, len(feature_names)],
                    "x": [[0.1 + index * 0.005, 0.3 + index * 0.002, 0.0]],
                }
            )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            windows = root / "windows.jsonl"
            windows.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            stats = prepare_forecast_dataset(
                windows,
                root / "forecast.npz",
                root / "forecast.json",
                horizon_steps=5,
                target_names=("temperature_c", "humidity_pct"),
                window_size=4,
            )
            meta = json.loads((root / "forecast.json").read_text(encoding="utf-8"))
        self.assertEqual(stats.cadence_seconds, 3600.0)
        self.assertEqual(stats.horizon_duration_seconds, 5 * 3600.0)
        self.assertNotIn("constant", stats.feature_names)
        self.assertIn("feature_manifest", meta)
        self.assertIn("data_quality", meta)


    def test_finite_or_raise_chunked_accepts_large_finite_and_rejects_nan(self):
        finite = np.ones((5000, 8, 4), dtype=np.float32)
        finite_or_raise({"X_train": finite}, chunk_rows=512)
        bad = finite.copy()
        bad[4999, 0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "non-finite"):
            finite_or_raise({"X_train": bad}, chunk_rows=512)


if __name__ == "__main__":
    unittest.main()
