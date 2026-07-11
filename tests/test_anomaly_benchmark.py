from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from iiot_ai_sensor_gateway.anomaly_benchmark import (
    EventInterval,
    benchmark_detection_file,
    benchmark_events,
    inject_labeled_normalized_fixture,
    intervals_from_boolean_records,
)
from iiot_ai_sensor_gateway.streaming_detection import run_streaming_detection


class AnomalyBenchmarkTests(unittest.TestCase):
    def test_boolean_samples_collapse_to_events(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        rows = [
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "is_anomaly": index in {3, 4, 8},
            }
            for index in range(10)
        ]
        events = intervals_from_boolean_records(rows, merge_gap_sec=60)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].start, start + timedelta(minutes=3))
        self.assertEqual(events[0].end, start + timedelta(minutes=4))

    def test_event_metrics_are_one_to_one_and_include_delay_false_alert_rate(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        truth = [
            EventInterval("truth-1", start + timedelta(hours=2), start + timedelta(hours=3)),
            EventInterval("truth-2", start + timedelta(hours=10), start + timedelta(hours=11)),
        ]
        detected = [
            EventInterval("det-1", start + timedelta(hours=2, minutes=5), start + timedelta(hours=2, minutes=10)),
            EventInterval("det-extra", start + timedelta(hours=6), start + timedelta(hours=6, minutes=5)),
        ]
        result = benchmark_events(
            detected,
            truth,
            observation_start=start,
            observation_end=start + timedelta(days=1),
        )
        self.assertEqual(result["true_positive_events"], 1)
        self.assertEqual(result["false_positive_events"], 1)
        self.assertEqual(result["false_negative_events"], 1)
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["f1"], 0.5)
        self.assertEqual(result["false_alerts_per_day"], 1.0)
        self.assertEqual(result["detection_delay"]["mean_sec"], 300.0)

    def test_fixture_stream_and_benchmark_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture = root / "fixture.jsonl"
            labels = root / "labels.json"
            detections = root / "detections.jsonl"
            benchmark = root / "benchmark.json"
            metadata = inject_labeled_normalized_fixture(
                fixture,
                labels,
                sample_count=180,
                interval_sec=60,
                event_start_indices=(80,),
                event_length=10,
            )
            self.assertEqual(metadata["status"], "SYNTHETIC_HARNESS_ONLY")
            run = run_streaming_detection(
                fixture,
                detections,
                feature_names=("temperature_c", "pm25_ug_m3"),
                backend="native",
                warmup_samples=40,
                anomaly_threshold=0.45,
                z_scale=2.0,
                page_hinkley_threshold=0.2,
            )
            self.assertEqual(run["processed"], 180)
            result = benchmark_detection_file(
                detections,
                labels,
                benchmark,
                merge_gap_sec=60,
                match_tolerance_sec=120,
            )
            self.assertEqual(result["truth_event_count"], 1)
            self.assertIn(result["status"], {"EXPERIMENTAL_FIXTURE_OR_LABEL_DEPENDENT"})
            self.assertTrue(benchmark.exists())
            label_data = json.loads(labels.read_text(encoding="utf-8"))
            self.assertIsNone(label_data["production_accuracy_claim"])

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            intervals_from_boolean_records(
                [{"timestamp": "2026-01-01T00:00:00", "is_anomaly": True}]
            )


if __name__ == "__main__":
    unittest.main()
