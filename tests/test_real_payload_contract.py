import json
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iiot_ai_sensor_gateway.config import load_config
from iiot_ai_sensor_gateway.parser import PayloadParser
from iiot_ai_sensor_gateway.validation import ReadingValidator


FIXTURE = ROOT / "tests" / "fixtures" / "real_payload_samples.jsonl"


def _load_samples():
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class RealPayloadContractTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config/default.toml")

    def test_fixture_payloads_parse_with_v2_parser(self):
        parser = PayloadParser("raspi_gateway_01", "lab_default", allow_legacy_v1=False)
        samples = _load_samples()
        self.assertGreaterEqual(len(samples), 8)

        for sample in samples:
            payload = sample["payload"]
            reading = parser.parse(
                payload,
                receive_timestamp=sample.get("receive_timestamp"),
                radio_meta=sample.get("radio"),
            )
            self.assertEqual(reading.gateway_id, payload.get("gw", "raspi_gateway_01"))
            self.assertEqual(reading.room_id, payload.get("r", "lab_default"))
            self.assertEqual(reading.compact_version, 2)
            self.assertTrue(reading.event_id.startswith("se_"))
            self.assertIsNotNone(reading.timestamp)
            self.assertIsNotNone(reading.sensor.temperature_c)
            self.assertIsNotNone(reading.sensor.humidity_pct)

    def test_real_like_payload_validation_matches_contract_expectations(self):
        parser = PayloadParser("raspi_gateway_01", "lab_default", allow_legacy_v1=False)
        validator = ReadingValidator(
            self.config.validation_ranges,
            self.config.pipeline.sequence_gap_warn,
        )

        results = {}
        for sample in _load_samples():
            reading = parser.parse(
                sample["payload"],
                receive_timestamp=sample.get("receive_timestamp"),
                radio_meta=sample.get("radio"),
            )
            result = validator.validate(reading)
            results[sample["case"]] = result
            self.assertEqual(result.is_valid, sample["expected_valid"], sample["case"])
            for issue in sample.get("expected_issues", []):
                self.assertIn(issue, result.issues, sample["case"])

        self.assertIn("missing_pressure_hpa", results["missing_optional_pressure"].issues)
        self.assertIn("sensor_error", results["sensor_error"].issues)
        self.assertIn("sequence_gap", results["sequence_gap"].issues)
        self.assertIn("range_co_ppm", results["high_co"].issues)
        self.assertIn("range_bme_gas_ohm", results["high_gas"].issues)

    def test_uptime_uses_gateway_receive_timestamp(self):
        parser = PayloadParser("raspi_gateway_01", "lab_default", allow_legacy_v1=False)
        wrapped = next(
            sample for sample in _load_samples() if sample["case"] == "receiver_wrapped_payload"
        )
        reading = parser.parse(
            wrapped["payload"],
            receive_timestamp=wrapped["receive_timestamp"],
            radio_meta=wrapped["radio"],
        )

        self.assertEqual(wrapped["receiver_id"], "raspi_gateway_01_lora_rx")
        self.assertIn("raw_payload", wrapped)
        self.assertEqual(reading.node_id, "esp32c6_node_02")
        self.assertEqual(reading.node_timestamp, 77)
        self.assertEqual(reading.time_quality, "gateway_received")
        self.assertEqual(reading.timestamp, reading.receive_timestamp)
        self.assertEqual(reading.radio.rssi, -91)

    def test_event_id_is_stable_for_retry(self):
        parser = PayloadParser("raspi_gateway_01", "lab_default", allow_legacy_v1=False)
        sample = _load_samples()[0]["payload"]
        first = parser.parse(sample, receive_timestamp="2026-06-18T02:30:02Z")
        second = parser.parse(sample, receive_timestamp="2026-06-18T02:31:02Z")
        self.assertEqual(first.event_id, second.event_id)

    def test_invalid_version_and_flags_fail_closed(self):
        parser = PayloadParser("gw", "room", allow_legacy_v1=False)
        with self.assertRaisesRegex(ValueError, "unsupported compact sensor version"):
            parser.parse({"v": 99})
        with self.assertRaisesRegex(ValueError, "flags"):
            parser.parse(
                {
                    "v": 2,
                    "n": "node",
                    "r": "room",
                    "ts": 1,
                    "seq": 1,
                    "bid": "boot",
                    "st": "ok",
                    "q": "valid",
                    "f": {"bad": True},
                    "ok": {"bme688": "ok"},
                    "s": {"tc": 25},
                }
            )
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            parser.parse(
                {
                    "v": 2,
                    "n": "node",
                    "r": "room",
                    "ts": 1,
                    "seq": 1,
                    "bid": "boot",
                    "s": {"tc": 25},
                }
            )


if __name__ == "__main__":
    unittest.main()
