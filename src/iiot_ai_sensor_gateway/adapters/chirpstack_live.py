from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import struct
from datetime import UTC, datetime
from numbers import Real
from typing import Any

from ..contracts import RadioMeta, SensorReading, SensorValues

SOURCE_CONTRACT = "chirpstack_live.v1"

_ALIASES = {
    "temperature_c": ("temperature", "temp", "temp_c"),
    "humidity_pct": ("humidity", "hum", "rh"),
    "pressure_hpa": ("pressure", "pressure_hpa"),
    "bme_gas_ohm": ("gas_resistance", "gasResistance"),
    "co2_ppm": ("co2", "co2_ppm"),
    "co_ppm": ("co", "co_ppm"),
    "current_ma": ("current_ma", "current_mA"),
}


def _utc(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _first(mapping: dict[str, Any], aliases: tuple[str, ...]) -> float | None:
    for name in aliases:
        if name in mapping:
            return _finite(mapping[name])
    return None


def decode_binary_payload(raw: bytes) -> tuple[dict[str, Any], str]:
    if len(raw) == 5:
        raw_id, temp_raw, hum_raw = struct.unpack(">BHH", raw)
        return {
            "id": raw_id,
            "temperature": round(temp_raw / 10.0, 1),
            "humidity": round(hum_raw / 10.0, 1),
        }, "bme_lite_v1_5byte"
    if len(raw) == 7:
        raw_id, temp_raw, hum_raw, current_raw = struct.unpack(">BhHH", raw)
        return {
            "id": raw_id,
            "temperature": round(temp_raw / 10.0, 1),
            "humidity": round(hum_raw / 10.0, 1),
            "current_ma": round(current_raw / 100.0, 2),
        }, "bme_current_v1_7byte"
    if len(raw) == 11:
        raw_id, temp_raw, hum_raw, co2_raw, co_raw, no2_raw = struct.unpack(">BhHHHH", raw)
        return {
            "id": raw_id,
            "temperature": round(temp_raw / 10.0, 1),
            "humidity": round(hum_raw / 10.0, 1),
            "co2": co2_raw,
            "co": round(co_raw / 10.0, 1),
            "no2": round(no2_raw / 100.0, 2),
        }, "legacy_env_v1_11byte"
    raise ValueError(f"unsupported ChirpStack binary payload length: {len(raw)}")


def _sensor_object(event: dict[str, Any]) -> tuple[dict[str, Any], str]:
    obj = event.get("object")
    encoded = event.get("data")
    raw: bytes | None = None
    if isinstance(encoded, str) and encoded:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raw = None

    if raw is not None and len(raw) in {5, 7, 11}:
        return decode_binary_payload(raw)

    if isinstance(obj, dict) and obj:
        return dict(obj), "chirpstack_object"

    if raw is not None:
        return decode_binary_payload(raw)
    raise ValueError("ChirpStack event has neither usable decoded object nor Base64 data")


def _radio(event: dict[str, Any]) -> RadioMeta:
    rx = event.get("rxInfo")
    first = rx[0] if isinstance(rx, list) and rx and isinstance(rx[0], dict) else {}
    return RadioMeta(rssi=_finite(first.get("rssi")), snr=_finite(first.get("snr")))


def _event_id(
    gateway_id: str,
    dev_eui: str,
    dev_addr: str,
    f_cnt: int,
    deduplication_id: str,
) -> str:
    identity = {
        "gateway_id": gateway_id,
        "dev_eui": dev_eui,
        "dev_addr": dev_addr,
        "f_cnt": f_cnt,
        "deduplication_id": deduplication_id,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "se_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class ChirpStackLiveAdapter:
    """Convert ChirpStack v4 uplinks into the internal SensorReading.

    This source is explicitly *not* compact_sensor.v3. LoRaWAN session
    identity is kept separately and is only used as the state-cycle boundary.
    """

    def __init__(
        self,
        gateway_id: str,
        *,
        room_by_dev_eui: dict[str, str] | None = None,
        allow_dev_eui: set[str] | None = None,
        default_room_id: str = "room_A",
    ) -> None:
        self.gateway_id = gateway_id.strip()
        if not self.gateway_id:
            raise ValueError("gateway_id must not be empty")
        self.room_by_dev_eui = {
            key.lower(): value for key, value in (room_by_dev_eui or {}).items()
        }
        self.allow_dev_eui = {item.lower() for item in (allow_dev_eui or set())}
        self.default_room_id = default_room_id

    def adapt(
        self,
        event: dict[str, Any],
        *,
        topic: str = "",
        receive_timestamp: str | datetime | None = None,
    ) -> SensorReading:
        if not isinstance(event, dict):
            raise ValueError("ChirpStack uplink must be an object")
        device = event.get("deviceInfo")
        if not isinstance(device, dict):
            raise ValueError("ChirpStack uplink missing deviceInfo")
        dev_eui = str(device.get("devEui", "")).strip().lower()
        if not dev_eui:
            raise ValueError("ChirpStack uplink missing DevEUI")
        if self.allow_dev_eui and dev_eui not in self.allow_dev_eui:
            raise ValueError(f"DevEUI not allowed: {dev_eui}")
        f_cnt = event.get("fCnt")
        if isinstance(f_cnt, bool) or not isinstance(f_cnt, int) or f_cnt < 0:
            raise ValueError("ChirpStack uplink fCnt must be a non-negative integer")
        dev_addr = str(event.get("devAddr", "")).strip().lower()
        deduplication_id = str(event.get("deduplicationId", "")).strip()
        source_values, payload_format = _sensor_object(event)

        values = {
            field: _first(source_values, aliases) for field, aliases in _ALIASES.items()
        }
        if values["temperature_c"] is None or values["humidity_pct"] is None:
            raise ValueError("live uplink requires finite temperature and humidity")

        receive_time = _utc(receive_timestamp)
        event_time = _utc(event.get("time") if isinstance(event.get("time"), str) else None)
        source_id = _event_id(
            self.gateway_id, dev_eui, dev_addr, f_cnt, deduplication_id
        )
        metadata: dict[str, Any] = {
            "dev_eui": dev_eui,
            "dev_addr": dev_addr or None,
            "f_port": event.get("fPort"),
            "deduplication_id": deduplication_id or None,
            "chirpstack_topic": topic or None,
            "device_name": device.get("deviceName"),
            "application_id": device.get("applicationId"),
            "application_name": device.get("applicationName"),
            "payload_format": payload_format,
        }
        if "id" in source_values:
            metadata["source_sensor_id"] = source_values.get("id")
        if "no2" in source_values:
            metadata["no2_source_unmapped"] = source_values["no2"]

        return SensorReading(
            gateway_id=self.gateway_id,
            node_id=dev_eui,
            room_id=self.room_by_dev_eui.get(dev_eui, self.default_room_id),
            timestamp=event_time,
            receive_timestamp=receive_time,
            sequence=f_cnt,
            boot_id="unknown",
            event_id=source_id,
            compact_version=None,
            schema_version=SOURCE_CONTRACT,
            time_quality="source_clock" if event.get("time") else "gateway_received",
            node_timestamp=event.get("time"),
            sensor=SensorValues(**values),
            status="ok",
            quality="valid",
            flags=(),
            sensor_status={},
            radio=_radio(event),
            source="chirpstack_mqtt",
            raw=dict(event),
            time_basis="rfc3339" if event.get("time") else "gateway_receive",
            processing_profile="gateway_live_adapter",
            firmware_version="unknown",
            hardware_config_version="unknown",
            calibration_version="unknown",
            hardware_summary="unknown",
            preprocessing_version="unprocessed",
            source_event_id=source_id,
            source_contract=SOURCE_CONTRACT,
            source_session_id=f"lorawan:{dev_addr}" if dev_addr else f"lorawan:{dev_eui}",
            source_metadata=metadata,
        )
