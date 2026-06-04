from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

USED_COLUMNS = ('ts', 'device', 'temp', 'humidity', 'co', 'lpg', 'smoke')
IGNORED_COLUMNS = ('light', 'motion')
UNAVAILABLE_FIELDS = ('pressure_hpa', 'voc_raw')
PROXY_FIELDS = ('bme_gas_raw', 'voc_proxy')

@dataclass(frozen=True)
class GaryConversionStats:
    total_rows: int
    written_rows: int
    invalid_rows: int
    devices: tuple[str, ...]
    start_timestamp: str | None
    end_timestamp: str | None
    used_columns: tuple[str, ...] = USED_COLUMNS
    ignored_columns: tuple[str, ...] = IGNORED_COLUMNS
    unavailable_fields: tuple[str, ...] = UNAVAILABLE_FIELDS
    proxy_fields: tuple[str, ...] = PROXY_FIELDS

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

def _to_float(value: str | None) -> float | None:
    if value is None or value == '':
        return None
    return float(value)

def _timestamp(value: str) -> datetime:
    return datetime.fromtimestamp(float(value), tz=UTC)

def _avg_present(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None

def convert_gary_stafford_csv(input_csv: str | Path, output_jsonl: str | Path, gateway_id: str = 'gary_stafford_public', room_id: str = 'gary_public') -> GaryConversionStats:
    input_path = Path(input_csv)
    output_path = Path(output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    seq_by_device: dict[str, int] = {}
    devices: set[str] = set()
    total = written = invalid = 0
    start: datetime | None = None
    end: datetime | None = None
    with input_path.open(newline='', encoding='utf-8') as src, output_path.open('w', encoding='utf-8') as dst:
        for row in csv.DictReader(src):
            total += 1
            try:
                ts = _timestamp(row['ts'])
                device = row['device']
                seq = seq_by_device.get(device, 0)
                seq_by_device[device] = seq + 1
                devices.add(device)
                start = ts if start is None or ts < start else start
                end = ts if end is None or ts > end else end
                lpg = _to_float(row.get('lpg'))
                smoke = _to_float(row.get('smoke'))
                gas_proxy = _avg_present(lpg, smoke)
                sensor = {
                    'temperature_c': _to_float(row.get('temp')),
                    'humidity_pct': _to_float(row.get('humidity')),
                    'co_raw': _to_float(row.get('co')),
                    'bme_gas_raw': gas_proxy,
                    'gas_raw': gas_proxy,
                    'pressure_hpa': None,
                    'voc_raw': None,
                }
                flags = ['pressure_unavailable', 'voc_not_native', 'gas_proxy_from_lpg_smoke']
                if any(sensor[name] is None for name in ('temperature_c', 'humidity_pct', 'co_raw', 'bme_gas_raw')):
                    flags.append('missing_target_or_proxy_feature')
                payload = {
                    'gw': gateway_id,
                    'n': device,
                    'r': room_id,
                    'ts': ts.isoformat(),
                    'seq': seq,
                    'st': 'ok',
                    'q': 'valid',
                    'f': flags,
                    'sensor': sensor,
                    'meta': {
                        'source_dataset': 'gary_stafford_iot_telemetry',
                        'used_columns': list(USED_COLUMNS),
                        'ignored_columns': list(IGNORED_COLUMNS),
                        'unavailable_fields': list(UNAVAILABLE_FIELDS),
                        'proxy_fields': list(PROXY_FIELDS),
                        'gas_proxy_source': ['lpg', 'smoke'],
                    },
                }
                dst.write(json.dumps(payload, separators=(',', ':')) + '\n')
                written += 1
            except (KeyError, TypeError, ValueError):
                invalid += 1
    return GaryConversionStats(total, written, invalid, tuple(sorted(devices)), start.isoformat() if start else None, end.isoformat() if end else None)
