from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

COMPACT_KEYS = {
    'gateway_id': ('gateway_id', 'gw'),
    'node_id': ('node_id', 'node', 'n'),
    'room_id': ('room_id', 'room', 'r'),
    'timestamp': ('timestamp', 'ts', 't'),
    'sequence': ('sequence', 'seq', 'sq'),
    'status': ('status', 'st'),
    'quality': ('quality', 'q'),
    'flags': ('flags', 'f'),
}

SENSOR_KEYS = {
    'temperature_c': ('temperature_c', 'temp_c', 'tc'),
    'humidity_pct': ('humidity_pct', 'hum_pct', 'rh', 'h'),
    'pressure_hpa': ('pressure_hpa', 'pressure', 'p_hpa', 'p'),
    'voc_raw': ('voc_raw', 'voc', 'v'),
    'bme_gas_raw': ('bme_gas_raw', 'bme_gas', 'bme'),
    'co_raw': ('co_raw', 'co'),
    'gas_raw': ('gas_raw', 'gas', 'g'),
    'battery_voltage': ('battery_voltage', 'batt_v', 'bv'),
    'current_ma': ('current_ma', 'current_mA', 'ma', 'i'),
    'power_mw': ('power_mw', 'power_mW', 'mw'),
}

@dataclass(frozen=True)
class SensorValues:
    temperature_c: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    voc_raw: float | None = None
    bme_gas_raw: float | None = None
    co_raw: float | None = None
    gas_raw: float | None = None
    battery_voltage: float | None = None
    current_ma: float | None = None
    power_mw: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return self.__dict__.copy()

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
    sequence: int
    sensor: SensorValues
    status: str = 'ok'
    quality: str = 'valid'
    flags: tuple[str, ...] = field(default_factory=tuple)
    radio: RadioMeta = field(default_factory=RadioMeta)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            'gateway_id': self.gateway_id,
            'node_id': self.node_id,
            'room_id': self.room_id,
            'timestamp': self.timestamp.isoformat(),
            'sequence': self.sequence,
            'status': self.status,
            'quality': self.quality,
            'flags': list(self.flags),
            'sensor': self.sensor.as_dict(),
            'radio': self.radio.as_dict(),
        }

@dataclass(frozen=True)
class ValidationResult:
    reading: SensorReading
    is_valid: bool
    issues: tuple[str, ...] = field(default_factory=tuple)

    def as_record(self) -> dict[str, Any]:
        record = self.reading.as_record()
        record['is_valid'] = self.is_valid
        record['issues'] = list(self.issues)
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
            'gateway_id': self.gateway_id,
            'node_id': self.node_id,
            'room_id': self.room_id,
            'start_timestamp': self.start_timestamp.isoformat(),
            'end_timestamp': self.end_timestamp.isoformat(),
            'feature_names': list(self.feature_names),
            'shape': list(self.shape),
            'x': self.x,
        }

def pick(mapping: dict[str, Any], aliases: tuple[str, ...], default: Any = None) -> Any:
    for alias in aliases:
        if alias in mapping:
            return mapping[alias]
    return default

def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, int | float):
        seconds = value / 1000 if value > 10_000_000_000 else value
        dt = datetime.fromtimestamp(seconds, tz=UTC)
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    else:
        raise ValueError('timestamp is required')
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

def to_float(value: Any) -> float | None:
    if value is None or value == '':
        return None
    return float(value)

def to_int(value: Any, default: int = 0) -> int:
    if value is None or value == '':
        return default
    return int(value)

