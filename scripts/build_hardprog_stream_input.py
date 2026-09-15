#!/usr/bin/env python3
"""Build stream-detect input JSONL from hardprog per-sensor CSVs.

Writes one JSONL per sensor lane with records containing the raw node clock:
  {"node_timestamp_ms": uptime_ms, "timestamp_basis": "uptime_ms", "features": {...}}

No RFC3339 timestamp is fabricated from uptime. The runtime detector adds a
gateway receive timestamp when it processes the replay.

Usage: python3 scripts/build_hardprog_stream_input.py <csv-dir> <out-dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def to_float(v):
    try:
        value = float(str(v).replace(",", "."))
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--max-rows", type=int, default=20000)
    args = ap.parse_args()
    csv_dir = Path(args.csv_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    lanes = [
        ("data_bme.csv", ";", ["temperature_c", "humidity_rh", "pressure_hpa", "gas_ohm"], {}),
        ("data_mentah_bme688.csv", ";", ["temperature_c", "humidity_%", "pressure_hPa", "gas_ohm"], {}),
        ("data_co2.csv", ";", ["co2_ppm"], {"co2_ppm": (-1.0,)}),
        ("data_no2.csv", ";", ["raw_adc", "voltage_V"], {}),
        ("data_pms_1.csv", ",", ["pm1_0", "pm2_5", "pm10"], {}),
        ("data_INA226.csv", ";", ["bus_voltage_v", "current_mA"], {}),
        # ToF is a hybrid-camera reference lane, not a node-sensor stream.
    ]
    for fname, delim, cols, invalid_values in lanes:
        src = csv_dir / fname
        if not src.exists():
            print(f"SKIP {fname}")
            continue
        lane = fname.replace(".csv", "")
        out = out_dir / f"{lane}_stream.jsonl"
        n = 0
        with src.open(encoding="utf-8-sig") as f, out.open("w", encoding="utf-8") as w:
            reader = csv.DictReader(f, delimiter=delim)
            for row in reader:
                if n >= args.max_rows:
                    break
                # Source timestamps are node uptime milliseconds, not epoch
                # time. Preserve them explicitly; do not fabricate a 1970
                # RFC3339 timestamp that downstream code could mistake for
                # wall-clock event time.
                first_key = list(row.keys())[0]
                ts_ms = to_float(row.get(first_key))
                features = {}
                invalid_fields = []
                for c in cols:
                    v = to_float(row.get(c))
                    if v is not None:
                        invalid = any(abs(v - marker) < 1e-9 for marker in invalid_values.get(c, ()))
                        if invalid:
                            invalid_fields.append(c)
                        else:
                            features[c] = v
                rec = {"features": features}
                if ts_ms is not None:
                    rec["node_timestamp_ms"] = ts_ms
                    rec["timestamp_basis"] = "uptime_ms"
                if invalid_fields:
                    rec["invalid_fields"] = invalid_fields
                w.write(json.dumps(rec, separators=(",", ":")) + "\n")
                n += 1
        print(f"{fname}: {n} rows -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
