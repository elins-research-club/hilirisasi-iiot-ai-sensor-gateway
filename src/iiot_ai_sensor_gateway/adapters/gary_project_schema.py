from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CSV_COLUMNS = (
    "timestamp",
    "node_id",
    "sequence",
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "bme_gas_raw",
    "co_raw",
)


@dataclass(frozen=True)
class GaryProjectSchemaStats:
    total_rows: int
    written_rows: int
    invalid_rows: int
    devices: tuple[str, ...]
    start_timestamp: str | None
    end_timestamp: str | None
    pressure_min_hpa: float | None
    pressure_max_hpa: float | None
    bme_gas_min: float | None
    bme_gas_max: float | None
    output_csv: str
    output_jsonl: str
    output_payloads: str

    def as_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["devices"] = list(self.devices)
        return data


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _timestamp(value: str) -> datetime:
    return datetime.fromtimestamp(float(value), tz=UTC)


def _avg_present(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _node_phase(node_id: str) -> float:
    return (sum(ord(char) for char in node_id) % 360) * math.pi / 180.0


def _synthetic_pressure(ts: datetime, node_id: str, temperature_c: float, humidity_pct: float) -> float:
    day = ts.timestamp() / 86_400.0
    phase = _node_phase(node_id)
    daily = 1.7 * math.sin((2.0 * math.pi * day) + phase)
    slow = 0.45 * math.sin((2.0 * math.pi * day / 3.0) + (phase / 2.0))
    comfort_adjustment = ((50.0 - humidity_pct) * 0.012) + ((22.0 - temperature_c) * 0.018)
    pressure = 1011.2 + daily + slow + comfort_adjustment
    return round(min(1016.0, max(1006.0, pressure)), 2)


def _scaled_bme_gas(gas_base: float, gas_min: float, gas_max: float) -> float:
    if gas_max <= gas_min:
        return 1500.0
    normalized = (gas_base - gas_min) / (gas_max - gas_min)
    scaled = 500.0 + (normalized * 4000.0)
    return round(min(4500.0, max(500.0, scaled)), 2)


def _scan_gas_range(input_csv: Path) -> tuple[float, float]:
    values: list[float] = []
    with input_csv.open(newline="", encoding="utf-8") as src:
        for row in csv.DictReader(src):
            gas_base = _avg_present(_to_float(row.get("lpg")), _to_float(row.get("smoke")))
            if gas_base is not None:
                values.append(gas_base)
    if not values:
        return 0.0, 1.0
    return min(values), max(values)


def derive_gary_project_schema(
    input_csv: str | Path,
    output_csv: str | Path = "data/derived/gary_project_sensor_schema.csv",
    output_jsonl: str | Path = "data/derived/gary_project_sensor_schema.jsonl",
    output_payloads: str | Path = "data/derived/gary_project_sensor_payloads.jsonl",
    gateway_id: str = "gary_project_schema",
    room_id: str = "gary_public",
) -> GaryProjectSchemaStats:
    input_path = Path(input_csv)
    csv_path = Path(output_csv)
    jsonl_path = Path(output_jsonl)
    payload_path = Path(output_payloads)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.parent.mkdir(parents=True, exist_ok=True)

    gas_min, gas_max = _scan_gas_range(input_path)
    seq_by_device: dict[str, int] = {}
    devices: set[str] = set()
    total = written = invalid = 0
    start: datetime | None = None
    end: datetime | None = None
    pressure_values: list[float] = []
    bme_values: list[float] = []

    with (
        input_path.open(newline="", encoding="utf-8") as src,
        csv_path.open("w", newline="", encoding="utf-8") as csv_out,
        jsonl_path.open("w", encoding="utf-8") as jsonl_out,
        payload_path.open("w", encoding="utf-8") as payload_out,
    ):
        writer = csv.DictWriter(csv_out, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in csv.DictReader(src):
            total += 1
            try:
                ts = _timestamp(row["ts"])
                node_id = row["device"]
                sequence = seq_by_device.get(node_id, 0)
                seq_by_device[node_id] = sequence + 1
                temperature_c = round(float(row["temp"]), 2)
                humidity_pct = round(float(row["humidity"]), 2)
                co_raw = round(float(row["co"]), 6)
                gas_base = _avg_present(_to_float(row.get("lpg")), _to_float(row.get("smoke")))
                if gas_base is None:
                    raise ValueError("missing gas proxy source")
                pressure_hpa = _synthetic_pressure(ts, node_id, temperature_c, humidity_pct)
                bme_gas_raw = _scaled_bme_gas(gas_base, gas_min, gas_max)

                csv_record = {
                    "timestamp": ts.isoformat(),
                    "node_id": node_id,
                    "sequence": sequence,
                    "temperature_c": temperature_c,
                    "humidity_pct": humidity_pct,
                    "pressure_hpa": pressure_hpa,
                    "bme_gas_raw": bme_gas_raw,
                    "co_raw": co_raw,
                }
                sensor = {
                    "temperature_c": temperature_c,
                    "humidity_pct": humidity_pct,
                    "pressure_hpa": pressure_hpa,
                    "bme_gas_raw": bme_gas_raw,
                    "co_raw": co_raw,
                }
                canonical = {
                    "gw": gateway_id,
                    "n": node_id,
                    "r": room_id,
                    "ts": ts.isoformat(),
                    "seq": sequence,
                    "st": "ok",
                    "q": "valid",
                    "sensor": sensor,
                    "meta": {
                        "source_dataset": "gary_stafford_iot_telemetry",
                        "schema": "project_sensor_schema_derived",
                        "notes": [
                            "pressure_hpa is synthetic smooth barometric trend",
                            "bme_gas_raw is scaled from Gary lpg/smoke gas-like columns",
                        ],
                    },
                }
                compact = {
                    "v": 1,
                    "n": node_id,
                    "r": room_id,
                    "ts": ts.isoformat(),
                    "seq": sequence,
                    "st": "ok",
                    "q": "valid",
                    "f": "",
                    "s": {
                        "tc": temperature_c,
                        "h": humidity_pct,
                        "p": pressure_hpa,
                        "bme": bme_gas_raw,
                        "co": co_raw,
                    },
                }
                writer.writerow(csv_record)
                jsonl_out.write(json.dumps(canonical, separators=(",", ":")) + "\n")
                payload_out.write(json.dumps(compact, separators=(",", ":")) + "\n")

                devices.add(node_id)
                start = ts if start is None or ts < start else start
                end = ts if end is None or ts > end else end
                pressure_values.append(pressure_hpa)
                bme_values.append(bme_gas_raw)
                written += 1
            except (KeyError, TypeError, ValueError):
                invalid += 1

    return GaryProjectSchemaStats(
        total,
        written,
        invalid,
        tuple(sorted(devices)),
        start.isoformat() if start else None,
        end.isoformat() if end else None,
        min(pressure_values) if pressure_values else None,
        max(pressure_values) if pressure_values else None,
        min(bme_values) if bme_values else None,
        max(bme_values) if bme_values else None,
        str(csv_path),
        str(jsonl_path),
        str(payload_path),
    )
