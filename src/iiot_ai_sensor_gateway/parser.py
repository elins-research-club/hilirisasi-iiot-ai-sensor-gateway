from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .contracts import (
    COMPACT_KEYS,
    COMPACT_SCHEMA_V1,
    COMPACT_SCHEMA_V2,
    COMPACT_SCHEMA_V3,
    LEGACY_SENSOR_KEYS,
    SENSOR_AI_SCHEMA_V1,
    SENSOR_KEYS,
    RadioMeta,
    SensorReading,
    SensorValues,
    parse_timestamp,
    pick,
    resolve_event_time,
    stable_event_id,
    to_float,
    to_int,
)


class PayloadParser:
    def __init__(
        self,
        default_gateway_id: str,
        default_room_id: str,
        *,
        allow_legacy_v1: bool = True,
    ) -> None:
        self.default_gateway_id = default_gateway_id
        self.default_room_id = default_room_id
        self.allow_legacy_v1 = allow_legacy_v1

    @staticmethod
    def _version(data: dict[str, Any]) -> int:
        value = data.get("v", data.get("compact_version", data.get("schema_version")))
        if value in (3, "3", COMPACT_SCHEMA_V3):
            return 3
        if value in (2, "2", COMPACT_SCHEMA_V2):
            return 2
        if value in (None, 1, "1", COMPACT_SCHEMA_V1):
            return 1
        raise ValueError(f"unsupported compact sensor version: {value!r}")

    @staticmethod
    def _flags(value: Any) -> tuple[str, ...]:
        # Empty / zero-ish values mean "no flags" (research adapters may emit 0).
        if value in (None, "", 0, 0.0, False):
            return ()
        if isinstance(value, str):
            return tuple(flag.strip() for flag in value.split("|") if flag.strip())
        if isinstance(value, (list, tuple)):
            if not all(isinstance(flag, str) for flag in value):
                raise ValueError("flags list must contain strings only")
            return tuple(flag.strip() for flag in value if flag.strip())
        if isinstance(value, int):
            # Non-zero integer bitfields are not a supported compact-v2 encoding.
            raise ValueError("flags integer bitfield is not supported; use string or list of strings")
        raise ValueError("flags must be a string or a list of strings")

    @staticmethod
    def _sensor_status(value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("sensor_status must be an object")
        result: dict[str, str] = {}
        for key, status in value.items():
            if not isinstance(key, str) or not isinstance(status, (str, bool)):
                raise ValueError("sensor_status entries must be string keys and string/bool values")
            normalized = status if isinstance(status, str) else ("ok" if status else "error")
            result[key] = normalized.lower()
        return result

    @staticmethod
    def _receive_timestamp(value: Any | None) -> datetime:
        if value is None:
            return datetime.now(tz=UTC)
        return parse_timestamp(value)

    def parse(
        self,
        payload: str | bytes | dict[str, Any],
        *,
        receive_timestamp: Any | None = None,
        source: str = "unknown",
        radio_meta: dict[str, Any] | None = None,
    ) -> SensorReading:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        if not isinstance(data, dict):
            raise ValueError("payload must be a JSON object")

        compact_version = self._version(data)
        if compact_version == 1 and not self.allow_legacy_v1:
            raise ValueError("compact sensor v1 is migration-only and disabled")
        if compact_version == 2:
            required = ("v", "n", "r", "ts", "seq", "bid", "st", "q", "f", "ok", "s")
            missing = [key for key in required if key not in data]
            if missing:
                raise ValueError(f"compact sensor v2 missing required fields: {missing}")
        if compact_version == 3:
            required = (
                "v", "n", "r", "ts", "tb", "seq", "bid", "pp", "fw", "cfg",
                "cal", "hs", "f", "ok", "s",
            )
            missing = [key for key in required if key not in data]
            if missing:
                raise ValueError(f"compact sensor v3 missing required fields: {missing}")
            if data.get("pp") != "hardware_only":
                raise ValueError("compact sensor v3 processing profile must be hardware_only")
            for key in ("tb", "fw", "cfg", "cal", "hs"):
                if not isinstance(data.get(key), str) or not data[key].strip():
                    raise ValueError(f"compact sensor v3 field {key} must be a non-empty string")

        sensor_source = data.get("sensor") or data.get("s") or data
        if not isinstance(sensor_source, dict):
            raise ValueError("sensor payload must be an object")
        radio_source = radio_meta or data.get("radio") or data.get("lora") or {}
        if not isinstance(radio_source, dict):
            raise ValueError("radio metadata must be an object")

        flags = list(self._flags(pick(data, COMPACT_KEYS["flags"], ())))
        if compact_version in (2, 3):
            sensor_values = {
                name: to_float(pick(sensor_source, aliases)) for name, aliases in SENSOR_KEYS.items()
            }
            # Keep migration-only fields empty for clean v2/v3 records.
            sensor = SensorValues(**sensor_values)
        else:
            legacy_values = {
                name: to_float(pick(sensor_source, aliases))
                for name, aliases in LEGACY_SENSOR_KEYS.items()
            }
            common_values = {
                name: to_float(pick(sensor_source, aliases))
                for name, aliases in SENSOR_KEYS.items()
                if name in {"temperature_c", "humidity_pct", "pressure_hpa"}
            }
            sensor = SensorValues(**common_values, **legacy_values)
            if "legacy_contract_v1" not in flags:
                flags.append("legacy_contract_v1")

        gateway_id = str(pick(data, COMPACT_KEYS["gateway_id"], self.default_gateway_id)).strip()
        node_id = str(pick(data, COMPACT_KEYS["node_id"], "")).strip()
        room_id = str(pick(data, COMPACT_KEYS["room_id"], self.default_room_id)).strip()
        sequence = to_int(pick(data, COMPACT_KEYS["sequence"], 0))
        boot_id = str(pick(data, COMPACT_KEYS["boot_id"], "legacy" if compact_version == 1 else "")).strip()
        if compact_version in (2, 3) and not boot_id:
            raise ValueError(f"compact sensor v{compact_version} requires boot_id")
        if sequence < 0:
            raise ValueError("sequence must be >= 0")

        received_at = self._receive_timestamp(receive_timestamp)
        node_time = pick(data, COMPACT_KEYS["timestamp"])
        event_time, node_timestamp, time_quality = resolve_event_time(node_time, received_at)
        sensor_status = self._sensor_status(pick(data, COMPACT_KEYS["sensor_status"], {}))
        event_id = stable_event_id(
            gateway_id=gateway_id,
            node_id=node_id,
            boot_id=boot_id,
            sequence=sequence,
            node_timestamp=node_timestamp,
            sensor=sensor.as_dict(),
        )
        return SensorReading(
            gateway_id=gateway_id,
            node_id=node_id,
            room_id=room_id,
            timestamp=event_time,
            receive_timestamp=received_at,
            sequence=sequence,
            boot_id=boot_id,
            event_id=event_id,
            compact_version=compact_version,
            schema_version=SENSOR_AI_SCHEMA_V1,
            time_quality=time_quality,
            node_timestamp=node_timestamp,
            status=(
                str(data["hs"])
                if compact_version == 3
                else str(pick(data, COMPACT_KEYS["status"], "ok"))
            ),
            quality=(
                "hardware_observation"
                if compact_version == 3
                else str(pick(data, COMPACT_KEYS["quality"], "valid"))
            ),
            flags=tuple(flags),
            sensor_status=sensor_status,
            sensor=sensor,
            radio=RadioMeta(
                rssi=to_float(radio_source.get("rssi")),
                snr=to_float(radio_source.get("snr")),
            ),
            source=source,
            raw=data,
            time_basis=str(data.get("tb", "legacy_unspecified")),
            processing_profile=str(data.get("pp", "legacy_node_preprocessed" if compact_version == 2 else "legacy_unspecified")),
            firmware_version=str(data.get("fw", "unknown")),
            hardware_config_version=str(data.get("cfg", "unknown")),
            calibration_version=str(data.get("cal", "unknown")),
            hardware_summary=str(data.get("hs", data.get("st", "unknown"))),
            preprocessing_version="unprocessed",
            source_event_id=event_id,
        )
