from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .adapters.chirpstack_live import ChirpStackLiveAdapter
from .config import AppConfig
from .decision import build_sensor_decision
from .mqtt_contracts import (
    build_sensor_ai_event_v2,
    build_sensor_status,
    data_topic,
    node_state_from_reading,
    status_topic,
)
from .mqtt_publisher import ReliableMQTTPublisher, SQLiteOutbox
from .pipeline import PreModelPipeline
from .live_forecast import LiveEdgeForecaster
from .real.chirpstack_mqtt_source import ChirpStackMQTTSource, MQTTEnvelope
from .runtime_logging import JsonlLogger
from .streaming_detection import NativePageHinkley, NativeRobustAnomaly


@dataclass
class _NodeAnomalyState:
    model: NativeRobustAnomaly = field(default_factory=NativeRobustAnomaly)
    drift: dict[str, NativePageHinkley] = field(default_factory=dict)
    seen: int = 0


class LiveAnomalyEngine:
    def __init__(
        self,
        *,
        ranges: dict[str, tuple[float, float]],
        warmup_samples: int,
        anomaly_threshold: float,
    ) -> None:
        self.ranges = ranges
        self.warmup_samples = warmup_samples
        self.anomaly_threshold = anomaly_threshold
        self.states: dict[str, _NodeAnomalyState] = {}

    def _normalize(self, sensor: dict[str, Any]) -> dict[str, float]:
        values: dict[str, float] = {}
        for name, value in sensor.items():
            if value is None or name not in self.ranges:
                continue
            low, high = self.ranges[name]
            if high <= low:
                continue
            normalized = (float(value) - low) / (high - low)
            values[name] = max(0.0, min(1.0, normalized))
        return values

    def process(self, node_id: str, sensor: dict[str, Any]) -> dict[str, Any]:
        features = self._normalize(sensor)
        if not features:
            return {
                "warmup_complete": False,
                "anomaly_score": None,
                "is_anomaly": False,
                "drift_detected": False,
                "drift_features": [],
            }
        state = self.states.setdefault(node_id, _NodeAnomalyState())
        score = float(state.model.score_one(features))
        state.model.learn_one(features)
        drift_features: list[str] = []
        for name, value in features.items():
            detector = state.drift.setdefault(name, NativePageHinkley())
            detector.update(value)
            if detector.drift_detected:
                drift_features.append(name)
        state.seen += 1
        warmed = state.seen >= self.warmup_samples
        return {
            "warmup_complete": warmed,
            "anomaly_score": max(0.0, min(1.0, score)) if warmed else None,
            "is_anomaly": bool(warmed and score >= self.anomaly_threshold),
            "drift_detected": bool(warmed and drift_features),
            "drift_features": sorted(drift_features) if warmed else [],
        }


@dataclass
class LiveRuntimeSummary:
    started_at: str
    stopped_at: str | None = None
    received_total: int = 0
    accepted_total: int = 0
    rejected_total: int = 0
    ai_events_total: int = 0
    publish_enqueued_total: int = 0
    publish_flushed_total: int = 0
    queue_overflow_total: int = 0
    by_node: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "iiot.ai_sensor.live_runtime_summary.v1",
            **self.__dict__,
        }


class LiveSensorRuntime:
    def __init__(
        self,
        config: AppConfig,
        *,
        source: ChirpStackMQTTSource | None = None,
        publisher: ReliableMQTTPublisher | None = None,
    ) -> None:
        if not config.chirpstack.enabled and source is None:
            raise ValueError("chirpstack source is disabled")
        self.config = config
        username = os.environ.get(config.chirpstack.username_env) or None
        password = os.environ.get(config.chirpstack.password_env) or None
        self.source = source or ChirpStackMQTTSource(
            host=config.chirpstack.host,
            port=config.chirpstack.port,
            topic=config.chirpstack.topic,
            client_id=config.chirpstack.client_id,
            keepalive=config.chirpstack.keepalive,
            queue_max=config.chirpstack.queue_max,
            reconnect_min_sec=config.chirpstack.reconnect_min_sec,
            reconnect_max_sec=config.chirpstack.reconnect_max_sec,
            username=username,
            password=password,
        )
        self.adapter = ChirpStackLiveAdapter(
            config.identity.gateway_id,
            room_by_dev_eui=config.chirpstack.room_by_dev_eui,
            allow_dev_eui=set(config.chirpstack.allow_dev_eui),
            default_room_id=config.identity.default_room_id,
        )
        self.pipeline = PreModelPipeline(config)
        self.logger = JsonlLogger(
            config.live_runtime.output_dir,
            rotate_max_bytes=config.live_runtime.rotate_max_bytes,
        )
        self.anomaly = LiveAnomalyEngine(
            ranges=config.normalization_ranges,
            warmup_samples=config.live_runtime.anomaly_warmup_samples,
            anomaly_threshold=config.live_runtime.anomaly_threshold,
        )
        self.latest_readings: dict[str, Any] = {}
        self.forecaster = (
            LiveEdgeForecaster(
                config.forecast_runtime.manifest_path,
                target_node_id=config.forecast_runtime.target_node_id,
                cadence_tolerance_fraction=config.forecast_runtime.cadence_tolerance_fraction,
                device="cpu",
            )
            if config.forecast_runtime.enabled
            else None
        )
        self.last_forecast: dict[str, dict[str, Any]] = {}
        self.publisher = publisher
        if config.publisher.enabled and self.publisher is None:
            offline_status = build_sensor_status(
                gateway_id=config.identity.gateway_id,
                node_states=[],
                status="offline",
            )
            self.publisher = ReliableMQTTPublisher(
                host=config.publisher.host,
                port=config.publisher.port,
                client_id=config.publisher.client_id,
                keepalive=config.publisher.keepalive,
                publish_timeout_sec=config.publisher.publish_timeout_sec,
                reconnect_min_sec=config.publisher.reconnect_min_sec,
                reconnect_max_sec=config.publisher.reconnect_max_sec,
                username=os.environ.get(config.publisher.username_env) or None,
                password=os.environ.get(config.publisher.password_env) or None,
                outbox=SQLiteOutbox(
                    config.publisher.outbox_path,
                    max_entries=config.publisher.outbox_max_entries,
                ),
                will_topic=status_topic(config.identity.gateway_id),
                will_payload=json.dumps(
                    offline_status, separators=(",", ":"), ensure_ascii=False
                ),
            )
        self.summary = LiveRuntimeSummary(started_at=datetime.now(tz=UTC).isoformat())

    def _raw_record(self, envelope: MQTTEnvelope) -> dict[str, Any]:
        try:
            payload_text = envelope.payload.decode("utf-8")
        except UnicodeDecodeError:
            payload_text = envelope.payload.decode("utf-8", errors="replace")
        return {
            "received_at": envelope.receive_timestamp,
            "topic": envelope.topic,
            "payload": payload_text,
        }

    def _ai_fields(
        self, reading, *, new_vectors: list[Any]
    ) -> tuple[dict[str, Any], dict[str, Any], str | None]:
        sensor = reading.sensor.as_dict(include_legacy=False)
        anomaly = (
            self.anomaly.process(reading.node_id, sensor)
            if self.config.live_runtime.anomaly_enabled
            else {
                "warmup_complete": False,
                "anomaly_score": None,
                "is_anomaly": False,
                "drift_detected": False,
                "drift_features": [],
            }
        )
        forecast: dict[str, Any]
        if self.forecaster is None:
            forecast = {"status": "disabled", "forecast_status": "unavailable"}
        elif (
            self.forecaster.target_node_id
            and reading.node_id.lower() != self.forecaster.target_node_id
        ):
            forecast = {"status": "not_target_node", "forecast_status": "unavailable"}
        else:
            forecast = self.last_forecast.get(
                reading.node_id,
                {
                    "status": "waiting_for_resample",
                    "forecast_status": "warming",
                    "model_type": self.forecaster.runtime_artifact.get("model_type"),
                    "model_version": self.forecaster.runtime_artifact.get("model_version"),
                    "runtime_backend": self.forecaster.manifest.get("runtime_backend"),
                    "model_readiness": self.forecaster.manifest.get(
                        "readiness", "EXPERIMENTAL"
                    ),
                    "model_manifest_id": self.forecaster.manifest.get("id"),
                },
            )
            if new_vectors:
                for vector in new_vectors:
                    forecast = self.forecaster.process(vector)
                self.last_forecast[reading.node_id] = dict(forecast)
        decision = build_sensor_decision(
            sensor=sensor,
            quality=reading.quality,
            source_status=reading.status,
            anomaly_score=anomaly["anomaly_score"],
            drift_detected=anomaly["drift_detected"],
            forecast_status=str(forecast.get("forecast_status", "unavailable")),
            model_readiness=str(forecast.get("model_readiness", "EXPERIMENTAL")),
        )
        fields = {
            "env_status": decision["env_status"],
            "anomaly_score": anomaly["anomaly_score"],
            "forecast_status": decision["forecast_status"],
            "forecast": {
                "status": str(forecast.get("status", "unavailable")),
                "predicted": {
                    str(name): float(value)
                    for name, value in (forecast.get("predicted") or {}).items()
                },
                "horizon_steps": forecast.get("horizon_steps"),
                "horizon_duration_seconds": forecast.get(
                    "horizon_duration_seconds"
                ),
                "model_type": forecast.get("model_type"),
                "model_version": forecast.get("model_version"),
                "runtime_backend": forecast.get("runtime_backend"),
                "model_readiness": str(
                    forecast.get("model_readiness", "EXPERIMENTAL")
                ),
                "model_manifest_id": forecast.get("model_manifest_id"),
                "inference_latency_ms": forecast.get("inference_latency_ms"),
            },
            "main_factor": decision["main_factor"],
            "battery_status": decision["battery_status"],
            "node_health": decision["node_health"],
            "confidence": decision["confidence"],
            "abstain": decision["abstain"],
        }
        return (
            fields,
            {"anomaly": anomaly, "forecast": forecast, "decision": decision},
            forecast.get("model_manifest_id"),
        )

    def _status_payload(self) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        states = [
            node_state_from_reading(
                reading,
                now=now,
                silent_after_sec=self.config.pipeline.node_silent_after_sec,
            )
            for reading in self.latest_readings.values()
        ]
        overall = "online"
        if states and all(state["node_health"] == "stale" for state in states):
            overall = "degraded"
        return build_sensor_status(
            gateway_id=self.config.identity.gateway_id,
            node_states=states,
            status=overall,
            timestamp=now,
        )

    def handle(self, envelope: MQTTEnvelope) -> dict[str, Any] | None:
        self.summary.received_total += 1
        raw = self._raw_record(envelope)
        self.logger.write("raw_events", raw)
        try:
            event = json.loads(envelope.payload)
            reading = self.adapter.adapt(
                event,
                topic=envelope.topic,
                receive_timestamp=envelope.receive_timestamp,
            )
            validation, windows, new_vectors = self.pipeline.process_reading_detailed(reading)
        except Exception as exc:
            self.summary.rejected_total += 1
            self.logger.write(
                "rejected_events",
                {**raw, "category": "decode_or_adapter_error", "error": str(exc)},
            )
            return None
        if not validation.is_valid:
            self.summary.rejected_total += 1
            self.logger.write(
                "rejected_events",
                {
                    **raw,
                    "category": "validation_error",
                    "event_id": reading.event_id,
                    "issues": list(validation.issues),
                },
            )
            return None

        processed = validation.reading
        self.latest_readings[processed.node_id] = processed
        self.summary.accepted_total += 1
        self.summary.by_node[processed.node_id] = (
            self.summary.by_node.get(processed.node_id, 0) + 1
        )
        self.logger.write(
            "accepted_events",
            {
                "received_at": envelope.receive_timestamp,
                "topic": envelope.topic,
                "event_id": processed.event_id,
                "sequence": processed.sequence,
                "source_contract": processed.source_contract,
                "issues": list(validation.issues),
                "canonical": processed.as_record(),
                "new_windows": [window.as_record() for window in windows],
            },
        )
        if self.config.live_runtime.mode == "shadow_ingest":
            return None

        ai_fields, diagnostics, model_manifest_id = self._ai_fields(
            processed, new_vectors=new_vectors
        )
        payload = build_sensor_ai_event_v2(
            processed,
            ai=ai_fields,
            config_version=self.config.live_runtime.config_version,
            runtime_mode=self.config.live_runtime.mode,
            model_manifest_id=model_manifest_id,
        )
        self.summary.ai_events_total += 1
        self.logger.write(
            "ai_events",
            {
                "payload": payload,
                "diagnostics": diagnostics,
                "new_window_count": len(windows),
            },
        )
        if self.config.live_runtime.mode == "publish_ai":
            if self.publisher is None:
                raise RuntimeError("publish_ai mode has no publisher")
            topic = data_topic(self.config.identity.gateway_id)
            if self.publisher.enqueue_json(processed.event_id, topic, payload):
                self.summary.publish_enqueued_total += 1
            flushed = self.publisher.flush()
            self.summary.publish_flushed_total += flushed
            self.publisher.publish_status_json(
                status_topic(self.config.identity.gateway_id),
                self._status_payload(),
            )
        return payload

    def run(self, *, max_messages: int = 0, poll_timeout_sec: float = 1.0) -> dict[str, Any]:
        processed = 0
        try:
            while True:
                if max_messages and processed >= max_messages:
                    break
                envelope = self.source.next_message(timeout_sec=poll_timeout_sec)
                if envelope is None:
                    if self.publisher is not None:
                        self.summary.publish_flushed_total += self.publisher.flush()
                    continue
                self.handle(envelope)
                processed += 1
                self.summary.queue_overflow_total = self.source.queue_overflow_total
                if self.source.queue_overflow_total:
                    raise RuntimeError(
                        "ChirpStack source queue overflowed; refusing silent data loss"
                    )
        except KeyboardInterrupt:
            pass
        finally:
            self.summary.stopped_at = datetime.now(tz=UTC).isoformat()
            self.logger.write("runtime_events", self.summary.as_dict())
            if self.publisher is not None:
                try:
                    self.publisher.publish_status_json(
                        status_topic(self.config.identity.gateway_id),
                        build_sensor_status(
                            gateway_id=self.config.identity.gateway_id,
                            node_states=[],
                            status="offline",
                        ),
                    )
                finally:
                    self.publisher.close()
            self.source.close()
        return self.summary.as_dict()
