from __future__ import annotations

import csv
import configparser
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HardprogAdapterTests(unittest.TestCase):
    def test_no2_adapter_emits_schema_v3_and_converts_voltage_to_raw_mv(self):
        adapter = _load_script("adapt_hardprog_csv.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data_no2.csv").write_text(
                "timestamp_ms;raw_adc;voltage_V\n116305;2897;2.33\n",
                encoding="utf-8",
            )
            payload = adapter.build_payloads(root)[0]
            self.assertEqual(payload["tb"], "uptime_s")
            self.assertEqual(payload["ts"], 116.305)
            self.assertEqual(payload["s"]["n2mv"], 2330.0)
            self.assertEqual(payload["ok"]["sen0574"], "ok")
            self.assertEqual(payload["ok"]["bme688"], "missing")
            self.assertNotIn("src", payload)
            self.assertNotIn("q", payload)
            self.assertNotIn("st", payload)
            try:
                import jsonschema
            except ImportError:
                self.skipTest("jsonschema is optional")
            schema = json.loads((ROOT / "schemas/compact_sensor.v3.schema.json").read_text())
            jsonschema.Draft202012Validator(schema).validate(payload)


class HardprogForecastToolTests(unittest.TestCase):
    def test_builder_uses_source_cadence_and_purge_gap(self):
        builder = _load_script("build_hardprog_forecast_npz.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "data_co2.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(["timestamp_ms", "co2_ppm"])
                for index in range(600):
                    writer.writerow([index * 1000, 400.0 + (index % 17)])
            npz_path = root / "co2.npz"
            meta_path = root / "co2.json"
            meta = builder.build_npz("data_co2", root, npz_path, meta_path)
            self.assertAlmostEqual(meta["cadence_seconds"], 1.0)
            self.assertEqual(meta["purge_gap_steps"], 5)
            self.assertEqual(meta["horizon_duration_seconds"], 5.0)
            with np.load(npz_path) as data:
                self.assertEqual(int(data["purge_gap_steps"][0]), 5)
                self.assertAlmostEqual(float(data["cadence_seconds"][0]), 1.0)
                self.assertGreater(len(data["X_val"]), 0)
                self.assertGreater(len(data["X_test"]), 0)

    def test_tof_is_not_an_environmental_forecast_lane(self):
        builder = _load_script("build_hardprog_forecast_npz.py")
        self.assertNotIn("data_tof_1", builder.LANES)

    def test_direct_builder_invocation_works_without_pythonpath(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root / "data_co2.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(["timestamp_ms", "co2_ppm"])
                for index in range(600):
                    writer.writerow([index * 1000, 400.0 + (index % 17)])
            out_dir = root / "out"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/build_hardprog_forecast_npz.py"),
                    "--csv-dir", str(root),
                    "--out-dir", str(out_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("data_co2", result.stdout)
            self.assertTrue((out_dir / "data_co2_dataset.npz").exists())


class HardprogStreamToolTests(unittest.TestCase):
    def test_stream_builder_preserves_uptime_and_rejects_co2_error_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "data_co2.csv"
            csv_path.write_text(
                "timestamp_ms;co2_ppm\n1000;400\n2000;-1\n3000;500\n",
                encoding="utf-8",
            )
            out_dir = root / "stream"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/build_hardprog_stream_input.py"), str(root), str(out_dir)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            rows = [json.loads(line) for line in (out_dir / "data_co2_stream.jsonl").read_text().splitlines()]
            self.assertEqual(rows[0]["node_timestamp_ms"], 1000.0)
            self.assertEqual(rows[0]["timestamp_basis"], "uptime_ms")
            self.assertNotIn("timestamp", rows[0])
            self.assertEqual(rows[1]["features"], {})
            self.assertEqual(rows[1]["invalid_fields"], ["co2_ppm"])

            normalized = out_dir / "data_co2_norm_stream.jsonl"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/normalize_stream_input.py"),
                 str(out_dir / "data_co2_stream.jsonl"), str(normalized)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            normalized_rows = [json.loads(line) for line in normalized.read_text().splitlines()]
            self.assertEqual(normalized_rows[1]["features"], {})
            self.assertEqual(normalized_rows[1]["node_timestamp_ms"], 2000.0)
            self.assertEqual(normalized_rows[1]["timestamp_basis"], "uptime_ms")


class Pi5CollectorTests(unittest.TestCase):
    def test_collectors_exclude_hybrid_tof_lane(self):
        for name in ("collect_pi5_eval.py", "collect_pi5_results.py", "collect_pi5_summary.py"):
            collector = _load_script(name)
            self.assertEqual(
                collector.SENSOR_LANES,
                {
                    "data_bme",
                    "data_mentah_bme688",
                    "data_co2",
                    "data_no2",
                    "data_pms_1",
                    "data_INA226",
                },
            )


class BenchmarkHostLabelTests(unittest.TestCase):
    def test_explicit_hardware_label_override_is_honored(self):
        benchmark = _load_script("benchmark_inference.py")
        old = os.environ.get("IIOT_HARDWARE_LABEL")
        try:
            os.environ["IIOT_HARDWARE_LABEL"] = "test-host"
            self.assertEqual(benchmark.detect_hardware_label(), "test-host")
        finally:
            if old is None:
                os.environ.pop("IIOT_HARDWARE_LABEL", None)
            else:
                os.environ["IIOT_HARDWARE_LABEL"] = old


class FirmwareBuildInputTests(unittest.TestCase):
    def test_platformio_sdkconfig_defaults_exist_for_fresh_clone(self):
        firmware = ROOT / "firmware" / "esp32-c6-sensor-node"
        config = configparser.ConfigParser()
        config.read(firmware / "platformio.ini", encoding="utf-8")

        referenced = set()
        for section in ("env:mock", "env:hardware"):
            raw = config[section]["board_build.cmake_extra_args"]
            marker = "SDKCONFIG_DEFAULTS="
            self.assertIn(marker, raw)
            value = raw.split(marker, 1)[1].strip().strip('"')
            referenced.add(value)

        self.assertEqual(
            referenced,
            {"sdkconfig.mock.defaults", "sdkconfig.hardware.defaults"},
        )
        for relative in sorted(referenced):
            path = firmware / relative
            self.assertTrue(path.is_file(), f"missing PlatformIO build input: {path}")
            self.assertGreater(path.stat().st_size, 0, f"empty PlatformIO build input: {path}")


if __name__ == "__main__":
    unittest.main()
