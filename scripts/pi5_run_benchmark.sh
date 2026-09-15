#!/usr/bin/env bash
# Run the full Pi 5 forecast benchmark: train + eval for every lane x model.
# Usage: bash scripts/pi5_run_benchmark.sh <output_root>
set -euo pipefail

OUT_ROOT="${1:-$HOME/iiot-ai-sensor-gateway/models/pi5}"
REPO="$HOME/iiot-ai-sensor-gateway"
DATA="$REPO/data/real_offline/hardprog_csv_2026-08-28/forecast"
PY="$REPO/.venv/bin/python"
export PYTHONPATH="$REPO/src"
mkdir -p "$OUT_ROOT"

LANES=(data_bme data_mentah_bme688 data_co2 data_no2 data_pms_1 data_INA226)
EDGE_MODELS=(fits fits_official dlinear)

run() {
  echo "### $*"
  "$@"
}

for lane in "${LANES[@]}"; do
  ds="$DATA/${lane}_dataset.npz"
  [ -f "$ds" ] || { echo "SKIP $lane (no dataset)"; continue; }
  for m in "${EDGE_MODELS[@]}"; do
    out="$OUT_ROOT/${lane}/${m}"
    if [ -f "$out/training.json" ]; then
      echo "SKIP $lane/$m (already trained)"
      continue
    fi
    echo "===== TRAIN edge $lane/$m ====="
    run "$PY" "$REPO/run_gateway.py" train-edge-forecast \
      --dataset "$ds" --output-dir "$out" --model-type "$m" \
      --epochs 40 --seed 42 --device cpu
    echo "===== EVAL edge $lane/$m ====="
    run "$PY" "$REPO/run_gateway.py" evaluate-edge-forecast \
      --dataset "$ds" --model "$out/model.pt" --output-dir "$out/eval_results" --device cpu
  done
  # LSTM residual comparator
  out="$OUT_ROOT/${lane}/lstm_residual"
  if [ -f "$out/training.json" ]; then
    echo "SKIP $lane/lstm_residual (already trained)"
  else
    echo "===== TRAIN LSTM residual $lane ====="
    run "$PY" "$REPO/run_gateway.py" train-lstm-forecast \
      --dataset "$ds" --output-dir "$out" --epochs 40 --seed 42 --device cpu \
      --hidden-size 32 --num-layers 1 --forecast-strategy residual
    echo "===== EVAL LSTM residual $lane ====="
    run "$PY" "$REPO/run_gateway.py" evaluate-lstm-forecast \
      --dataset "$ds" --model "$out/model.pt" --output-dir "$out/eval_results" --device cpu
  fi
done

echo "ALL DONE"
