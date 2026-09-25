import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iiot_ai_sensor_gateway.cli import build_parser
from iiot_ai_sensor_gateway.config import load_config
from iiot_ai_sensor_gateway.mqtt_contracts import (
    build_sensor_ai_event,
    build_sensor_status,
    data_topic,
    node_state_from_reading,
    status_topic,
)
from iiot_ai_sensor_gateway.parser import PayloadParser
from iiot_ai_sensor_gateway.real.serial_source import SerialLineSource
from iiot_ai_sensor_gateway.validation import ReadingValidator

HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None
HAS_TORCH = importlib.util.find_spec("torch") is not None


def _payload(*, sequence=1, boot_id="boot-a", node_time=77, flags=None):
    return {
        "v": 2,
        "gw": "gw-1",
        "n": "node-1",
        "r": "room-1",
        "ts": node_time,
        "seq": sequence,
        "bid": boot_id,
        "st": "ok",
        "q": "valid",
        "f": [] if flags is None else flags,
        "ok": {"bme688": "ok", "sen0466": "ok"},
        "s": {"tc": 28.0, "h": 60.0, "p": 1008.0, "bme": 18000.0, "co": 2.0},
    }


class ContractAndTimePolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config/default.toml")
        self.parser = PayloadParser("gw-1", "room-1", allow_legacy_v1=False)

    def test_uptime_is_not_unix_epoch(self):
        receive = "2026-07-10T06:00:00Z"
        reading = self.parser.parse(_payload(node_time=77), receive_timestamp=receive)
        self.assertEqual(reading.timestamp, datetime(2026, 7, 10, 6, tzinfo=UTC))
        self.assertEqual(reading.node_timestamp, 77)
        self.assertEqual(reading.time_quality, "gateway_received")

    def test_stable_event_id_across_retry(self):
        first = self.parser.parse(_payload(), receive_timestamp="2026-07-10T06:00:00Z")
        second = self.parser.parse(_payload(), receive_timestamp="2026-07-10T06:05:00Z")
        changed_boot = self.parser.parse(
            _payload(boot_id="boot-b"), receive_timestamp="2026-07-10T06:05:00Z"
        )
        self.assertEqual(first.event_id, second.event_id)
        self.assertNotEqual(first.event_id, changed_boot.event_id)

    def test_flags_and_schema_version_fail_closed(self):
        with self.assertRaises(ValueError):
            self.parser.parse(_payload(flags={"bad": True}))
        unsupported = _payload()
        unsupported["v"] = 3
        with self.assertRaises(ValueError):
            self.parser.parse(unsupported)
        legacy = _payload()
        legacy["v"] = 1
        with self.assertRaises(ValueError):
            self.parser.parse(legacy)

    def test_sequence_duplicate_out_of_order_and_reboot(self):
        validator = ReadingValidator(self.config.validation_ranges)
        one = self.parser.parse(_payload(sequence=1), receive_timestamp="2026-07-10T06:00:00Z")
        duplicate = self.parser.parse(
            _payload(sequence=1), receive_timestamp="2026-07-10T06:00:01Z"
        )
        older = self.parser.parse(_payload(sequence=0), receive_timestamp="2026-07-10T06:00:02Z")
        reboot = self.parser.parse(
            _payload(sequence=0, boot_id="boot-b"), receive_timestamp="2026-07-10T06:00:03Z"
        )
        self.assertTrue(validator.validate(one).is_valid)
        self.assertIn("duplicate_sequence", validator.validate(duplicate).issues)
        self.assertIn("out_of_order_sequence", validator.validate(older).issues)
        reboot_result = validator.validate(reboot)
        self.assertTrue(reboot_result.is_valid)
        self.assertIn("node_reboot", reboot_result.issues)

    def test_validator_state_is_bounded(self):
        validator = ReadingValidator(self.config.validation_ranges, state_max_entries=2)
        for index in range(3):
            reading = self.parser.parse(
                _payload(sequence=index, boot_id=f"boot-{index}"),
                receive_timestamp=f"2026-07-10T06:0{index}:00Z",
            )
            self.assertTrue(validator.validate(reading).is_valid)
        self.assertEqual(len(validator.state.last_sequence), 2)
        self.assertEqual(len(validator.state.last_boot), 1)
        self.assertEqual(len(validator.state.seen_event_ids), 2)
        self.assertNotIn(("node-1", "boot-0"), validator.state.last_sequence)

    def test_topic_freeze_and_node_silent_policy(self):
        reading = self.parser.parse(_payload(), receive_timestamp="2026-07-10T06:00:00Z")
        self.assertEqual(data_topic("gw-1"), "iot/gw-1/data")
        self.assertEqual(status_topic("gw-1"), "iot/gw-1/status/sensor")
        state = node_state_from_reading(
            reading,
            now=reading.receive_timestamp + timedelta(seconds=181),
            silent_after_sec=180,
        )
        self.assertEqual(state["node_health"], "stale")
        status = build_sensor_status(gateway_id="gw-1", node_states=[state])
        self.assertEqual(status["schema_version"], "sensor_status.v1")

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema is optional")
    def test_sensor_ai_and_status_schemas(self):
        import jsonschema

        compact = _payload()
        compact["lora"] = {"rssi": -87, "snr": 8.5}
        compact_schema = json.loads((ROOT / "schemas/compact_sensor.v2.schema.json").read_text())
        jsonschema.Draft202012Validator(compact_schema).validate(compact)
        reading = self.parser.parse(compact, receive_timestamp="2026-07-10T06:00:00Z")
        event = build_sensor_ai_event(reading)
        status = build_sensor_status(
            gateway_id="gw-1",
            node_states=[node_state_from_reading(reading, now=reading.receive_timestamp)],
        )
        event_schema = json.loads((ROOT / "schemas/sensor_ai.v1.schema.json").read_text())
        status_schema = json.loads((ROOT / "schemas/sensor_status.v1.schema.json").read_text())
        jsonschema.Draft202012Validator(event_schema).validate(event)
        jsonschema.Draft202012Validator(status_schema).validate(status)

        compact_v3 = {
            "v": 3,
            "gw": "gw-1",
            "n": "node-1",
            "r": "room-1",
            "ts": "2026-09-10T06:30:00+00:00",
            "tb": "rfc3339",
            "seq": 2,
            "bid": "boot-v3",
            "pp": "hardware_only",
            "fw": "sensor-fw-3.0.0",
            "cfg": "board-a1",
            "cal": "factory-unverified",
            "hs": "partial",
            "f": ["co2_warmup"],
            "ok": {
                "bme688": "ok",
                "sen0466": "ok",
                "sen0574": "ok",
                "sen0321": "ok",
                "mhz19": "warming",
                "pms7003t": "ok",
                "ina226": "ok",
            },
            "s": {"tc": 28.0, "h": 60.0, "co2": None},
        }
        compact_v3_schema = json.loads(
            (ROOT / "schemas/compact_sensor.v3.schema.json").read_text()
        )
        jsonschema.Draft202012Validator(compact_v3_schema).validate(compact_v3)
        v3_reading = self.parser.parse(
            compact_v3, receive_timestamp="2026-09-10T06:30:01Z"
        )
        v3_event = build_sensor_ai_event(v3_reading)
        self.assertEqual(v3_event["source"]["compact_version"], 3)
        jsonschema.Draft202012Validator(event_schema).validate(v3_event)


class SerialSourceTests(unittest.TestCase):
    class FakeConnection:
        def __init__(self, rows):
            self.rows = iter(rows)
            self.closed = False

        def readline(self):
            return next(self.rows)

        def close(self):
            self.closed = True

    def test_empty_read_has_explicit_sleep(self):
        sleeps = []
        connection = self.FakeConnection([b"", b'{"v":2}\n'])
        source = SerialLineSource(
            "/dev/fake",
            serial_factory=lambda *args, **kwargs: connection,
            sleep_fn=sleeps.append,
            idle_sleep_sec=0.05,
        )
        self.assertEqual(next(source.iter_lines()), '{"v":2}')
        self.assertEqual(sleeps, [0.05])

    def test_reconnect_uses_backoff(self):
        sleeps = []
        calls = {"count": 0}

        def factory(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("disconnected")
            return self.FakeConnection([b'{"v":2}\n'])

        source = SerialLineSource(
            "/dev/fake",
            serial_factory=factory,
            sleep_fn=sleeps.append,
            reconnect_initial_sec=0.5,
            reconnect_max_sec=2.0,
        )
        self.assertEqual(next(source.iter_lines()), '{"v":2}')
        self.assertEqual(sleeps, [0.5])


class SafetyRegressionTests(unittest.TestCase):
    def test_cli_no_args_and_invalid_numeric_are_rejected(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])
        with self.assertRaises(SystemExit):
            parser.parse_args(["simulate", "--count", "0"])

    def test_active_sensor_code_has_no_forbidden_or_legacy_hardware_path(self):
        paths = [ROOT / "src", ROOT / "firmware/esp32-c6-sensor-node/src", ROOT / "firmware/esp32-c6-sensor-node/include"]
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for directory in paths
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".c", ".cc", ".cpp", ".h", ".hpp"}
        )
        for token in (
            "dis" + "tance" + "_mm",
            '"' + "di" + "st" + '"',
            "V" + "L" + "53",
        ):
            self.assertNotIn(token, text)
        self.assertNotIn("SEN" + "0377", text)

    def test_hardware_uart_paths_are_bounded_and_fail_closed(self):
        firmware = ROOT / "firmware/esp32-c6-sensor-node"
        bridge = (firmware / "src/uart_bridge.cpp").read_text(encoding="utf-8")
        sensors = (firmware / "src/sensors.cpp").read_text(encoding="utf-8")
        config = (firmware / "include/config.h").read_text(encoding="utf-8")
        self.assertIn("REG_TXLVL", bridge)
        self.assertIn("REG_RXLVL", bridge)
        self.assertIn("elapsedMs(started) >= timeout_ms", bridge)
        self.assertIn("winsenChecksum", sensors)
        self.assertIn("validatePlantowerFrame", sensors)
        self.assertIn("MHZ19_WARMUP_MS", config)
        self.assertIn("PMS7003T_WARMUP_MS", config)
        self.assertIn("IIOT_ENABLE_BOSCH_BME68X", config)

    def test_bme_profile_and_systemd_home_access_regression(self):
        firmware = ROOT / "firmware/esp32-c6-sensor-node/platformio.ini"
        platformio = firmware.read_text(encoding="utf-8")
        base_profile = platformio.split("[env:mock]", maxsplit=1)[0]
        bme_profile = platformio.split("[env:hardware-bme68x]", maxsplit=1)[1]
        self.assertIn("build_unflags", base_profile)
        self.assertIn("-fuse-cxa-atexit", base_profile)
        self.assertIn("IIOT_ENABLE_BOSCH_BME68X=1", bme_profile)

        service = (ROOT / "systemd/iiot-ai-sensor-gateway.service").read_text(encoding="utf-8")
        self.assertIn("WorkingDirectory=/home/pi/iiot-ai-sensor-gateway", service)
        self.assertIn("ProtectHome=read-only", service)
        active_directives = [
            line.strip() for line in service.splitlines() if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertNotIn("ProtectHome=true", active_directives)

    @unittest.skipUnless(HAS_TORCH, "PyTorch is optional")
    def test_forecast_loader_rejects_invalid_checkpoint(self):
        import torch
        from iiot_ai_sensor_gateway.forecasting import _load_model

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.pt"
            torch.save({"unexpected": 1}, path)
            with self.assertRaisesRegex(ValueError, "missing required keys"):
                _load_model(path, device="cpu")


if __name__ == "__main__":
    unittest.main()
