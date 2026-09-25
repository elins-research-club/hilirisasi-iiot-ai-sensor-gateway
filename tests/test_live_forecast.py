from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from iiot_ai_sensor_gateway.contracts import FeatureVector
from iiot_ai_sensor_gateway.live_forecast import LiveEdgeForecaster


class LiveForecastTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = Path(
            "deployment/model-manifests/co2_fits_pi5_20260828.json"
        )
        self.model = Path("models/pi5/data_co2/fits/model.pt")

    @staticmethod
    def _vector(index: int, *, seconds: int | None = None, present: bool = True) -> FeatureVector:
        offset = index * 60 if seconds is None else seconds
        return FeatureVector(
            "iiotgw",
            "737fa3925c5c2bba",
            "room_unassigned",
            datetime(2026, 9, 25, tzinfo=UTC) + timedelta(seconds=offset),
            {
                "co2_ppm": 600.0 + index,
                "has_co2_ppm": 1.0 if present else 0.0,
            },
        )

    def test_warmup_then_inference(self) -> None:
        runtime = LiveEdgeForecaster(self.manifest)
        result = None
        for index in range(16):
            result = runtime.process(self._vector(index))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["forecast_status"], "available_shadow")
        self.assertIn("co2_ppm", result["predicted"])
        self.assertEqual(result["model_manifest_id"], "co2-fits-pi5-20260828")
        self.assertEqual(result["runtime_backend"], "numpy_fits_v1")
        self.assertGreaterEqual(result["inference_latency_ms"], 0)

    def test_missing_feature_abstains(self) -> None:
        runtime = LiveEdgeForecaster(self.manifest)
        result = runtime.process(self._vector(0, present=False))
        self.assertEqual(result["status"], "abstain_missing_feature")
        self.assertEqual(result["model_manifest_id"], "co2-fits-pi5-20260828")

    def test_cadence_mismatch_clears_history(self) -> None:
        runtime = LiveEdgeForecaster(self.manifest)
        runtime.process(self._vector(0))
        result = runtime.process(self._vector(1, seconds=20))
        self.assertEqual(result["status"], "abstain_cadence_mismatch")
        self.assertEqual(result["model_manifest_id"], "co2-fits-pi5-20260828")
        next_result = runtime.process(self._vector(2, seconds=80))
        self.assertEqual(next_result["status"], "warming")
        self.assertEqual(next_result["samples"], 1)
        self.assertEqual(next_result["model_manifest_id"], "co2-fits-pi5-20260828")

    def test_numpy_export_matches_source_torch_checkpoint(self) -> None:
        if not self.model.is_file():
            self.skipTest("source PyTorch checkpoint is not bundled on target")
        try:
            import numpy as np
            import torch
        except ModuleNotFoundError:
            self.skipTest("host parity dependencies unavailable")
        from iiot_ai_sensor_gateway.edge_forecasting import load_edge_model

        runtime = LiveEdgeForecaster(self.manifest)
        model, checkpoint, _device = load_edge_model(self.model, "cpu")
        rng = np.random.default_rng(20260925)
        worst = 0.0
        for _ in range(64):
            history = rng.uniform(0.05, 0.95, size=(16, 1)).astype("float32")
            numpy_prediction = np.asarray(
                runtime._predict_normalized(history.tolist()), dtype="float32"
            )
            with torch.no_grad():
                torch_prediction = (
                    model(torch.from_numpy(history[None, :, :]))
                    .detach()
                    .cpu()
                    .numpy()[0]
                )
            worst = max(
                worst,
                float(np.max(np.abs(numpy_prediction - torch_prediction))),
            )
        self.assertLessEqual(worst, 2e-6)
        self.assertEqual(checkpoint["model_version"], "fits_edge_v2")


if __name__ == "__main__":
    unittest.main()
