from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .contracts import (
    SENSOR_AI_SCHEMA_V1,
    SENSOR_AI_SCHEMA_V2,
    SENSOR_STATUS_SCHEMA_V1,
    SensorReading,
)

DATA_TOPIC_TEMPLATE = "iot/{gateway_id}/data"
STATUS_TOPIC_TEMPLATE = "iot/{gateway_id}/status/sensor"
LEGACY_STATUS_TOPIC_TEMPLATE = "iot/{gateway_id}/status"

CANONICAL_SENSOR_FIELDS = (
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "bme_gas_ohm",
    "co_ppm",
    "no2_raw_mv",
    "no2_ratio",
    "o3_ppm",
    "co2_ppm",
    "pm1_ug_m3",
    "pm25_ug_m3",
    "pm10_ug_m3",
    "battery_voltage",
    "current_ma",
    "power_mw",
)


def data_topic(gateway_id: str) -> str:
    if not gateway_id.strip():
        raise ValueError("gateway_id must not be empty")
    return DATA_TOPIC_TEMPLATE.format(gateway_id=gateway_id)


def status_topic(gateway_id: str) -> str:
    if not gateway_id.strip():
        raise ValueError("gateway_id must not be empty")
    return STATUS_TOPIC_TEMPLATE.format(gateway_id=gateway_id)


def build_sensor_ai_event(
    reading: SensorReading,
    *,
    ai: dict[str, Any] | None = None,
    config_version: str = "sensor-foundation-v2",
) -> dict[str, Any]:
    if reading.compact_version not in {1, 2, 3}:
        raise ValueError("sensor_ai.v1 only supports compact_sensor.v1/v2/v3 origins")
    canonical_sensor = {
        field: getattr(reading.sensor, field) for field in CANONICAL_SENSOR_FIELDS
    }
    default_ai: dict[str, Any] = {
        "env_status": "unknown",
        "anomaly_score": None,
        "forecast_status": "unavailable",
        "main_factor": None,
        "battery_status": "unknown",
        "node_health": "healthy" if reading.status == "ok" else "degraded",
        "confidence": None,
        "abstain": True,
    }
    if ai:
        unknown = sorted(set(ai).difference(default_ai))
        if unknown:
            raise ValueError(f"unsupported AI output fields: {unknown}")
        default_ai.update(ai)
    return {
        "schema_version": SENSOR_AI_SCHEMA_V1,
        "event_id": reading.event_id,
        "gateway_id": reading.gateway_id,
        "node_id": reading.node_id,
        "room_id": reading.room_id,
        "timestamp": reading.timestamp.isoformat(),
        "source": {
            "compact_version": reading.compact_version,
            "boot_id": reading.boot_id,
            "sequence": reading.sequence,
            "node_timestamp": reading.node_timestamp,
            "receive_timestamp": reading.receive_timestamp.isoformat(),
            "time_quality": reading.time_quality,
            "transport": reading.source,
            "radio": reading.radio.as_dict(),
            "sensor_status": dict(reading.sensor_status),
            "flags": list(reading.flags),
        },
        "sensor": canonical_sensor,
        "ai": default_ai,
        "deployment": {"config_version": config_version},
    }


def build_sensor_ai_event_v2(
    reading: SensorReading,
    *,
    ai: dict[str, Any] | None = None,
    config_version: str = "sensor-live-v1",
    runtime_mode: str = "shadow_ai",
    model_manifest_id: str | None = None,
) -> dict[str, Any]:
    """Build a source-agnostic sensor+AI event without fabricating compact metadata."""

    if not reading.source_contract.strip():
        raise ValueError("source_contract must not be empty")
    canonical_sensor = {
        field: getattr(reading.sensor, field) for field in CANONICAL_SENSOR_FIELDS
    }
    default_ai: dict[str, Any] = {
        "env_status": "unknown",
        "anomaly_score": None,
        "forecast_status": "unavailable",
        "main_factor": None,
        "battery_status": "unknown",
        "node_health": "healthy" if reading.status == "ok" else "degraded",
        "confidence": None,
        "abstain": True,
    }
    if ai:
        unknown = sorted(set(ai).difference(default_ai))
        if unknown:
            raise ValueError(f"unsupported AI output fields: {unknown}")
        default_ai.update(ai)
    return {
        "schema_version": SENSOR_AI_SCHEMA_V2,
        "event_id": reading.event_id,
        "gateway_id": reading.gateway_id,
        "node_id": reading.node_id,
        "room_id": reading.room_id,
        "timestamp": reading.timestamp.isoformat(),
        "source": {
            "contract": reading.source_contract,
            "source_event_id": reading.source_event_id or reading.event_id,
            "session_id": reading.source_session_id,
            "sequence": reading.sequence,
            "node_timestamp": reading.node_timestamp,
            "receive_timestamp": reading.receive_timestamp.isoformat(),
            "time_quality": reading.time_quality,
            "transport": reading.source,
            "radio": reading.radio.as_dict(),
            "sensor_status": dict(reading.sensor_status),
            "flags": list(reading.flags),
            "metadata": dict(reading.source_metadata),
        },
        "sensor": canonical_sensor,
        "ai": default_ai,
        "deployment": {
            "config_version": config_version,
            "runtime_mode": runtime_mode,
            "model_manifest_id": model_manifest_id,
        },
    }


def build_sensor_status(
    *,
    gateway_id: str,
    node_states: list[dict[str, Any]],
    status: str = "online",
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    if status not in {"online", "degraded", "offline"}:
        raise ValueError("sensor status must be online, degraded, or offline")
    now = timestamp or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return {
        "schema_version": SENSOR_STATUS_SCHEMA_V1,
        "gateway_id": gateway_id,
        "timestamp": now.isoformat(),
        "status": status,
        "nodes": node_states,
    }


def node_state_from_reading(
    reading: SensorReading,
    *,
    now: datetime | None = None,
    silent_after_sec: int = 180,
) -> dict[str, Any]:
    current = now or datetime.now(tz=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    age_sec = max(0.0, (current - reading.receive_timestamp).total_seconds())
    if age_sec > silent_after_sec:
        health = "stale"
    elif reading.status != "ok" or reading.quality not in {"valid", "ok"}:
        health = "degraded"
    else:
        health = "healthy"
    return {
        "node_id": reading.node_id,
        "room_id": reading.room_id,
        "boot_id": reading.boot_id,
        "last_sequence": reading.sequence,
        "last_event_id": reading.event_id,
        "last_receive_timestamp": reading.receive_timestamp.isoformat(),
        "age_sec": age_sec,
        "node_health": health,
    }
