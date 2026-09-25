from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from iiot_ai_sensor_gateway.contracts import FeatureVector
from iiot_ai_sensor_gateway.live_forecast import LiveEdgeForecaster


class LiveForecastTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = Path("models/pi5/data_co2/fits/model.pt")
        if not self.model.is_file():
            self.skipTest("local Pi5 CO2 model evidence unavailable")

    def _manifest(self, root: Path) -> Path:
        digest = hashlib.sha256(self.model.read_bytes()).hexdigest()
        manifest = {
            "schema": "iiot.ai_sensor.model_manifest.v1",
            "id": "test-co2-fits",
            "model_path": str(self.model.resolve()),
            "model_sha256": digest,
            "target_names": ["co2_ppm"],
            "input_length": 16,
            "expected_cadence_sec": 60.0,
            "horizon_steps": 5,
            "horizon_duration_seconds": 300.0,
            "readiness": "PROMISING",
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

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
        with tempfile.TemporaryDirectory() as td:
            runtime = LiveEdgeForecaster(self._manifest(Path(td)))
            result = None
            for index in range(16):
                result = runtime.process(self._vector(index))
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["forecast_status"], "available_shadow")
            self.assertIn("co2_ppm", result["predicted"])
            self.assertEqual(result["model_manifest_id"], "test-co2-fits")
            self.assertGreaterEqual(result["inference_latency_ms"], 0)

    def test_missing_feature_abstains(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = LiveEdgeForecaster(self._manifest(Path(td)))
            result = runtime.process(self._vector(0, present=False))
            self.assertEqual(result["status"], "abstain_missing_feature")

    def test_cadence_mismatch_clears_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = LiveEdgeForecaster(self._manifest(Path(td)))
            runtime.process(self._vector(0))
            result = runtime.process(self._vector(1, seconds=20))
            self.assertEqual(result["status"], "abstain_cadence_mismatch")
            next_result = runtime.process(self._vector(2, seconds=80))
            self.assertEqual(next_result["status"], "warming")
            self.assertEqual(next_result["samples"], 1)


if __name__ == "__main__":
    unittest.main()
