from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from iiot_ai_sensor_gateway.advanced_forecasting import (
    predict_local_forecast_v2,
    train_local_forecast_v2,
)
from iiot_ai_sensor_gateway.contracts import FeatureVector
from iiot_ai_sensor_gateway.forecast_evaluator_v2 import build_forecast_dataset_v2
from iiot_ai_sensor_gateway.live_forecast import build_live_forecaster
from iiot_ai_sensor_gateway.model_registry import (
    ModelRegistry,
    build_model_registry,
    export_local_deployment_manifest,
    validate_deployment_manifest,
)
from iiot_ai_sensor_gateway.normalization import DEFAULT_RANGES
from iiot_ai_sensor_gateway.runtime_benchmark import benchmark_forecast_runtime_v2


FEATURES = ("temperature_c", "humidity_pct")
TARGETS = FEATURES


def _make_dataset(root: Path) -> Path:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    window = 6
    points = 130
    values = [
        [0.30 + index * 0.001, 0.50 + ((index % 13) - 6) * 0.002]
        for index in range(points)
    ]
    rows = []
    for index in range(window - 1, points):
        rows.append(
            json.dumps(
                {
                    "gateway_id": "gw-test",
                    "node_id": "Node1",
                    "room_id": "lab",
                    "start_timestamp": (
                        start + timedelta(minutes=index - window + 1)
                    ).isoformat(),
                    "end_timestamp": (start + timedelta(minutes=index)).isoformat(),
                    "feature_names": list(FEATURES),
                    "shape": [window, len(FEATURES)],
                    "x": values[index - window + 1 : index + 1],
                },
                separators=(",", ":"),
            )
            + "\n"
        )
    windows = root / "windows.jsonl"
    windows.write_text("".join(rows), encoding="utf-8")
    dataset = root / "forecast_v2.npz"
    build_forecast_dataset_v2(
        windows,
        dataset,
        root / "forecast_v2.json",
        target_names=TARGETS,
        horizon_steps=3,
        declared_cadence_sec=60,
    )
    return dataset


class RegistryRuntimeV2Tests(unittest.TestCase):
    def test_manifest_registry_and_numpy_runtime_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _make_dataset(root)
            model_dir = root / "nlinear"
            train_local_forecast_v2(dataset, model_dir, model_type="nlinear")
            manifest_path = root / "deploy" / "nlinear.json"
            manifest = export_local_deployment_manifest(
                model_dir / "model.json",
                manifest_path,
                model_id="nlinear-test-v2",
                target_node_id="Node1",
            )
            self.assertEqual(manifest["runtime_backend"], "numpy_nlinear_v2")
            validate_deployment_manifest(manifest_path)
            registry_path = root / "deploy" / "registry.json"
            build_model_registry([manifest_path], registry_path)
            registry = ModelRegistry(registry_path)
            self.assertEqual(registry.ids(), ("nlinear-test-v2",))

            batch_prediction = predict_local_forecast_v2(
                dataset,
                model_dir / "model.json",
                split="test",
            )[0]
            data = np.load(dataset, allow_pickle=False)
            history = data["X_test"][0]
            runtime = build_live_forecaster(manifest_path, device="cpu")
            start = datetime(2026, 2, 1, tzinfo=UTC)
            result = None
            for step, row in enumerate(history):
                raw = {}
                for index, name in enumerate(FEATURES):
                    low, high = DEFAULT_RANGES[name]
                    raw[name] = low + float(row[index]) * (high - low)
                result = runtime.process(
                    FeatureVector(
                        "gw-test",
                        "node1",
                        "lab",
                        start + timedelta(minutes=step),
                        raw,
                    )
                )
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(result["trajectory"]), 3)
            for horizon in range(3):
                for target_index, name in enumerate(TARGETS):
                    low, high = DEFAULT_RANGES[name]
                    expected = low + float(batch_prediction[horizon, target_index]) * (high - low)
                    self.assertAlmostEqual(
                        result["trajectory"][horizon][name], expected, places=5
                    )

    def test_runtime_fails_closed_on_cadence_and_target_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _make_dataset(root)
            model_dir = root / "ridge"
            train_local_forecast_v2(dataset, model_dir, model_type="ridge")
            manifest_path = root / "deploy.json"
            export_local_deployment_manifest(
                model_dir / "model.json",
                manifest_path,
                model_id="ridge-test-v2",
                target_node_id="node1",
            )
            runtime = build_live_forecaster(manifest_path)
            other = runtime.process(
                FeatureVector(
                    "gw",
                    "node2",
                    "lab",
                    datetime(2026, 1, 1, tzinfo=UTC),
                    {"temperature_c": 20.0, "humidity_pct": 50.0},
                )
            )
            self.assertEqual(other["status"], "not_target_node")
            first = runtime.process(
                FeatureVector(
                    "gw",
                    "node1",
                    "lab",
                    datetime(2026, 1, 1, tzinfo=UTC),
                    {"temperature_c": 20.0, "humidity_pct": 50.0},
                )
            )
            self.assertEqual(first["status"], "warming")
            mismatch = runtime.process(
                FeatureVector(
                    "gw",
                    "node1",
                    "lab",
                    datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    {"temperature_c": 20.0, "humidity_pct": 50.0},
                )
            )
            self.assertEqual(mismatch["status"], "abstain_cadence_mismatch")

    def test_manifest_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _make_dataset(root)
            model_dir = root / "ridge"
            train_local_forecast_v2(dataset, model_dir, model_type="ridge")
            manifest_path = root / "deploy.json"
            export_local_deployment_manifest(
                model_dir / "model.json",
                manifest_path,
                model_id="ridge-tamper-v2",
            )
            document = json.loads(manifest_path.read_text())
            document["feature_names"] = list(reversed(document["feature_names"]))
            manifest_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "feature_names"):
                validate_deployment_manifest(manifest_path)

    def test_runtime_benchmark_reports_host_scope_and_source_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _make_dataset(root)
            model_dir = root / "nlinear"
            train_local_forecast_v2(dataset, model_dir, model_type="nlinear")
            manifest_path = root / "deploy.json"
            export_local_deployment_manifest(
                model_dir / "model.json",
                manifest_path,
                model_id="nlinear-benchmark-v2",
            )
            result = benchmark_forecast_runtime_v2(
                dataset,
                manifest_path,
                iterations=8,
                warmup=2,
                parity_samples=4,
                device="cpu",
            )
            self.assertTrue(result["parity"]["passed"])
            self.assertEqual(result["samples_benchmarked"], 8)
            self.assertIn("p99", result["latency_ms"])
            self.assertGreater(result["footprint"]["artifact_bytes"], 0)
            self.assertIn(
                result["evidence_scope"],
                {"RASPBERRY_PI_5_MEASUREMENT", "CURRENT_HOST_MEASUREMENT_NOT_PI_EVIDENCE"},
            )
            self.assertFalse(result["production_promotion"])


if __name__ == "__main__":
    unittest.main()
