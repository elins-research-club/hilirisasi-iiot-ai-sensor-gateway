#!/usr/bin/env bash
# Normalize + run stream-detect (native) for every hardprog lane on Pi 5.
# Usage: bash scripts/pi5_run_stream_detect_norm.sh
set -euo pipefail

REPO="$HOME/iiot-ai-sensor-gateway"
STREAM="$REPO/data/real_offline/hardprog_csv_2026-08-28/stream"
PY="$REPO/.venv/bin/python"
export PYTHONPATH="$REPO/src"
mkdir -p "$STREAM/pi5"

declare -A LANES=(
  [data_bme]="temperature_c,humidity_rh,pressure_hpa,gas_ohm"
  [data_mentah_bme688]="temperature_c,humidity_%,pressure_hPa,gas_ohm"
  [data_co2]="co2_ppm"
  [data_no2]="raw_adc,voltage_V"
  [data_pms_1]="pm1_0,pm2_5,pm10"
  [data_INA226]="bus_voltage_v,current_mA"
)

for lane in "${!LANES[@]}"; do
  feats="${LANES[$lane]}"
  raw="$STREAM/${lane}_stream.jsonl"
  [ -f "$raw" ] || { echo "SKIP $lane"; continue; }
  norm="$STREAM/${lane}_norm_stream.jsonl"
  out="$STREAM/pi5/${lane}_det.jsonl"
  echo "===== NORMALIZE $lane ====="
  "$PY" "$REPO/scripts/normalize_stream_input.py" "$raw" "$norm"
  echo "===== STREAM native $lane ====="
  "$PY" "$REPO/run_gateway.py" stream-detect \
    --input "$norm" --output "$out" --backend native \
    --feature-names "$feats" --warmup-samples 64 --anomaly-threshold 0.7 \
    --z-scale 3.0 --page-hinkley-delta 0.005 --page-hinkley-threshold 0.25
done

echo "ALL DONE"
