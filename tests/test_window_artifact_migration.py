from __future__ import annotations

import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iiot_ai_sensor_gateway.cli import main
from iiot_ai_sensor_gateway.simulator import write_simulation
from iiot_ai_sensor_gateway.window_paths import (
    CANONICAL_WINDOWS_FILENAME,
    LEGACY_WINDOWS_FILENAME,
    resolve_windows_path,
)


class WindowArtifactMigrationTests(unittest.TestCase):
    def test_resolver_falls_back_both_directions_only_when_requested_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            canonical = root / CANONICAL_WINDOWS_FILENAME
            legacy = root / LEGACY_WINDOWS_FILENAME

            legacy.write_text('{"source":"legacy"}\n', encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.assertEqual(resolve_windows_path(canonical), legacy)
            self.assertEqual(len(caught), 1)

            canonical.write_text('{"source":"canonical"}\n', encoding="utf-8")
            self.assertEqual(resolve_windows_path(canonical), canonical)
            self.assertEqual(resolve_windows_path(legacy), legacy)

            legacy.unlink()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.assertEqual(resolve_windows_path(legacy), canonical)
            self.assertEqual(len(caught), 1)

    def test_run_writes_one_canonical_window_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payloads = root / "payloads.jsonl"
            output = root / "processed"
            write_simulation(payloads, "normal", count=20, nodes=1, interval_sec=60)

            result = main(
                [
                    "run",
                    "--config",
                    str(ROOT / "config/default.toml"),
                    "--input-file",
                    str(payloads),
                    "--output-dir",
                    str(output),
                ]
            )

            self.assertEqual(result, 0)
            canonical = output / CANONICAL_WINDOWS_FILENAME
            legacy = output / LEGACY_WINDOWS_FILENAME
            self.assertTrue(canonical.is_file())
            self.assertGreater(canonical.stat().st_size, 0)
            self.assertFalse(legacy.exists())
            first = json.loads(canonical.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first["shape"][0], 12)


if __name__ == "__main__":
    unittest.main()
