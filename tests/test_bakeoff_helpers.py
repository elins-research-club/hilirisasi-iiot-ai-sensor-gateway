from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

from iiot_ai_sensor_gateway.parser import PayloadParser


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BakeoffHelperTests(unittest.TestCase):
    def test_compact_v2_zero_flags_are_empty_but_bitfields_fail(self):
        self.assertEqual(PayloadParser._flags(0), ())
        self.assertEqual(PayloadParser._flags(""), ())
        with self.assertRaises(ValueError):
            PayloadParser._flags(3)

    def test_dataset_converter_emits_contract_compatible_control_fields(self):
        converter = _load_script("dataset_record_to_compact.py")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.jsonl"
            output = tmp_path / "compact.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "schema_version": "iiot.dataset_record.v1",
                        "record_id": "r1",
                        "dataset_id": "fixture",
                        "lane": "public_proxy",
                        "device_id": "d1",
                        "site_id": "s1",
                        "timestamp": "2026-07-11T00:00:00+00:00",
                        "sensor": {"temperature_c": 25.0, "humidity_pct": 50.0},
                        "quality": {"valid": True},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stats = converter.convert(
                source,
                output,
                gateway_id="test_gateway",
                include_reference_as_sensor=False,
            )
            self.assertEqual(stats["written"], 1)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["q"], "valid")
            self.assertEqual(payload["f"], "")
            self.assertEqual(payload["ok"], {})
            parsed = PayloadParser("fallback", "fallback").parse(payload)
            self.assertEqual(parsed.node_id, "d1")
            self.assertEqual(parsed.flags, ())

    def test_download_catalog_uses_stable_filenames(self):
        catalog = json.loads((ROOT / "datasets/catalog.json").read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in catalog["datasets"]}
        self.assertEqual(entries["uci_air_quality_360"]["download_filename"], "uci_air_quality_360.zip")
        self.assertEqual(
            entries["zenodo_fidas_pm_reference_7198378"]["download_filename"],
            "zenodo_fidas_pm_reference_7198378.csv",
        )

    def test_runner_dry_run_state_is_atomic_and_resume_descriptive(self):
        runner_module = _load_script("laptop_bakeoff_runner.py")
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            runner = runner_module.Runner(
                python="python3",
                state_path=state_path,
                artifact_root=Path(tmp) / "artifacts",
                force=False,
                dry_run=True,
            )
            runner.run_step("fixture", ["--version"], [])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["schema"], runner_module.STATE_SCHEMA)
            self.assertEqual(state["steps"]["fixture"]["status"], "dry_run")
            self.assertEqual(len(state["steps"]["fixture"]["fingerprint"]), 64)

    def test_runner_reexecutes_when_declared_input_changes(self):
        runner_module = _load_script("laptop_bakeoff_runner.py")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_path = tmp_path / "state.json"
            input_path = tmp_path / "input.txt"
            output_path = tmp_path / "output.txt"
            input_path.write_text("first", encoding="utf-8")
            runner = runner_module.Runner(
                python=sys.executable,
                state_path=state_path,
                artifact_root=tmp_path / "artifacts",
                force=False,
                dry_run=False,
            )
            command = [
                "-c",
                f"from pathlib import Path; Path({str(output_path)!r}).write_text(Path({str(input_path)!r}).read_text())",
            ]
            runner.run_step("fixture", command, [output_path], input_paths=[input_path])
            first_fingerprint = runner.state["steps"]["fixture"]["fingerprint"]
            input_path.write_text("second", encoding="utf-8")
            runner.run_step("fixture", command, [output_path], input_paths=[input_path])
            self.assertEqual(output_path.read_text(encoding="utf-8"), "second")
            self.assertNotEqual(first_fingerprint, runner.state["steps"]["fixture"]["fingerprint"])

    def test_outputs_ready_allows_probe_only_steps(self):
        runner_module = _load_script("laptop_bakeoff_runner.py")
        self.assertTrue(runner_module.outputs_ready([]))

    def test_normalization_clipping_gate_checks_worst_feature(self):
        runner_module = _load_script("laptop_bakeoff_runner.py")
        report = {
            "schema": "iiot.ai_sensor.normalization_report.v1",
            "clip_fraction": 0.01,
            "fields": {
                "temperature_c": {"seen": 100, "clip_fraction": 0.0},
                "co2_ppm": {"seen": 100, "clip_fraction": 0.20},
            },
        }
        result = runner_module.normalization_clipping_gate(
            report, max_clip_fraction=0.05
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["worst_field"], "co2_ppm")
        self.assertAlmostEqual(result["worst_field_clip_fraction"], 0.20)

    def test_normalization_clipping_gate_passes_clean_lane(self):
        runner_module = _load_script("laptop_bakeoff_runner.py")
        report = {
            "schema": "iiot.ai_sensor.normalization_report.v1",
            "clip_fraction": 0.01,
            "fields": {
                "temperature_c": {"seen": 100, "clip_fraction": 0.01},
                "co2_ppm": {"seen": 100, "clip_fraction": 0.02},
            },
        }
        result = runner_module.normalization_clipping_gate(
            report, max_clip_fraction=0.05
        )
        self.assertEqual(result["status"], "PASS")

    def test_lane_windows_match_locked_bakeoff_methodology(self):
        runner_module = _load_script("laptop_bakeoff_runner.py")
        lanes = runner_module.lane_definitions()
        self.assertEqual(lanes["gary"].window_size, 48)
        self.assertEqual(lanes["uci"].window_size, 48)
        self.assertEqual(lanes["fidas"].window_size, 12)
        self.assertEqual(lanes["sim"].window_size, 12)

    def test_new_run_artifacts_use_isolated_root_and_non_empty_root_is_blocked(self):
        runner_module = _load_script("laptop_bakeoff_runner.py")
        with tempfile.TemporaryDirectory() as tmp:
            artifact_root = Path(tmp) / "models" / "bakeoff_runs" / "run_1"
            lanes = runner_module.lane_definitions(artifact_root)
            self.assertEqual(lanes["gary"].models_root, artifact_root / "gary")
            artifact_root.mkdir(parents=True)
            (artifact_root / "marker").write_text("existing", encoding="utf-8")
            with self.assertRaises(SystemExit):
                runner_module.ensure_artifact_root_safe(artifact_root, resume=False)
            runner_module.ensure_artifact_root_safe(artifact_root, resume=True)

    def test_run_path_guards_block_traversal_reparse_points_and_implicit_resume(self):
        runner_module = _load_script("laptop_bakeoff_runner.py")
        for value in ("models/bakeoff", "models/bakeoff_runs", "../models/bakeoff_runs/run"):
            with self.assertRaises(SystemExit):
                runner_module.resolve_artifact_root(value)

        original_root = runner_module.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "repo"
            runs_root = fake_root / "models" / "bakeoff_runs"
            runs_root.mkdir(parents=True)
            try:
                os.symlink(tmp, runs_root / "outside", target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink fixture unavailable: {exc}")
            runner_module.ROOT = fake_root
            try:
                with self.assertRaises(SystemExit):
                    runner_module.resolve_artifact_root("models/bakeoff_runs/outside")
            finally:
                runner_module.ROOT = original_root

        with self.assertRaises(SystemExit):
            runner_module.main(["--resume", "--dry-run", "--lanes", "sim"])

    def test_remote_enqueue_default_workdir_is_resolved_by_windows_agent(self):
        script = ROOT / "scripts" / "remote" / "enqueue_job.py"
        with tempfile.TemporaryDirectory() as tmp:
            # The script's ROOT is fixed to the repo, so capture and remove the
            # generated ignored runtime file after asserting the payload.
            job_id = "unit-test-empty-workdir"
            target = ROOT / "jobs" / "inbox" / f"{job_id}.job.json"
            try:
                subprocess.run(
                    [sys.executable, str(script), "--job-id", job_id, "--command", "echo ok"],
                    check=True,
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                )
                payload = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(payload["workdir"], "")
            finally:
                target.unlink(missing_ok=True)

    def test_summary_aggregates_nested_seed_runs(self):
        summary = _load_script("summarize_bakeoff.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for seed, skill in ((42, 0.1), (43, 0.3)):
                path = root / "lane" / "fits" / f"seed_{seed}" / "metrics.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "device": "cuda",
                            "model_readiness": "EXPERIMENTAL",
                            "data_quality": {"status": "PASS"},
                            "baseline_gate": {
                                "passed": False,
                                "baseline_passed": True,
                                "data_quality_passed": True,
                                "rmse_skill_score": skill,
                                "model_rmse": 0.2,
                                "per_target_wins": 1,
                                "effective_target_count": 2,
                                "target_count": 2,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            result = summary.build_summary(root)
            aggregate = result["lanes"]["lane"]["aggregates"]["fits"]
            self.assertEqual(aggregate["run_count"], 2)
            self.assertAlmostEqual(aggregate["skill"]["mean"], 0.2)
            self.assertGreater(aggregate["skill"]["std"], 0.0)

    def test_summary_excludes_legacy_root_run_when_seed_runs_exist(self):
        summary = _load_script("summarize_bakeoff.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative, skill in (
                ("lane/fits/metrics.json", 0.9),
                ("lane/fits/seed_42/metrics.json", 0.1),
                ("lane/fits/seed_43/metrics.json", 0.3),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "model_readiness": "PROMISING",
                            "baseline_gate": {
                                "passed": True,
                                "baseline_passed": True,
                                "data_quality_passed": True,
                                "rmse_skill_score": skill,
                                "model_rmse": 0.2,
                                "per_target_wins": 1,
                                "effective_target_count": 1,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            result = summary.build_summary(root)
            aggregate = result["lanes"]["lane"]["aggregates"]["fits"]
            self.assertEqual(aggregate["run_count"], 2)
            self.assertAlmostEqual(aggregate["skill"]["mean"], 0.2)
            self.assertFalse(result["lanes"]["lane"]["runs"]["fits"]["aggregate_included"])
            self.assertTrue(result["lanes"]["lane"]["runs"]["fits/seed_42"]["aggregate_included"])

    def test_summary_excludes_runner_preparation_directory(self):
        summary = _load_script("summarize_bakeoff.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metrics_path = root / "_run_data" / "sim" / "fits" / "metrics.json"
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(
                json.dumps(
                    {
                        "model_readiness": "PROMISING",
                        "baseline_gate": {
                            "passed": True,
                            "baseline_passed": True,
                            "data_quality_passed": True,
                            "rmse_skill_score": 0.1,
                            "model_rmse": 0.2,
                            "per_target_wins": 1,
                            "effective_target_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = summary.build_summary(root)
            self.assertNotIn("_run_data", result["lanes"])

    def test_summary_reads_lstm_metrics_schema(self):
        summary = _load_script("summarize_bakeoff.py")
        metrics = {
            "device": "cuda",
            "model_readiness": "PROMISING",
            "baseline_comparison_status": "BEATS_BASELINE",
            "splits": {
                "test": {
                    "lstm": {"overall_rmse": 0.2},
                    "baseline_delta": {
                        "overall_rmse_skill_score": 0.3,
                        "per_target": {
                            "a": {"beats_baseline": True},
                            "b": {"beats_baseline": False},
                        },
                    },
                }
            },
        }
        result = summary.summarize_metrics(metrics, Path("metrics.json"))
        self.assertEqual(result["skill"], 0.3)
        self.assertEqual(result["wins"], "1/2")
        self.assertTrue(result["passed"])
        self.assertEqual(result["test_rmse"], 0.2)
        self.assertEqual(result["device"], "cuda")


if __name__ == "__main__":
    unittest.main()
