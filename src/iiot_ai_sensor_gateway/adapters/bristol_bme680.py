from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .uci_air_quality import CANONICAL_FIELDS

CANONICAL_SENSOR_MAP = {
    "temperature": "temperature_c",
    "humidity": "humidity_pct",
    "pressure": "pressure_hpa",
}
REFERENCE_SENSOR_MAP = {
    "gas": "bme680_iaq_index",
    "mic": "measurement_accuracy",
    "rssi": "rssi",
    "light": "light_lux",
    "accelerometer": "acceleration_g",
}


@dataclass(frozen=True)
class BristolBme680Stats:
    source: str
    output: str
    total_rows: int
    written_rows: int
    rejected_rows: int
    devices: tuple[str, ...]
    sensor_types: tuple[str, ...]

    def as_dict(self):
        result = asdict(self)
        result["devices"] = list(self.devices)
        result["sensor_types"] = list(self.sensor_types)
        return result


def _field(row: dict[str, str], *names: str) -> str:
    lower = {key.strip().lower(): value for key, value in row.items() if key is not None}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    raise KeyError(names[0])


def adapt_bristol_bme680_csv(
    input_csv: str | Path,
    output_jsonl: str | Path,
) -> BristolBme680Stats:
    source = Path(input_csv)
    output = Path(output_jsonl)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = written = rejected = 0
    devices: set[str] = set()
    sensor_types: set[str] = set()
    with source.open(encoding="utf-8-sig", newline="") as input_file, output.open(
        "w", encoding="utf-8"
    ) as output_file:
        reader = csv.DictReader(input_file)
        if reader.fieldnames is None:
            raise ValueError("Bristol CSV has no header")
        for row_number, row in enumerate(reader, start=2):
            total += 1
            try:
                timestamp_raw = float(_field(row, "Time", "Timestamp"))
                value = float(_field(row, "Value"))
                device_id = _field(row, "DeviceId", "Device").strip()
                sensor_type = _field(row, "Sensor").strip().lower()
                if not device_id or not sensor_type or not math.isfinite(timestamp_raw) or not math.isfinite(value):
                    raise ValueError("empty or non-finite Bristol value")
                timestamp = datetime.fromtimestamp(timestamp_raw, tz=UTC).isoformat()
            except (KeyError, TypeError, ValueError, OverflowError):
                rejected += 1
                continue
            devices.add(device_id)
            sensor_types.add(sensor_type)
            sensor = {name: None for name in CANONICAL_FIELDS}
            reference: dict[str, float] = {}
            canonical_name = CANONICAL_SENSOR_MAP.get(sensor_type)
            reference_name = REFERENCE_SENSOR_MAP.get(sensor_type)
            if canonical_name:
                sensor[canonical_name] = value
            elif reference_name:
                reference[reference_name] = value
            else:
                reference[f"source_{sensor_type}"] = value
            record = {
                "schema_version": "iiot.dataset_record.v1",
                "dataset_id": "bristol_smart_building_bme680",
                "lane": "indoor_bme_like",
                "record_id": f"bris-{source.name}-{row_number}",
                "timestamp": timestamp,
                "device_id": device_id,
                "site_id": "university_of_bristol_office",
                "sensor": sensor,
                "reference": reference,
                "quality": {
                    "valid": canonical_name is not None or reference_name is not None,
                    "unit_conversion_applied": False,
                    "notes": [
                        "Each output row preserves one source measurement; downstream resampling joins fields by time/device.",
                        "BME680 Gas is retained as an IAQ index, not bme_gas_ohm or calibrated CO2.",
                        "BME680 is similar-purpose but not chip-identical to project BME688.",
                    ],
                },
                "provenance": {
                    "source_file": str(source),
                    "source_row": row_number,
                    "source_sensor_type": sensor_type,
                    "license": "Non-Commercial Government Licence for public sector information",
                },
            }
            output_file.write(json.dumps(record, separators=(",", ":")) + "\n")
            written += 1
    return BristolBme680Stats(
        str(source),
        str(output),
        total,
        written,
        rejected,
        tuple(sorted(devices)),
        tuple(sorted(sensor_types)),
    )
