from __future__ import annotations

import csv
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

USED_COLUMNS = ("ts", "device", "temp", "humidity", "co", "lpg", "smoke")
IGNORED_COLUMNS = ("light", "motion")
UNAVAILABLE_FIELDS = ("pressure_hpa",)
PROXY_FIELDS = ("bme_gas_raw", "voc_proxy")

RANGES = {
    "temperature_c": (-10.0, 80.0),
    "humidity_pct": (0.0, 100.0),
    "co_raw": (0.0, 0.05),
    "bme_gas_raw": (0.0, 5000.0),
}


@dataclass(frozen=True)
class GaryEsp32SimulationStats:
    total_rows: int
    written_rows: int
    invalid_rows: int
    devices: tuple[str, ...]
    start_timestamp: str | None
    end_timestamp: str | None
    moving_average_window: int
    used_columns: tuple[str, ...] = USED_COLUMNS
    ignored_columns: tuple[str, ...] = IGNORED_COLUMNS
    unavailable_fields: tuple[str, ...] = UNAVAILABLE_FIELDS
    proxy_fields: tuple[str, ...] = PROXY_FIELDS
    output_format: str = "esp32_compact_lora_jsonl"

    def as_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["devices"] = list(self.devices)
        data["used_columns"] = list(self.used_columns)
        data["ignored_columns"] = list(self.ignored_columns)
        data["unavailable_fields"] = list(self.unavailable_fields)
        data["proxy_fields"] = list(self.proxy_fields)
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


def _moving_average(
    buffers: dict[tuple[str, str], deque[float]],
    device: str,
    field_name: str,
    value: float | None,
) -> float | None:
    if value is None:
        return None
    key = (device, field_name)
    buffers[key].append(value)
    return sum(buffers[key]) / len(buffers[key])


def _range_flags(values: dict[str, float | None]) -> list[str]:
    flags: list[str] = []
    for field_name, value in values.items():
        if value is None:
            flags.append(f"missing_{field_name}")
            continue
        low, high = RANGES[field_name]
        if not low <= value <= high:
            flags.append(f"range_{field_name}")
    return flags


def _round_payload_value(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def simulate_gary_esp32_payloads(
    input_csv: str | Path,
    output_jsonl: str | Path,
    room_id: str = "gary_public",
    moving_average_window: int = 3,
) -> GaryEsp32SimulationStats:
    """Convert Gary CSV rows into ESP32-C6-like compact LoRa payload JSONL.

    This is a laptop-side simulation of the firmware layer. It does not replace
    the real ESP32-C6 firmware, but it lets the Raspberry Pi pipeline consume the
    same compact schema expected from LoRa.
    """

    input_path = Path(input_csv)
    output_path = Path(output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    seq_by_device: dict[str, int] = {}
    smooth_buffers: dict[tuple[str, str], deque[float]] = defaultdict(
        lambda: deque(maxlen=max(1, moving_average_window))
    )
    devices: set[str] = set()
    total = written = invalid = 0
    start: datetime | None = None
    end: datetime | None = None

    with input_path.open(newline="", encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for row in csv.DictReader(src):
            total += 1
            try:
                ts = _timestamp(row["ts"])
                device = row["device"]
                lpg = _to_float(row.get("lpg"))
                smoke = _to_float(row.get("smoke"))
                raw_values = {
                    "temperature_c": _to_float(row.get("temp")),
                    "humidity_pct": _to_float(row.get("humidity")),
                    "co_raw": _to_float(row.get("co")),
                    "bme_gas_raw": _avg_present(lpg, smoke),
                }
                smoothed = {
                    name: _moving_average(smooth_buffers, device, name, value)
                    for name, value in raw_values.items()
                }
                flags = [
                    "pressure_unavailable",
                    "voc_not_native",
                    "gas_proxy_from_lpg_smoke",
                ]
                flags.extend(_range_flags(smoothed))
                quality = "valid" if not any(flag.startswith(("missing_", "range_")) for flag in flags) else "invalid"

                seq = seq_by_device.get(device, 0)
                seq_by_device[device] = seq + 1
                devices.add(device)
                start = ts if start is None or ts < start else start
                end = ts if end is None or ts > end else end

                sensor = {
                    "tc": _round_payload_value(smoothed["temperature_c"], 2),
                    "h": _round_payload_value(smoothed["humidity_pct"], 2),
                    "bme": _round_payload_value(smoothed["bme_gas_raw"], 6),
                    "co": _round_payload_value(smoothed["co_raw"], 6),
                }
                payload = {
                    "v": 1,
                    "n": device,
                    "r": room_id,
                    "ts": ts.isoformat(),
                    "seq": seq,
                    "st": "ok" if quality == "valid" else "sensor_error",
                    "q": quality,
                    "f": "|".join(flags),
                    "s": sensor,
                }
                dst.write(json.dumps(payload, separators=(",", ":")) + "\n")
                written += 1
            except (KeyError, TypeError, ValueError):
                invalid += 1

    return GaryEsp32SimulationStats(
        total,
        written,
        invalid,
        tuple(sorted(devices)),
        start.isoformat() if start else None,
        end.isoformat() if end else None,
        max(1, moving_average_window),
    )
