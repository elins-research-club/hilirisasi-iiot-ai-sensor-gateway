from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from iiot_ai_sensor_gateway.config import load_config
from iiot_ai_sensor_gateway.live_runtime import LiveSensorRuntime
from iiot_ai_sensor_gateway.mqtt_publisher import (
    OutboxFullError,
    ReliableMQTTPublisher,
    SQLiteOutbox,
)
from iiot_ai_sensor_gateway.real.chirpstack_mqtt_source import MQTTEnvelope
from iiot_ai_sensor_gateway.real.chirpstack_mqtt_source import ChirpStackMQTTSource

NODE1 = "737fa3925c5c2bba"


def _event(fcnt: int, *, co2: float = 600.0) -> dict:
    return {
        "deduplicationId": f"dup-{fcnt}",
        "time": "2026-09-25T02:43:12+00:00",
        "deviceInfo": {
            "devEui": NODE1,
            "deviceName": "node-1",
            "applicationId": "app-1",
            "applicationName": "iiot",
        },
        "devAddr": "0022392f",
        "fCnt": fcnt,
        "fPort": 1,
        "object": {"temp": 25.0, "hum": 50.0, "co2": co2, "co": 2.0},
        "rxInfo": [{"rssi": -45, "snr": 12.5}],
    }


def _envelope(fcnt: int) -> MQTTEnvelope:
    return MQTTEnvelope(
        topic=f"application/app-1/device/{NODE1}/event/up",
        payload=json.dumps(_event(fcnt)).encode(),
        receive_timestamp="2026-09-25T02:43:13+00:00",
    )


class _FakeSource:
    queue_overflow_total = 0

    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.closed = False

    def next_message(self, *, timeout_sec=1.0):
        del timeout_sec
        return self.messages.pop(0) if self.messages else None

    def close(self):
        self.closed = True


class _PublishInfo:
    def __init__(self, published=True):
        self._published = published

    def wait_for_publish(self, timeout=None):
        del timeout

    def is_published(self):
        return self._published


class _FakeClient:
    def __init__(self):
        self.calls = []

    def publish(self, topic, payload, qos, retain):
        self.calls.append((topic, payload, qos, retain))
        return _PublishInfo(True)


class _ReasonCodeLike:
    def __init__(self, value: int):
        self.value = value

    def __eq__(self, other):
        return self.value == other


class _SubscribeClient:
    def __init__(self):
        self.calls = []

    def subscribe(self, topic, qos=0):
        self.calls.append((topic, qos))
        return 0, 1


class _FakePublisher:
    def __init__(self):
        self.events = []
        self.status = []
        self.closed = False

    def enqueue_json(self, event_id, topic, payload):
        self.events.append((event_id, topic, payload))
        return True

    def flush(self, *, limit=100):
        del limit
        return len(self.events)

    def publish_status_json(self, topic, payload):
        self.status.append((topic, payload))
        return True

    def close(self):
        self.closed = True


class OutboxTests(unittest.TestCase):
    def test_sqlite_outbox_is_idempotent_and_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            outbox = SQLiteOutbox(Path(td) / "outbox.sqlite3", max_entries=1)
            self.assertTrue(outbox.enqueue("a", "topic", "{}"))
            self.assertFalse(outbox.enqueue("a", "topic", "{}"))
            self.assertEqual(outbox.count(), 1)
            with self.assertRaises(OutboxFullError):
                outbox.enqueue("b", "topic", "{}")
            self.assertEqual(outbox.pending()[0].event_id, "a")
            self.assertTrue(outbox.ack("a"))
            self.assertEqual(outbox.count(), 0)

    def test_publisher_waits_for_confirmation_then_acks(self):
        with tempfile.TemporaryDirectory() as td:
            outbox = SQLiteOutbox(Path(td) / "outbox.sqlite3")
            client = _FakeClient()
            publisher = ReliableMQTTPublisher(
                host="example",
                port=1883,
                client_id="test",
                outbox=outbox,
                client=client,
            )
            publisher.enqueue_json("evt-1", "iot/test/data", {"x": 1})
            self.assertEqual(publisher.flush(), 1)
            self.assertEqual(outbox.count(), 0)
            self.assertEqual(client.calls[0][2:], (1, False))
            self.assertTrue(
                publisher.publish_status_json("iot/test/status", {"status": "online"})
            )
            self.assertEqual(client.calls[-1][2:], (1, True))

    def test_paho_v2_reason_code_callbacks(self):
        source = ChirpStackMQTTSource()
        client = _SubscribeClient()
        fake_mqtt = SimpleNamespace(MQTT_ERR_SUCCESS=0)
        with patch.object(
            ChirpStackMQTTSource,
            "_mqtt_module",
            return_value=(fake_mqtt, object()),
        ):
            source._on_connect(client, None, {}, _ReasonCodeLike(0))
        self.assertTrue(source.connected)
        self.assertEqual(client.calls, [(source.topic, 0)])

        with tempfile.TemporaryDirectory() as td:
            publisher = ReliableMQTTPublisher(
                host="example",
                port=1883,
                client_id="test",
                outbox=SQLiteOutbox(Path(td) / "outbox.sqlite3"),
                client=_FakeClient(),
            )
            publisher._connected.clear()
            publisher._on_connect(None, None, {}, _ReasonCodeLike(0))
            self.assertTrue(publisher.connected)
            publisher._on_connect(None, None, {}, _ReasonCodeLike(1))
            self.assertFalse(publisher.connected)


class RuntimeTests(unittest.TestCase):
    def _config(self, td: str, mode: str):
        config = load_config("config/iiotgw.toml")
        runtime = replace(
            config.live_runtime,
            mode=mode,
            output_dir=str(Path(td) / "state"),
            anomaly_warmup_samples=1,
        )
        publisher = replace(config.publisher, enabled=(mode == "publish_ai"))
        return replace(config, live_runtime=runtime, publisher=publisher)

    def test_shadow_ingest_writes_raw_and_canonical_without_ai(self):
        with tempfile.TemporaryDirectory() as td:
            source = _FakeSource()
            runtime = LiveSensorRuntime(self._config(td, "shadow_ingest"), source=source)
            self.assertIsNone(runtime.handle(_envelope(1)))
            state = Path(td) / "state"
            self.assertTrue((state / "raw_events.jsonl").exists())
            self.assertTrue((state / "accepted_events.jsonl").exists())
            self.assertFalse((state / "ai_events.jsonl").exists())
            self.assertEqual(runtime.summary.accepted_total, 1)

    def test_shadow_ai_builds_source_agnostic_event(self):
        with tempfile.TemporaryDirectory() as td:
            source = _FakeSource()
            runtime = LiveSensorRuntime(self._config(td, "shadow_ai"), source=source)
            payload = runtime.handle(_envelope(1))
            self.assertIsNotNone(payload)
            self.assertEqual(payload["schema_version"], "sensor_ai.v2")
            self.assertEqual(payload["source"]["contract"], "chirpstack_live.v1")
            self.assertIsNotNone(payload["ai"]["anomaly_score"])
            self.assertEqual(payload["ai"]["forecast"]["status"], "disabled")

    def test_publish_mode_enqueues_event_and_retained_status(self):
        with tempfile.TemporaryDirectory() as td:
            source = _FakeSource()
            publisher = _FakePublisher()
            runtime = LiveSensorRuntime(
                self._config(td, "publish_ai"),
                source=source,
                publisher=publisher,
            )
            payload = runtime.handle(_envelope(1))
            self.assertEqual(len(publisher.events), 1)
            self.assertEqual(publisher.events[0][1], "iot/iiotgw/data")
            self.assertEqual(payload["deployment"]["runtime_mode"], "publish_ai")
            self.assertEqual(publisher.status[-1][0], "iot/iiotgw/status/sensor")

    def test_duplicate_event_is_rejected_without_ai_publish(self):
        with tempfile.TemporaryDirectory() as td:
            source = _FakeSource()
            publisher = _FakePublisher()
            runtime = LiveSensorRuntime(
                self._config(td, "publish_ai"),
                source=source,
                publisher=publisher,
            )
            runtime.handle(_envelope(1))
            self.assertIsNone(runtime.handle(_envelope(1)))
            self.assertEqual(len(publisher.events), 1)
            self.assertEqual(runtime.summary.rejected_total, 1)


if __name__ == "__main__":
    unittest.main()
