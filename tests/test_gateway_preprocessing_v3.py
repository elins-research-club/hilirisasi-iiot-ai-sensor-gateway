from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from iiot_ai_sensor_gateway.parser import PayloadParser
from iiot_ai_sensor_gateway.preprocessing import GatewaySemanticPreprocessor
from iiot_ai_sensor_gateway.resampling import resample
from iiot_ai_sensor_gateway.validation import ReadingValidator
from iiot_ai_sensor_gateway.contracts import FeatureVector
from iiot_ai_sensor_gateway.features import FEATURE_NAMES
from iiot_ai_sensor_gateway.windowing import WindowBuilder

ROOT = Path(__file__).resolve().parents[1]


def _v3_payload(
    *,
    node: str = "node-1",
    boot: str = "boot-a",
    sequence: int = 1,
    timestamp: str = "2026-07-11T00:00:00+00:00",
    temperature: float = 20.0,
) -> dict:
    return {
        "v": 3,
        "gw": "gw-1",
        "n": node,
        "r": "room-a",
        "ts": timestamp,
        "tb": "rfc3339",
        "seq": sequence,
        "bid": boot,
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
        "s": {"tc": temperature, "h": 50.0, "co2": None},
    }


class CompactV3AndGatewayPreprocessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = PayloadParser("fallback-gw", "fallback-room", allow_legacy_v1=False)
        self.validator = ReadingValidator({"temperature_c": (-10.0, 80.0)})

    def test_v3_parser_preserves_hardware_provenance(self):
        reading = self.parser.parse(_v3_payload())
        self.assertEqual(reading.compact_version, 3)
        self.assertEqual(reading.processing_profile, "hardware_only")
        self.assertEqual(reading.firmware_version, "sensor-fw-3.0.0")
        self.assertEqual(reading.hardware_config_version, "board-a1")
        self.assertEqual(reading.calibration_version, "factory-unverified")
        self.assertEqual(reading.quality, "hardware_observation")
        self.assertEqual(reading.time_basis, "rfc3339")
        record = reading.as_record()
        self.assertEqual(record["source"]["source_event_id"], reading.event_id)

    def test_v3_rejects_semantic_or_missing_processing_profile(self):
        payload = _v3_payload()
        payload["pp"] = "moving_average"
        with self.assertRaisesRegex(ValueError, "hardware_only"):
            self.parser.parse(payload)
        del payload["pp"]
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            self.parser.parse(payload)

    def test_v3_schema_declares_hardware_only_and_no_distance(self):
        schema_text = (ROOT / "schemas/compact_sensor.v3.schema.json").read_text(encoding="utf-8")
        schema = json.loads(schema_text)
        self.assertEqual(schema["properties"]["pp"]["const"], "hardware_only")
        self.assertNotIn("distance_mm", schema_text)
        self.assertNotIn("VL53", schema_text)

    def test_gateway_filter_state_isolated_by_node_and_boot(self):
        preprocessor = GatewaySemanticPreprocessor(
            version="gateway_preprocess.test",
            filters={"temperature_c": {"kind": "moving_average", "window": 2}},
        )
        first = self.validator.validate(self.parser.parse(_v3_payload(temperature=10.0)))
        second = self.validator.validate(
            self.parser.parse(
                _v3_payload(sequence=2, timestamp="2026-07-11T00:01:00+00:00", temperature=20.0)
            )
        )
        other_node = self.validator.validate(
            self.parser.parse(_v3_payload(node="node-2", temperature=100.0))
        )
        rebooted = self.validator.validate(
            self.parser.parse(_v3_payload(boot="boot-b", sequence=1, temperature=30.0))
        )
        self.assertEqual(preprocessor.process(first).reading.sensor.temperature_c, 10.0)
        self.assertEqual(preprocessor.process(second).reading.sensor.temperature_c, 15.0)
        self.assertEqual(preprocessor.process(other_node).reading.sensor.temperature_c, 100.0)
        self.assertEqual(preprocessor.process(rebooted).reading.sensor.temperature_c, 30.0)

    def test_v2_is_not_double_filtered_by_default(self):
        payload = {
            "v": 2,
            "n": "node-1",
            "r": "room-a",
            "ts": "2026-07-11T00:00:00+00:00",
            "seq": 1,
            "bid": "boot-a",
            "st": "ok",
            "q": "valid",
            "f": [],
            "ok": {},
            "s": {"tc": 12.5},
        }
        result = self.validator.validate(self.parser.parse(payload))
        processed = GatewaySemanticPreprocessor(
            version="gateway_preprocess.test",
            filters={"temperature_c": {"kind": "moving_average", "window": 5}},
        ).process(result)
        self.assertEqual(processed.reading.sensor.temperature_c, 12.5)
        self.assertEqual(processed.reading.preprocessing_version, "legacy_node_preprocessed.v2")

    def test_resampling_never_mixes_nodes_in_same_time_bucket(self):
        timestamp = datetime(2026, 7, 11, tzinfo=UTC)
        results = []
        for node, value in (("node-a", 10.0), ("node-b", 90.0)):
            payload = _v3_payload(
                node=node,
                timestamp=timestamp.isoformat(),
                temperature=value,
            )
            result = self.validator.validate(self.parser.parse(payload))
            result = GatewaySemanticPreprocessor(
                version="gateway_preprocess.test"
            ).process(result)
            results.append(result)
        points = resample(results, 60)
        self.assertEqual(len(points), 2)
        values = {point.node_id: point.sensor.temperature_c for point in points}
        self.assertEqual(values, {"node-a": 10.0, "node-b": 90.0})
        self.assertTrue(all(len(point.source_event_ids) == 1 for point in points))

    def test_resampling_rejects_mixed_preprocessing_version(self):
        first = GatewaySemanticPreprocessor(version="pre-a").process(
            self.validator.validate(self.parser.parse(_v3_payload()))
        )
        second_payload = _v3_payload(
            sequence=2,
            timestamp=(datetime(2026, 7, 11, tzinfo=UTC) + timedelta(seconds=10)).isoformat(),
        )
        second = GatewaySemanticPreprocessor(version="pre-b").process(
            self.validator.validate(self.parser.parse(second_payload))
        )
        with self.assertRaisesRegex(ValueError, "mixed preprocessing versions"):
            resample([first, second], 60)

    def test_validator_sequence_state_isolated_across_gateways(self):
        first_payload = _v3_payload(node="shared-node", sequence=5)
        first_payload["gw"] = "gateway-a"
        second_payload = _v3_payload(node="shared-node", sequence=5)
        second_payload["gw"] = "gateway-b"
        first = self.validator.validate(self.parser.parse(first_payload))
        second = self.validator.validate(self.parser.parse(second_payload))
        self.assertTrue(first.is_valid)
        self.assertTrue(second.is_valid)
        self.assertNotIn("duplicate_sequence", second.issues)

    def test_window_state_isolated_across_gateway_and_room(self):
        builder = WindowBuilder(window_size=2)
        base_values = {name: 0.1 for name in FEATURE_NAMES}
        timestamp = datetime(2026, 7, 11, tzinfo=UTC)
        first_a = FeatureVector("gw-a", "node", "room", timestamp, base_values)
        first_b = FeatureVector("gw-b", "node", "room", timestamp, base_values)
        second_a = FeatureVector(
            "gw-a", "node", "room", timestamp + timedelta(minutes=1), base_values
        )
        self.assertIsNone(builder.add(first_a))
        self.assertIsNone(builder.add(first_b))
        window = builder.add(second_a)
        self.assertIsNotNone(window)
        self.assertEqual(window.gateway_id, "gw-a")
        self.assertEqual(window.start_timestamp, timestamp)


if __name__ == "__main__":
    unittest.main()
