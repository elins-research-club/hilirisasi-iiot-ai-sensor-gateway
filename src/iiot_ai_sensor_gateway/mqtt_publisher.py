from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class PublisherDependencyError(RuntimeError):
    pass


class OutboxFullError(RuntimeError):
    pass


@dataclass(frozen=True)
class OutboxRecord:
    event_id: str
    topic: str
    payload: str
    created_at: str


class SQLiteOutbox:
    def __init__(self, path: str | Path, *, max_entries: int = 10000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10.0)

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def enqueue(self, event_id: str, topic: str, payload: str) -> bool:
        if not event_id or not topic or not payload:
            raise ValueError("outbox event_id/topic/payload must not be empty")
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing:
                return False
            count = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            if count >= self.max_entries:
                raise OutboxFullError(
                    f"outbox full ({count}/{self.max_entries}); refusing to drop event"
                )
            conn.execute(
                "INSERT INTO events(event_id,topic,payload,created_at) VALUES(?,?,?,?)",
                (
                    event_id,
                    topic,
                    payload,
                    datetime.now(tz=UTC).isoformat(),
                ),
            )
            return True

    def pending(self, limit: int = 100) -> list[OutboxRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT event_id,topic,payload,created_at FROM events ORDER BY created_at,event_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [OutboxRecord(*row) for row in rows]

    def ack(self, event_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM events WHERE event_id=?", (event_id,))
            return cur.rowcount > 0


class ReliableMQTTPublisher:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: str,
        outbox: SQLiteOutbox,
        keepalive: int = 30,
        publish_timeout_sec: float = 10.0,
        reconnect_min_sec: int = 1,
        reconnect_max_sec: int = 30,
        username: str | None = None,
        password: str | None = None,
        will_topic: str | None = None,
        will_payload: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.outbox = outbox
        self.keepalive = keepalive
        self.publish_timeout_sec = publish_timeout_sec
        self.reconnect_min_sec = reconnect_min_sec
        self.reconnect_max_sec = reconnect_max_sec
        self.username = username
        self.password = password
        self.will_topic = will_topic
        self.will_payload = will_payload
        self._client = client
        self._owns_client = client is None
        self._connected = threading.Event()
        self.publish_success_total = 0
        self.publish_failure_total = 0

    @staticmethod
    def _mqtt_module():
        try:
            import paho.mqtt.client as mqtt
            from paho.mqtt.enums import CallbackAPIVersion
        except ModuleNotFoundError as exc:
            raise PublisherDependencyError(
                "paho-mqtt is required; install with python -m pip install -e '.[mqtt]'"
            ) from exc
        return mqtt, CallbackAPIVersion

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        del client, userdata, flags, properties
        if reason_code == 0:
            self._connected.set()
        else:
            self._connected.clear()

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties=None
    ) -> None:
        del client, userdata, disconnect_flags, reason_code, properties
        self._connected.clear()

    def start(self) -> None:
        if self._client is not None and not self._owns_client:
            self._connected.set()
            return
        if self._client is not None:
            return
        mqtt, api = self._mqtt_module()
        client = mqtt.Client(api.VERSION2, client_id=self.client_id)
        if self.username:
            client.username_pw_set(self.username, self.password)
        if self.will_topic and self.will_payload:
            client.will_set(self.will_topic, self.will_payload, qos=1, retain=True)
        client.reconnect_delay_set(self.reconnect_min_sec, self.reconnect_max_sec)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        # Do not make service startup depend on the remote broker already
        # accepting TCP. Paho's network loop owns exponential reconnect.
        client.connect_async(self.host, self.port, self.keepalive)
        client.loop_start()
        self._client = client

    def close(self) -> None:
        client = self._client
        self._client = None
        self._connected.clear()
        if client is not None and self._owns_client:
            try:
                client.disconnect()
            finally:
                client.loop_stop()

    def enqueue_json(self, event_id: str, topic: str, payload: dict[str, Any]) -> bool:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        return self.outbox.enqueue(event_id, topic, encoded)

    def flush(self, *, limit: int = 100) -> int:
        self.start()
        client = self._client
        if client is None or not self.connected:
            return 0
        sent = 0
        for record in self.outbox.pending(limit):
            try:
                info = client.publish(record.topic, record.payload, qos=1, retain=False)
                waiter = getattr(info, "wait_for_publish", None)
                published = getattr(info, "is_published", None)
                if not callable(waiter) or not callable(published):
                    raise RuntimeError("MQTT client does not expose publish confirmation")
                waiter(timeout=self.publish_timeout_sec)
                if not bool(published()):
                    raise RuntimeError("MQTT publish not confirmed before timeout")
            except Exception:
                self.publish_failure_total += 1
                break
            self.outbox.ack(record.event_id)
            self.publish_success_total += 1
            sent += 1
        return sent

    def publish_status_json(self, topic: str, payload: dict[str, Any]) -> bool:
        """Publish retained latest status without putting heartbeat/status in event outbox."""

        self.start()
        client = self._client
        if client is None or not self.connected:
            return False
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        try:
            info = client.publish(topic, encoded, qos=1, retain=True)
            info.wait_for_publish(timeout=self.publish_timeout_sec)
            if not bool(info.is_published()):
                raise RuntimeError("status publish not confirmed before timeout")
        except Exception:
            self.publish_failure_total += 1
            return False
        self.publish_success_total += 1
        return True

    @property
    def connected(self) -> bool:
        return self._connected.is_set()
