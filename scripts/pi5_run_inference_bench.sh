#!/usr/bin/env bash
# Benchmark inference latency for every Pi 5 trained model.
# Usage: bash scripts/pi5_run_inference_bench.sh <models_root>
set -euo pipefail

ROOT="${1:-$HOME/iiot-ai-sensor-gateway/models/pi5}"
REPO="$HOME/iiot-ai-sensor-gateway"
DATA="$REPO/data/real_offline/hardprog_csv_2026-08-28/forecast"
PY="$REPO/.venv/bin/python"
export PYTHONPATH="$REPO/src"

LANES=(data_bme data_mentah_bme688 data_co2 data_no2 data_pms_1 data_INA226)

for lane in "${LANES[@]}"; do
  ds="$DATA/${lane}_dataset.npz"
  [ -f "$ds" ] || { echo "SKIP $lane (no dataset)"; continue; }
  for tr in "$ROOT/$lane"/*/model.pt; do
    [ -f "$tr" ] || continue
    m=$(basename "$(dirname "$tr")")
    out="$ROOT/${lane}/${m}/benchmark.json"
    if [ -f "$out" ]; then
      echo "SKIP $lane/$m (benchmark exists)"
      continue
    fi
    case "$m" in
      lstm_residual) kind="lstm" ;;
      *) kind="edge" ;;
    esac
    echo "===== BENCH $lane/$m ($kind) ====="
    "$PY" "$REPO/scripts/benchmark_inference.py" \
      --dataset "$ds" --model "$tr" --model-kind "$kind" \
      --iterations 200 --warmup 10 --device cpu --output "$out"
  done
done

echo "ALL DONE"
