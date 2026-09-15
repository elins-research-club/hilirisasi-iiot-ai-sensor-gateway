#!/usr/bin/env bash
# Pi 5 benchmark for bakeoff datasets (fidas, gary, sim, smoke, uci).
# Trains + evals + benchmarks inference for each bakeoff lane x model.
# Usage: bash scripts/pi5_run_bakeoff.sh <models_root>
set -euo pipefail

ROOT="${1:-$HOME/iiot-ai-sensor-gateway/models/pi5_bakeoff}"
REPO="$HOME/iiot-ai-sensor-gateway"
DATA="$REPO/data/bakeoff"
PY="$REPO/.venv/bin/python"
export PYTHONPATH="$REPO/src"
mkdir -p "$ROOT"

LANES=(fidas gary sim smoke uci)
EDGE_MODELS=(fits dlinear)

for lane in "${LANES[@]}"; do
  ds="$DATA/${lane}/forecast.npz"
  [ -f "$ds" ] || { echo "SKIP $lane (no dataset)"; continue; }
  for m in "${EDGE_MODELS[@]}"; do
    out="$ROOT/${lane}/${m}"
    if [ -f "$out/training.json" ]; then
      echo "SKIP $lane/$m (trained)"
    else
      echo "===== TRAIN edge $lane/$m ====="
      "$PY" "$REPO/run_gateway.py" train-edge-forecast \
        --dataset "$ds" --output-dir "$out" --model-type "$m" \
        --epochs 40 --seed 42 --device cpu
      echo "===== EVAL edge $lane/$m ====="
      "$PY" "$REPO/run_gateway.py" evaluate-edge-forecast \
        --dataset "$ds" --model "$out/model.pt" --output-dir "$out/eval_results" --device cpu
    fi
    # inference bench
    if [ ! -f "$out/benchmark.json" ]; then
      echo "===== BENCH edge $lane/$m ====="
      "$PY" "$REPO/scripts/benchmark_inference.py" \
        --dataset "$ds" --model "$out/model.pt" --model-kind edge \
        --iterations 200 --warmup 10 --device cpu --output "$out/benchmark.json"
    fi
  done
  # LSTM residual (may be heavy on big data; still benchmark it)
  out="$ROOT/${lane}/lstm_residual"
  if [ -f "$out/training.json" ]; then
    echo "SKIP $lane/lstm_residual (trained)"
  else
    echo "===== TRAIN LSTM residual $lane ====="
    "$PY" "$REPO/run_gateway.py" train-lstm-forecast \
      --dataset "$ds" --output-dir "$out" --epochs 40 --seed 42 --device cpu \
      --hidden-size 32 --num-layers 1 --forecast-strategy residual
    echo "===== EVAL LSTM residual $lane ====="
    "$PY" "$REPO/run_gateway.py" evaluate-lstm-forecast \
      --dataset "$ds" --model "$out/model.pt" --output-dir "$out/eval_results" --device cpu
  fi
  if [ ! -f "$out/benchmark.json" ]; then
    echo "===== BENCH LSTM $lane ====="
    "$PY" "$REPO/scripts/benchmark_inference.py" \
      --dataset "$ds" --model "$out/model.pt" --model-kind lstm \
      --iterations 200 --warmup 10 --device cpu --output "$out/benchmark.json"
  fi
done

echo "ALL DONE"
