#!/usr/bin/env python3
"""Convert iiot.dataset_record.v1 JSONL into compact-v2-like payloads for pipeline run.

Honesty rules:
- Only copies fields that exist in sensor{} (never fabricates ppm/proxy).
- Optional --include-reference-as-sensor maps selected reference.* fields into sensor
  targets for research lanes only (e.g. Fidas PM reference -> pm25). Marked in meta.
- Missing sensors stay null / zeroed by the normal pipeline presence flags.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_REF_MAP = {
    "pm1_ug_m3": "pm1_ug_m3",
    "pm25_ug_m3": "pm25_ug_m3",
    "pm10_ug_m3": "pm10_ug_m3",
    # UCI CO is mg/m3 — do NOT silently map to co_ppm.
}


def _sensor_block(record: dict[str, Any], include_reference: bool) -> dict[str, Any]:
    sensor = dict(record.get("sensor") or {})
    if include_reference:
        reference = record.get("reference") or {}
        for src, dst in DEFAULT_REF_MAP.items():
            if sensor.get(dst) is None and reference.get(src) is not None:
                sensor[dst] = reference[src]
    return sensor


def convert(
    input_jsonl: Path,
    output_jsonl: Path,
    *,
    gateway_id: str,
    include_reference_as_sensor: bool,
    node_id_field: str = "device_id",
) -> dict[str, Any]:
    written = 0
    rejected = 0
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with input_jsonl.open("r", encoding="utf-8") as src, output_jsonl.open(
        "w", encoding="utf-8"
    ) as dst:
        for line_no, line in enumerate(src, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                rejected += 1
                continue
            if record.get("schema_version") != "iiot.dataset_record.v1":
                rejected += 1
                continue
            quality = record.get("quality") or {}
            if quality.get("valid") is False:
                rejected += 1
                continue
            sensor = _sensor_block(record, include_reference_as_sensor)
            node_id = str(record.get(node_id_field) or record.get("site_id") or "unknown_device")
            # Compact v2 style payload the project pipeline understands.
            payload = {
                "v": 2,
                "n": node_id,
                "r": str(record.get("site_id") or "public_site"),
                "ts": record.get("timestamp"),
                "seq": line_no,
                "bid": gateway_id,
                "st": "ok",
                "q": "valid",
                "f": "",
                "ok": {},
                "s": {
                    "tc": sensor.get("temperature_c"),
                    "h": sensor.get("humidity_pct"),
                    "p": sensor.get("pressure_hpa"),
                    "bme": sensor.get("bme_gas_ohm") or sensor.get("bme_gas_raw"),
                    "co": sensor.get("co_ppm"),
                    "n2mv": sensor.get("no2_raw_mv"),
                    "n2r": sensor.get("no2_ratio"),
                    "o3": sensor.get("o3_ppm"),
                    "co2": sensor.get("co2_ppm"),
                    "pm1": sensor.get("pm1_ug_m3"),
                    "pm25": sensor.get("pm25_ug_m3"),
                    "pm10": sensor.get("pm10_ug_m3"),
                    "bv": sensor.get("battery_voltage"),
                    "bi": sensor.get("current_ma"),
                    "bp": sensor.get("power_mw"),
                },
                "meta": {
                    "source_dataset": record.get("dataset_id"),
                    "lane": record.get("lane"),
                    "record_id": record.get("record_id"),
                    "include_reference_as_sensor": include_reference_as_sensor,
                },
            }
            dst.write(json.dumps(payload, separators=(",", ":")) + "\n")
            written += 1
    return {
        "input": str(input_jsonl),
        "output": str(output_jsonl),
        "written": written,
        "rejected": rejected,
        "include_reference_as_sensor": include_reference_as_sensor,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--gateway-id", default="public_dataset_gateway")
    parser.add_argument(
        "--include-reference-as-sensor",
        action="store_true",
        help="research-only: copy selected reference PM fields into sensor targets",
    )
    args = parser.parse_args(argv)
    stats = convert(
        Path(args.input),
        Path(args.output),
        gateway_id=args.gateway_id,
        include_reference_as_sensor=args.include_reference_as_sensor,
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
