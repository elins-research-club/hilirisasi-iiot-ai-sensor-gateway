from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from iiot_ai_sensor_gateway.anomaly_benchmark import (
    benchmark_detection_file,
    inject_labeled_normalized_fixture,
)
from iiot_ai_sensor_gateway.streaming_detection import (
    EWMACUSUMAnomaly,
    RollingMADAnomaly,
    StreamingConfig,
    StreamingDetectionPipeline,
    build_mad_pipeline,
    run_streaming_detection,
)


class AnomalyV2Tests(unittest.TestCase):
    def test_rolling_mad_scores_before_learning_and_attributes_feature(self) -> None:
        model = RollingMADAnomaly(window_size=32, min_samples=8, z_scale=3.5)
        for index in range(12):
            point = {"a": 0.2 + (index % 3) * 0.001, "b": 0.5}
            self.assertLessEqual(model.score_one(point), 1.0)
            model.learn_one(point)
        score = model.score_one({"a": 0.95, "b": 0.5})
        self.assertGreater(score, 0.8)
        self.assertEqual(max(model.last_feature_scores, key=model.last_feature_scores.get), "a")

    def test_persistence_and_hysteresis_suppress_single_spike(self) -> None:
        class SequenceModel:
            def __init__(self) -> None:
                self.scores = iter((0.0, 0.9, 0.1, 0.9, 0.9, 0.6, 0.2, 0.2))
                self.last_feature_scores = {"x": 0.0}

            def score_one(self, _features):
                score = next(self.scores)
                self.last_feature_scores = {"x": score}
                return score

            def learn_one(self, _features):
                return self

        pipeline = StreamingDetectionPipeline(
            SequenceModel(),
            {},
            StreamingConfig(
                warmup_samples=1,
                anomaly_threshold=0.8,
                persistence_samples=2,
                recovery_samples=2,
                clear_threshold=0.3,
            ),
        )
        states = [pipeline.process({"x": 0.2})["is_anomaly"] for _ in range(8)]
        self.assertEqual(states, [False, False, False, False, True, True, True, False])

    def test_ewma_cusum_is_finite(self) -> None:
        model = EWMACUSUMAnomaly(alpha=0.1, drift=0.01, threshold=0.2)
        for _ in range(20):
            score = model.score_one({"x": 0.2})
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)
            model.learn_one({"x": 0.2})
        shifted = model.score_one({"x": 0.8})
        self.assertGreater(shifted, 0.0)

    def test_mad_backend_runs_through_event_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture.jsonl"
            labels = root / "labels.json"
            detections = root / "detections.jsonl"
            benchmark = root / "benchmark.json"
            inject_labeled_normalized_fixture(
                fixture,
                labels,
                sample_count=220,
                event_start_indices=(100,),
                event_length=15,
            )
            summary = run_streaming_detection(
                fixture,
                detections,
                feature_names=("temperature_c", "pm25_ug_m3"),
                backend="mad",
                warmup_samples=32,
                anomaly_threshold=0.65,
                window_size=64,
                mad_min_samples=16,
                persistence_samples=2,
                recovery_samples=3,
                clear_threshold=0.4,
            )
            self.assertEqual(summary["backend"], "native.RollingMAD+PageHinkley")
            rows = [json.loads(line) for line in detections.read_text().splitlines() if line]
            attributed = [row for row in rows if row.get("anomaly_main_feature")]
            self.assertTrue(attributed)
            result = benchmark_detection_file(
                detections,
                labels,
                benchmark,
                merge_gap_sec=120,
                match_tolerance_sec=120,
            )
            self.assertIn("false_alerts_per_day", result)
            self.assertIn("detection_delay", result)

    def test_mad_builder_validates_clear_threshold(self) -> None:
        with self.assertRaisesRegex(ValueError, "clear_threshold"):
            build_mad_pipeline(
                feature_names=("x",),
                anomaly_threshold=0.3,
                clear_threshold=0.4,
            )


if __name__ == "__main__":
    unittest.main()
