from __future__ import annotations

import json
from typing import Any

from .contracts import COMPACT_KEYS, SENSOR_KEYS, RadioMeta, SensorReading, SensorValues
from .contracts import parse_timestamp, pick, to_float, to_int

class PayloadParser:
    def __init__(self, default_gateway_id: str, default_room_id: str) -> None:
        self.default_gateway_id = default_gateway_id
        self.default_room_id = default_room_id

    def parse(self, payload: str | bytes | dict[str, Any]) -> SensorReading:
        if isinstance(payload, bytes):
            payload = payload.decode('utf-8')
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        sensor_source = data.get('sensor') or data.get('s') or data
        radio_source = data.get('radio') or data.get('lora') or {}
        flags = pick(data, COMPACT_KEYS['flags'], []) or []
        if isinstance(flags, str):
            flags = [flag for flag in flags.split('|') if flag]
        sensor = SensorValues(**{name: to_float(pick(sensor_source, aliases)) for name, aliases in SENSOR_KEYS.items()})
        return SensorReading(
            gateway_id=str(pick(data, COMPACT_KEYS['gateway_id'], self.default_gateway_id)),
            node_id=str(pick(data, COMPACT_KEYS['node_id'], '')),
            room_id=str(pick(data, COMPACT_KEYS['room_id'], self.default_room_id)),
            timestamp=parse_timestamp(pick(data, COMPACT_KEYS['timestamp'])),
            sequence=to_int(pick(data, COMPACT_KEYS['sequence'], 0)),
            status=str(pick(data, COMPACT_KEYS['status'], 'ok')),
            quality=str(pick(data, COMPACT_KEYS['quality'], 'valid')),
            flags=tuple(str(flag) for flag in flags),
            sensor=sensor,
            radio=RadioMeta(rssi=to_float(radio_source.get('rssi')), snr=to_float(radio_source.get('snr'))),
            raw=data,
        )
