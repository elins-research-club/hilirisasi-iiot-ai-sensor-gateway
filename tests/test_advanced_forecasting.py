from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from iiot_ai_sensor_gateway.advanced_forecasting import (
    MODEL_SCHEMA,
    evaluate_rolling_origin_local_v2,
    evaluate_local_forecast_v2,
    predict_local_forecast_v2,
    run_local_bakeoff_v2,
    train_local_forecast_v2,
)
from iiot_ai_sensor_gateway.forecast_evaluator_v2 import build_forecast_dataset_v2


FEATURES = ("temperature_c", "humidity_pct", "aux")
TARGETS = ("temperature_c", "humidity_pct")


def _build_dataset(root: Path) -> Path:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    window = 8
    point_count = 150
    values = [
        [
            0.25 + index * 0.0015,
            0.50 + index * 0.0008 + ((index % 9) - 4) * 0.001,
            0.2 + ((index % 5) * 0.02),
        ]
        for index in range(point_count)
    ]
    rows: list[str] = []
    for index in range(window - 1, point_count):
        record = {
            "gateway_id": "gw-test",
            "node_id": "Node1",
            "room_id": "lab",
            "start_timestamp": (start + timedelta(minutes=index - window + 1)).isoformat(),
            "end_timestamp": (start + timedelta(minutes=index)).isoformat(),
            "feature_names": list(FEATURES),
            "shape": [window, len(FEATURES)],
            "x": values[index - window + 1 : index + 1],
        }
        rows.append(json.dumps(record, separators=(",", ":")) + "\n")
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


class AdvancedForecastingTests(unittest.TestCase):
    def test_ridge_and_nlinear_train_predict_and_evaluate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _build_dataset(root)
            for model_type in ("ridge", "nlinear"):
                with self.subTest(model_type=model_type):
                    output = root / model_type
                    metadata = train_local_forecast_v2(
                        dataset,
                        output,
                        model_type=model_type,
                    )
                    self.assertEqual(metadata["schema"], MODEL_SCHEMA)
                    self.assertEqual(metadata["deployment_mode"], "shadow_only")
                    self.assertIsNone(metadata["field_accuracy_claim"])
                    self.assertEqual(metadata["artifact"], "model.npz")
                    predictions = predict_local_forecast_v2(
                        dataset,
                        output / "model.json",
                        split="test",
                    )
                    data = np.load(dataset, allow_pickle=False)
                    self.assertEqual(predictions.shape, data["Y_test"].shape)
                    self.assertTrue(np.isfinite(predictions).all())
                    metrics = evaluate_local_forecast_v2(
                        dataset,
                        output / "model.json",
                    )
                    self.assertIn("uncertainty", metrics)
                    self.assertIn("physical_units", metrics["splits"]["test"])
                    self.assertIn(metrics["promotion_gate"]["status"], {"EXPERIMENTAL", "PROMISING_HOST_ONLY"})
                    self.assertFalse(metrics["promotion_gate"]["field_generalization_verified"])
                    self.assertFalse(metrics["promotion_gate"]["pi_shadow_verified"])

    def test_elasticnet_is_dependency_free_and_relocatable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _build_dataset(root)
            output = root / "elastic"
            metadata = train_local_forecast_v2(
                dataset,
                output,
                model_type="elasticnet",
                alpha=1e-4,
                l1_ratio=0.25,
                elasticnet_max_iter=300,
            )
            self.assertEqual(metadata["model_config"]["solver"], "fista_proximal_gradient")
            self.assertGreater(metadata["model_config"]["iterations_ran"], 0)
            predictions = predict_local_forecast_v2(
                dataset,
                output / "model.json",
                split="val",
            )
            self.assertTrue(np.isfinite(predictions).all())

    def test_tsmixer_lite_safe_checkpoint_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _build_dataset(root)
            output = root / "tsmixer"
            metadata = train_local_forecast_v2(
                dataset,
                output,
                model_type="tsmixer_lite",
                epochs=3,
                patience=2,
                batch_size=32,
                time_hidden=8,
                feature_hidden=8,
                blocks=1,
                dropout=0.0,
                device="cpu",
            )
            self.assertEqual(metadata["artifact"], "model.pt")
            self.assertGreater(metadata["param_count"], 0)
            predictions = predict_local_forecast_v2(
                dataset,
                output / "model.json",
                split="test",
                device="cpu",
            )
            self.assertTrue(np.isfinite(predictions).all())

    def test_artifact_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _build_dataset(root)
            output = root / "ridge"
            train_local_forecast_v2(dataset, output, model_type="ridge")
            with (output / "model.npz").open("ab") as handle:
                handle.write(b"tamper")
            with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                predict_local_forecast_v2(dataset, output / "model.json", split="test")

    def test_rolling_origin_uses_development_region_without_touching_final_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _build_dataset(root)
            result = evaluate_rolling_origin_local_v2(
                dataset,
                model_type="ridge",
                seeds=(17,),
                tsmixer_epochs=2,
                device="cpu",
            )
            self.assertFalse(result["final_test_touched"])
            self.assertGreaterEqual(result["fold_count"], 1)
            self.assertEqual(len(result["runs"]), result["fold_count"])
            self.assertIn("rmse_mean", result["aggregate"])
            self.assertIn("rmse_worst", result["aggregate"])

    def test_bakeoff_does_not_touch_final_test_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _build_dataset(root)
            result = run_local_bakeoff_v2(
                dataset,
                root / "bakeoff",
                model_types=("ridge",),
                seeds=(17,),
                rolling_origin=False,
            )
            self.assertFalse(result["final_test_evaluated"])
            self.assertFalse(result["runs"][0]["final_test_evaluated"])
            self.assertNotIn("test_rmse", result["runs"][0])
            self.assertIn("validation_rmse", result["runs"][0])


if __name__ == "__main__":
    unittest.main()
