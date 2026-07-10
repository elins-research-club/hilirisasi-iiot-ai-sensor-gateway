from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

COMPACT_SCHEMA_V1 = "compact_sensor.v1"
COMPACT_SCHEMA_V2 = "compact_sensor.v2"
SENSOR_AI_SCHEMA_V1 = "sensor_ai.v1"
SENSOR_STATUS_SCHEMA_V1 = "sensor_status.v1"

COMPACT_KEYS = {
    "gateway_id": ("gateway_id", "gw"),
    "node_id": ("node_id", "node", "n"),
    "room_id": ("room_id", "room", "r"),
    "timestamp": ("node_timestamp", "timestamp", "ts", "t"),
    "sequence": ("sequence", "seq", "sq"),
    "boot_id": ("boot_id", "boot", "bid"),
    "status": ("status", "st"),
    "quality": ("quality", "q"),
    "flags": ("flags", "f"),
    "sensor_status": ("sensor_status", "ok"),
}

# Canonical compact-v2 aliases. The short names are frozen for the LoRa payload.
SENSOR_KEYS = {
    "temperature_c": ("temperature_c", "temp_c", "tc"),
    "humidity_pct": ("humidity_pct", "hum_pct", "rh", "h"),
    "pressure_hpa": ("pressure_hpa", "pressure", "p_hpa", "p"),
    "bme_gas_ohm": ("bme_gas_ohm", "bme_gas", "bme"),
    "co_ppm": ("co_ppm", "co"),
    "no2_raw_mv": ("no2_raw_mv", "n2mv"),
    "no2_ratio": ("no2_ratio", "n2r"),
    "o3_ppm": ("o3_ppm", "o3"),
    "co2_ppm": ("co2_ppm", "co2"),
    "pm1_ug_m3": ("pm1_ug_m3", "pm1"),
    "pm25_ug_m3": ("pm25_ug_m3", "pm25"),
    "pm10_ug_m3": ("pm10_ug_m3", "pm10"),
    "battery_voltage": ("battery_voltage", "batt_v", "bv"),
    "current_ma": ("current_ma", "current_mA", "bi"),
    "power_mw": ("power_mw", "power_mW", "bp"),
}

# Accepted only for migration/reference fixtures. These names never become the
# production compact-v2 contract.
LEGACY_SENSOR_KEYS = {
    "voc_raw": ("voc_raw", "voc", "v"),
    "bme_gas_raw": ("bme_gas_raw", "bme_gas", "bme"),
    "co_raw": ("co_raw", "co"),
    "gas_raw": ("gas_raw", "gas", "g"),
    "battery_voltage": ("battery_voltage", "batt_v", "bv"),
    "current_ma": ("current_ma", "current_mA", "ma", "i"),
    "power_mw": ("power_mw", "power_mW", "mw"),
}

TIME_QUALITY_VALUES = {
    "node_synced",
    "node_epoch",
    "gateway_received",
    "source_clock",
    "unknown",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def stable_event_id(
    *,
    gateway_id: str,
    node_id: str,
    boot_id: str,
    sequence: int,
    node_timestamp: Any,
    sensor: dict[str, Any],
) -> str:
    """Build a deterministic retry-safe event ID for one node boot cycle."""

    identity = {
        "gateway_id": gateway_id,
        "node_id": node_id,
        "boot_id": boot_id,
        "sequence": sequence,
        "node_timestamp": node_timestamp,
        "sensor": sensor,
    }
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return f"se_{digest[:32]}"


@dataclass(frozen=True)
class SensorValues:
    temperature_c: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    bme_gas_ohm: float | None = None
    co_ppm: float | None = None
    no2_raw_mv: float | None = None
    no2_ratio: float | None = None
    o3_ppm: float | None = None
    co2_ppm: float | None = None
    pm1_ug_m3: float | None = None
    pm25_ug_m3: float | None = None
    pm10_ug_m3: float | None = None
    battery_voltage: float | None = None
    current_ma: float | None = None
    power_mw: float | None = None

    # Migration/reference-only lanes. They are kept separate so a gas proxy is
    # never silently relabelled as a calibrated RAB sensor reading.
    voc_raw: float | None = None
    bme_gas_raw: float | None = None
    co_raw: float | None = None
    gas_raw: float | None = None

    def as_dict(self, *, include_legacy: bool = True) -> dict[str, float | None]:
        values = self.__dict__.copy()
        if not include_legacy:
            for name in LEGACY_SENSOR_KEYS:
                values.pop(name, None)
        return values

    def present_count(self, *, include_legacy: bool = True) -> int:
        return sum(value is not None for value in self.as_dict(include_legacy=include_legacy).values())


@dataclass(frozen=True)
class RadioMeta:
    rssi: float | None = None
    snr: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class SensorReading:
    gateway_id: str
    node_id: str
    room_id: str
    timestamp: datetime
    receive_timestamp: datetime
    sequence: int
    boot_id: str
    event_id: str
    compact_version: int
    schema_version: str
    time_quality: str
    node_timestamp: Any
    sensor: SensorValues
    status: str = "ok"
    quality: str = "valid"
    flags: tuple[str, ...] = field(default_factory=tuple)
    sensor_status: dict[str, str] = field(default_factory=dict)
    radio: RadioMeta = field(default_factory=RadioMeta)
    source: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "gateway_id": self.gateway_id,
            "node_id": self.node_id,
            "room_id": self.room_id,
            "timestamp": self.timestamp.isoformat(),
            "source": {
                "compact_version": self.compact_version,
                "boot_id": self.boot_id,
                "sequence": self.sequence,
                "node_timestamp": self.node_timestamp,
                "receive_timestamp": self.receive_timestamp.isoformat(),
                "time_quality": self.time_quality,
                "transport": self.source,
                "radio": self.radio.as_dict(),
            },
            "status": self.status,
            "quality": self.quality,
            "flags": list(self.flags),
            "sensor_status": dict(self.sensor_status),
            "sensor": self.sensor.as_dict(),
        }


@dataclass(frozen=True)
class ValidationResult:
    reading: SensorReading
    is_valid: bool
    issues: tuple[str, ...] = field(default_factory=tuple)

    def as_record(self) -> dict[str, Any]:
        record = self.reading.as_record()
        record["is_valid"] = self.is_valid
        record["issues"] = list(self.issues)
        return record


@dataclass(frozen=True)
class ResampledPoint:
    gateway_id: str
    node_id: str
    room_id: str
    timestamp: datetime
    sensor: SensorValues
    valid_ratio: float
    missing_count: int
    seq_gap_count: int


@dataclass(frozen=True)
class FeatureVector:
    gateway_id: str
    node_id: str
    room_id: str
    timestamp: datetime
    values: dict[str, float]


@dataclass(frozen=True)
class WindowSample:
    gateway_id: str
    node_id: str
    room_id: str
    start_timestamp: datetime
    end_timestamp: datetime
    feature_names: tuple[str, ...]
    x: list[list[float]]

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.x), len(self.x[0]) if self.x else 0)

    def as_record(self) -> dict[str, Any]:
        return {
            "gateway_id": self.gateway_id,
            "node_id": self.node_id,
            "room_id": self.room_id,
            "start_timestamp": self.start_timestamp.isoformat(),
            "end_timestamp": self.end_timestamp.isoformat(),
            "feature_names": list(self.feature_names),
            "shape": list(self.shape),
            "x": self.x,
        }


def pick(mapping: dict[str, Any], aliases: tuple[str, ...], default: Any = None) -> Any:
    for alias in aliases:
        if alias in mapping:
            return mapping[alias]
    return default


def parse_timestamp(value: Any) -> datetime:
    """Parse an absolute timestamp only.

    Numeric values below year 2000 are deliberately rejected here because node
    uptime must never be interpreted as Unix epoch time.
    """

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise ValueError("timestamp must be finite")
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        if seconds < 946_684_800:
            raise ValueError("numeric node timestamp is uptime, not an absolute epoch")
        dt = datetime.fromtimestamp(seconds, tz=UTC)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp is empty")
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp is required")
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def resolve_event_time(node_time: Any, receive_time: datetime) -> tuple[datetime, Any, str]:
    """Resolve authoritative event time without mistaking uptime for epoch."""

    if node_time is None or node_time == "":
        return receive_time, node_time, "gateway_received"
    try:
        parsed = parse_timestamp(node_time)
    except ValueError as exc:
        if isinstance(node_time, (int, float)) and not isinstance(node_time, bool):
            numeric = float(node_time)
            if math.isfinite(numeric) and 0 <= numeric < 946_684_800:
                return receive_time, node_time, "gateway_received"
        raise exc
    quality = "node_epoch" if isinstance(node_time, (int, float)) else "node_synced"
    return parsed, node_time, quality


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric sensor value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("sensor value must be finite")
    return number


def to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    return int(value)
