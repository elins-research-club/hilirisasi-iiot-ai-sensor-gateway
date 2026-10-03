from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from iiot_ai_sensor_gateway.anomaly_research import (
    CATALOG_SCHEMA,
    anomaly_research_catalog,
    probe_anomaly_research_environment,
    run_static_anomaly_research,
    tspulse_preflight,
)


def _write_rows(path: Path, values: list[tuple[float, float]]) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        json.dumps(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "features": {"x": x, "y": y},
            },
            separators=(",", ":"),
        )
        + "\n"
        for index, (x, y) in enumerate(values)
    ]
    path.write_text("".join(rows), encoding="utf-8")


class AnomalyResearchTests(unittest.TestCase):
    def test_catalog_and_environment_are_fail_closed(self) -> None:
        catalog = anomaly_research_catalog()
        self.assertEqual(catalog["schema"], CATALOG_SCHEMA)
        by_id = {item["id"]: item for item in catalog["models"]}
        self.assertIn("isolation_forest", by_id)
        self.assertIn("ecod", by_id)
        self.assertIn("copod", by_id)
        self.assertIn("granite_tspulse_r1", by_id)
        self.assertFalse(by_id["granite_tspulse_r1"]["runtime_default"])
        environment = probe_anomaly_research_environment()
        self.assertFalse(environment["automatic_install"])

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "scikit-learn not installed")
    def test_isolation_forest_runs_with_separate_fit_and_eval_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fit = root / "fit.jsonl"
            evaluate = root / "eval.jsonl"
            output = root / "scores.jsonl"
            _write_rows(
                fit,
                [(0.20 + (index % 5) * 0.002, 0.40 + (index % 3) * 0.002) for index in range(80)],
            )
            _write_rows(
                evaluate,
                [(0.21, 0.40)] * 20 + [(0.95, 0.95)] * 4,
            )
            summary = run_static_anomaly_research(
                fit,
                evaluate,
                output,
                feature_names=("x", "y"),
                backend="isolation_forest",
                contamination=0.05,
                n_estimators=50,
            )
            self.assertEqual(summary["backend"], "sklearn.IsolationForest")
            rows = [json.loads(line) for line in output.read_text().splitlines() if line]
            self.assertEqual(len(rows), 24)
            self.assertTrue(all(0.0 <= row["anomaly_score"] <= 1.0 for row in rows))
            self.assertTrue(all("raw_backend_score" in row for row in rows))
            self.assertEqual(summary["fit_samples"], 80)
            self.assertEqual(summary["eval_samples"], 24)
            # Do not assert that a tiny synthetic fixture must be detected by
            # one unsupervised algorithm. Detection quality belongs in the
            # common labeled benchmark, not in the runtime contract test.

    def test_same_fit_and_eval_file_is_rejected_before_backend_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "same.jsonl"
            _write_rows(path, [(0.2, 0.4), (0.21, 0.41)])
            with self.assertRaisesRegex(ValueError, "must be different"):
                run_static_anomaly_research(
                    path,
                    path,
                    Path(tmp) / "scores.jsonl",
                    feature_names=("x", "y"),
                    backend="ecod",
                )

    def test_optional_pyod_failure_is_actionable_when_absent(self) -> None:
        if importlib.util.find_spec("pyod") is not None:
            self.skipTest("pyod installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fit = root / "fit.jsonl"
            evaluate = root / "eval.jsonl"
            _write_rows(fit, [(0.2, 0.4), (0.21, 0.41), (0.22, 0.42)])
            _write_rows(evaluate, [(0.2, 0.4), (0.9, 0.9)])
            with self.assertRaisesRegex(RuntimeError, "pyod"):
                run_static_anomaly_research(
                    fit,
                    evaluate,
                    root / "scores.jsonl",
                    feature_names=("x", "y"),
                    backend="copod",
                )

    def test_tspulse_preflight_blocks_short_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "short.jsonl"
            _write_rows(path, [(0.2, 0.4)] * 20)
            result = tspulse_preflight(path, feature_names=("x", "y"))
            self.assertEqual(result["status"], "BLOCKED_INSUFFICIENT_CONTEXT")
            self.assertEqual(result["minimum_points"], 1536)
            self.assertFalse(result["automatic_download"])


if __name__ == "__main__":
    unittest.main()
