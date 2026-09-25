from __future__ import annotations

import base64
import json
import unittest

from iiot_ai_sensor_gateway.adapters.chirpstack_live import ChirpStackLiveAdapter
from iiot_ai_sensor_gateway.mqtt_contracts import build_sensor_ai_event_v2
from iiot_ai_sensor_gateway.preprocessing import GatewaySemanticPreprocessor
from iiot_ai_sensor_gateway.validation import ReadingValidator

NODE1 = "737fa3925c5c2bba"
NODE2 = "e02d6ee6c5f39cfd"


def _base(dev_eui: str, fcnt: int) -> dict:
    return {
        "deduplicationId": f"dup-{dev_eui}-{fcnt}",
        "time": "2026-09-25T02:43:12.533024839+00:00",
        "deviceInfo": {
            "devEui": dev_eui,
            "deviceName": "node-1" if dev_eui == NODE1 else "node-2",
            "applicationId": "app-1",
            "applicationName": "iiot",
        },
        "devAddr": "0022392f" if dev_eui == NODE1 else "00bae7a0",
        "fCnt": fcnt,
        "fPort": 1,
        "rxInfo": [{"rssi": -44, "snr": 13.2, "crcStatus": "CRC_OK"}],
    }


class ChirpStackAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ChirpStackLiveAdapter(
            "iiotgw",
            room_by_dev_eui={NODE1: "room-env", NODE2: "room-env"},
            allow_dev_eui={NODE1, NODE2},
        )

    def test_node1_object_preserves_unmapped_no2(self) -> None:
        event = _base(NODE1, 3901)
        event["object"] = {
            "id": 181,
            "temp": 29.1,
            "hum": 67.0,
            "co2": 715,
            "co": 4.3,
            "no2": 0.12,
        }
        reading = self.adapter.adapt(
            event,
            topic=f"application/app/device/{NODE1}/event/up",
            receive_timestamp="2026-09-25T02:43:13Z",
        )
        self.assertIsNone(reading.compact_version)
        self.assertEqual(reading.source_contract, "chirpstack_live.v1")
        self.assertEqual(reading.source_session_id, "lorawan:0022392f")
        self.assertEqual(reading.sequence, 3901)
        self.assertEqual(reading.sensor.temperature_c, 29.1)
        self.assertEqual(reading.sensor.humidity_pct, 67.0)
        self.assertEqual(reading.sensor.co2_ppm, 715.0)
        self.assertEqual(reading.sensor.co_ppm, 4.3)
        self.assertIsNone(reading.sensor.no2_ratio)
        self.assertEqual(reading.source_metadata["no2_source_unmapped"], 0.12)
        self.assertEqual(reading.radio.rssi, -44.0)

    def test_node2_current_7byte_preserves_unknown_extra(self) -> None:
        event = _base(NODE2, 3081)
        event["data"] = base64.b64encode(bytes.fromhex("3301010291001d")).decode()
        reading = self.adapter.adapt(event, receive_timestamp="2026-09-25T02:43:13Z")
        self.assertEqual(reading.sensor.temperature_c, 25.7)
        self.assertEqual(reading.sensor.humidity_pct, 65.7)
        self.assertEqual(reading.source_metadata["extra_raw_u16"], 29)
        self.assertEqual(reading.source_metadata["payload_format"], "bme_lite_v2_7byte")

    def test_duplicate_is_rejected_by_existing_validator(self) -> None:
        event = _base(NODE1, 10)
        event["object"] = {"temp": 25.0, "hum": 50.0}
        reading = self.adapter.adapt(event)
        validator = ReadingValidator(
            {"temperature_c": (-10, 80), "humidity_pct": (0, 100)}
        )
        self.assertTrue(validator.validate(reading).is_valid)
        second = validator.validate(reading)
        self.assertFalse(second.is_valid)
        self.assertIn("duplicate_sequence", second.issues)

    def test_live_source_is_eligible_for_gateway_preprocessing(self) -> None:
        event = _base(NODE1, 11)
        event["object"] = {"temp": 25.0, "hum": 50.0}
        reading = self.adapter.adapt(event)
        validated = ReadingValidator({}).validate(reading)
        processed = GatewaySemanticPreprocessor(
            version="gateway_preprocess.v1",
            filters={"temperature_c": {"kind": "none"}},
        ).process(validated)
        self.assertEqual(processed.reading.preprocessing_version, "gateway_preprocess.v1")
        self.assertEqual(processed.reading.source_event_id, reading.event_id)

    def test_sensor_ai_v2_keeps_source_contract(self) -> None:
        event = _base(NODE1, 12)
        event["object"] = {"temp": 25.0, "hum": 50.0, "co2": 600}
        reading = self.adapter.adapt(event)
        payload = build_sensor_ai_event_v2(
            reading,
            ai={"env_status": "normal", "node_health": "healthy", "abstain": False},
        )
        self.assertEqual(payload["schema_version"], "sensor_ai.v2")
        self.assertEqual(payload["source"]["contract"], "chirpstack_live.v1")
        self.assertEqual(payload["source"]["sequence"], 12)
        self.assertNotIn("compact_version", payload["source"])
        self.assertEqual(payload["ai"]["forecast"]["status"], "unavailable")

    def test_malformed_or_unapproved_input_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.adapt({"deviceInfo": {"devEui": NODE1}, "fCnt": 1})
        blocked = _base("0011223344556677", 1)
        blocked["object"] = {"temp": 25.0, "hum": 50.0}
        with self.assertRaises(ValueError):
            self.adapter.adapt(blocked)


class SensorAiV2SchemaTests(unittest.TestCase):
    def test_schema_validates_live_payload(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ModuleNotFoundError:
            self.skipTest("jsonschema optional dependency unavailable")
        adapter = ChirpStackLiveAdapter("iiotgw", allow_dev_eui={NODE1})
        event = _base(NODE1, 13)
        event["object"] = {"temp": 25.0, "hum": 50.0, "co2": 600}
        payload = build_sensor_ai_event_v2(adapter.adapt(event))
        with open("schemas/sensor_ai.v2.schema.json", encoding="utf-8") as handle:
            schema = json.load(handle)
        errors = list(Draft202012Validator(schema).iter_errors(payload))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
