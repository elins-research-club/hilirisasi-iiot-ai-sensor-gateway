from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterator


class MQTTDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class MQTTEnvelope:
    topic: str
    payload: bytes
    receive_timestamp: str


class ChirpStackMQTTSource:
    """Bounded ChirpStack MQTT uplink source.

    The MQTT callback only copies messages into a bounded queue. Processing is
    intentionally outside the Paho network thread so slow AI work does not
    block keepalive/reconnect handling.
    """

    source_name = "chirpstack_mqtt"

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 1883,
        topic: str = "application/+/device/+/event/up",
        client_id: str = "iiot-ai-sensor-shadow",
        keepalive: int = 30,
        queue_max: int = 4096,
        reconnect_min_sec: int = 1,
        reconnect_max_sec: int = 30,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        if not host or port <= 0 or keepalive <= 0 or queue_max < 1:
            raise ValueError("invalid MQTT source settings")
        if reconnect_min_sec < 1 or reconnect_max_sec < reconnect_min_sec:
            raise ValueError("invalid MQTT reconnect delay")
        self.host = host
        self.port = port
        self.topic = topic
        self.client_id = client_id
        self.keepalive = keepalive
        self.queue: queue.Queue[MQTTEnvelope] = queue.Queue(maxsize=queue_max)
        self.username = username
        self.password = password
        self.reconnect_min_sec = reconnect_min_sec
        self.reconnect_max_sec = reconnect_max_sec
        self._client: Any = None
        self._started = False
        self._connected = threading.Event()
        self._stop = threading.Event()
        self.received_total = 0
        self.queue_overflow_total = 0

    def metadata(self) -> dict[str, Any]:
        return {
            "source": self.source_name,
            "host": self.host,
            "port": self.port,
            "topic": self.topic,
            "client_id": self.client_id,
            "queue_max": self.queue.maxsize,
        }

    @staticmethod
    def _mqtt_module():
        try:
            import paho.mqtt.client as mqtt
            from paho.mqtt.enums import CallbackAPIVersion
        except ModuleNotFoundError as exc:
            raise MQTTDependencyError(
                "paho-mqtt is required; install with python -m pip install -e '.[mqtt]'"
            ) from exc
        return mqtt, CallbackAPIVersion

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        del userdata, flags, properties
        # Paho callback API v2 passes a ReasonCode object. Compare to zero
        # directly; coercing it with int() is not portable across Paho 2.x.
        if reason_code != 0:
            self._connected.clear()
            return
        result, _mid = client.subscribe(self.topic, qos=0)
        mqtt, _api = self._mqtt_module()
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._connected.clear()
            return
        self._connected.set()

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties=None
    ) -> None:
        del client, userdata, disconnect_flags, reason_code, properties
        self._connected.clear()

    def _on_message(self, client, userdata, message) -> None:
        del client, userdata
        envelope = MQTTEnvelope(
            topic=str(message.topic),
            payload=bytes(message.payload),
            receive_timestamp=datetime.now(tz=UTC).isoformat(),
        )
        self.received_total += 1
        try:
            self.queue.put_nowait(envelope)
        except queue.Full:
            self.queue_overflow_total += 1

    def start(self) -> None:
        if self._started:
            return
        mqtt, api = self._mqtt_module()
        client = mqtt.Client(api.VERSION2, client_id=self.client_id)
        if self.username:
            client.username_pw_set(self.username, self.password)
        client.reconnect_delay_set(self.reconnect_min_sec, self.reconnect_max_sec)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.connect(self.host, self.port, self.keepalive)
        client.loop_start()
        self._client = client
        self._started = True

    def close(self) -> None:
        self._stop.set()
        client = self._client
        self._client = None
        self._started = False
        if client is not None:
            try:
                client.disconnect()
            finally:
                client.loop_stop()

    def iter_messages(
        self,
        *,
        max_messages: int = 0,
        poll_timeout_sec: float = 1.0,
    ) -> Iterator[MQTTEnvelope]:
        if poll_timeout_sec <= 0:
            raise ValueError("poll_timeout_sec must be > 0")
        self.start()
        yielded = 0
        while not self._stop.is_set():
            if max_messages and yielded >= max_messages:
                break
            item = self.next_message(timeout_sec=poll_timeout_sec)
            if item is None:
                continue
            yielded += 1
            yield item

    def next_message(self, *, timeout_sec: float = 1.0) -> MQTTEnvelope | None:
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be > 0")
        if not self._started:
            self.start()
        try:
            return self.queue.get(timeout=timeout_sec)
        except queue.Empty:
            return None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()
