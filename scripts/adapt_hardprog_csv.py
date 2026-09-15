#!/usr/bin/env python3
"""Adapt hardprog per-sensor CSV captures into canonical compact_sensor.v3 JSONL.

Raw CSV files from the hardware-programmer team (Google Drive DATASET folder,
downloaded 2026-08-28) are per-sensor single-signal captures with an ESP32
uptime millisecond timestamp (relative to boot), NOT wall-clock UTC.

Design decisions (project rules):
- Raw CSVs are immutable: we only READ them; outputs go to a separate lane.
- Timestamps are node uptime ms -> they are emitted as `tb=uptime_s`.
  The gateway/replay layer must supply receive time out-of-band; this adapter
  never fabricates wall-clock event time.
- Each sensor file emits records with ONLY that sensor's field populated; all
  other RAB fields are null (missing), per shared-canonical-schema.
- Values are converted to canonical units: NO2 `voltage_V` becomes raw mV in
  `n2mv`; this is not a calibrated ppm claim. Raw ADC has no v3 field and is
  intentionally not relabeled as mV.
- ToF is NOT part of the RAB node sensor contract, so it is stored as a
  reference lane only (hybrid camera scope), not fed to sensor models.
- CO is constant 0.00 ppm in the capture -> kept raw (honest), but flagged.
- CO2 capture saturates at 5000 ppm and has -1 values -> kept raw, flagged.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

GATEWAY_ID = "gw_hardprog_vps"
ROOM_ID = "lab_hardprog"
NODE_ID = "esp32c6_hardprog"
BOOT_ID = "boot-hardprog-csv-20260828"

SENSOR_FIELD_MAP = {
    "data_bme.csv": {
        "temperature_c": "temperature_c",
        "humidity_rh": "humidity_pct",
        "pressure_hpa": "pressure_hpa",
        "gas_ohm": "bme_gas_ohm",
    },
    "data_mentah_bme688.csv": {
        "temperature_c": "temperature_c",
        "humidity_%": "humidity_pct",
        "pressure_hPa": "pressure_hpa",
        "gas_ohm": "bme_gas_ohm",
    },
    "data_co.csv": {"co_ppm": "co_ppm"},
    "data_co2.csv": {"co2_ppm": "co2_ppm"},
    "data_no2.csv": {"raw_adc": "no2_raw_adc", "voltage_V": "no2_raw_mv"},
    "data_INA226.csv": {
        "bus_voltage_v": "battery_voltage",
        "current_mA": "current_ma",
        "power_mW": "power_mw",
    },
    "data_pms_1.csv": {
        "pm1_0": "pm1_ug_m3",
        "pm2_5": "pm25_ug_m3",
        "pm10": "pm10_ug_m3",
    },
    # data_tof_1.csv -> reference only (hybrid camera), not RAB sensor
}

CANONICAL_KEYS = [
    "temperature_c", "humidity_pct", "pressure_hpa", "bme_gas_ohm",
    "co_ppm", "no2_raw_mv", "no2_ratio", "o3_ppm", "co2_ppm",
    "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "battery_voltage",
    "current_ma", "power_mw",
]

# compact_sensor.v3 uses short, frozen wire keys. Keep the adapter's internal
# names descriptive, then translate exactly once at the contract boundary.
COMPACT_SENSOR_KEYS = {
    "temperature_c": "tc", "humidity_pct": "h", "pressure_hpa": "p",
    "bme_gas_ohm": "bme", "co_ppm": "co", "no2_raw_mv": "n2mv",
    "no2_ratio": "n2r", "o3_ppm": "o3", "co2_ppm": "co2",
    "pm1_ug_m3": "pm1", "pm25_ug_m3": "pm25", "pm10_ug_m3": "pm10",
    "battery_voltage": "bv", "current_ma": "bi", "power_mw": "bp",
}

SENSOR_STATUS_KEYS = (
    "bme688", "sen0466", "sen0574", "sen0321", "mhz19", "pms7003t", "ina226",
)

ACTIVE_STATUS_BY_FILE = {
    "data_bme.csv": "bme688",
    "data_mentah_bme688.csv": "bme688",
    "data_co.csv": "sen0466",
    "data_co2.csv": "mhz19",
    "data_no2.csv": "sen0574",
    "data_INA226.csv": "ina226",
    "data_pms_1.csv": "pms7003t",
}

# NO2 mapping: raw ADC (0-4095 12-bit) is NOT calibrated ppm nor calibrated mV.
# Raw ADC has no compact_sensor.v3 field and is therefore omitted from the
# canonical payload. The voltage column is converted to raw millivolts in
# ``n2mv``; it is not a calibrated ppm claim.
NO2_ADC_KEY = "no2_raw_adc"
NO2_VOLT_KEY = "no2_voltage_v"


def detect_delimiter(sample: str) -> str:
    return ";" if sample.count(";") > sample.count(",") else ","


def read_csv_rows(path: Path):
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        sample = f.read(4000)
    delim = detect_delimiter(sample)
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delim)
        return [row for row in reader if row and any(v.strip() for v in row.values())]


def to_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "null", "NULL", "nan", "NaN"):
        return None
    try:
        value = float(s.replace(",", "."))
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def build_payloads(csv_dir: Path) -> list[dict]:
    payloads = []
    for filename, mapping in SENSOR_FIELD_MAP.items():
        path = csv_dir / filename
        if not path.exists():
            continue
        rows = read_csv_rows(path)
        ts_col = rows[0] and [k for k in rows[0].keys()][0]
        for i, row in enumerate(rows):
            n_ts = to_float(row.get(ts_col, ""))
            if n_ts is None:
                continue
            sensor = {k: None for k in CANONICAL_KEYS}
            for src_key, dst_key in mapping.items():
                val = to_float(row.get(src_key, ""))
                if val is None or dst_key == "no2_raw_adc":
                    continue
                if dst_key == "no2_raw_mv":
                    val *= 1000.0
                sensor[dst_key] = val
            active_sensor = ACTIVE_STATUS_BY_FILE[filename]
            hardware_status = {
                key: ("ok" if key == active_sensor else "missing")
                for key in SENSOR_STATUS_KEYS
            }
            flags = ["offline_csv_capture", "single_sensor_lane"]
            if filename == "data_co.csv":
                flags.append("co_constant_capture")
            if filename == "data_co2.csv" and sensor["co2_ppm"] == -1.0:
                flags.append("co2_error_read")
            if filename == "data_no2.csv":
                flags.append("no2_adc_not_mapped_to_mv")
            lane_boot_id = f"{BOOT_ID}-{Path(filename).stem}"
            payload = {
                "v": 3,
                "gw": GATEWAY_ID,
                "n": NODE_ID,
                "r": ROOM_ID,
                # Source timestamps are uptime milliseconds.  The receiver
                # supplies gateway receive time out-of-band; never synthesize
                # a wall-clock value here and label it as node time.
                "ts": n_ts / 1000.0,
                "tb": "uptime_s",
                "seq": i + 1,
                "bid": lane_boot_id,
                "pp": "hardware_only",
                "fw": "hardprog_csv_2026-08-28",
                "cfg": "csv-raw",
                "cal": "uncalibrated-csv-raw",
                "hs": "partial",
                "f": flags,
                "ok": hardware_status,
                "s": {COMPACT_SENSOR_KEYS[key]: value for key, value in sensor.items()},
            }
            payloads.append(payload)
    return payloads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    payloads = build_payloads(Path(args.csv_dir))
    with open(args.output, "w", encoding="utf-8") as f:
        for p in payloads:
            f.write(json.dumps(p) + "\n")
    print(f"Wrote {len(payloads)} payloads -> {args.output}")


if __name__ == "__main__":
    main()
