#!/usr/bin/env bash
# Thin Linux/WSL wrapper around the cross-platform Python state-machine runner.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-python3}"
DEVICE="${DEVICE:-auto}"
EPOCHS="${EPOCHS:-80}"
LSTM_EPOCHS="${LSTM_EPOCHS:-40}"
PATIENCE="${PATIENCE:-10}"
BATCH="${BATCH:-256}"
HORIZON="${HORIZON:-5}"
PURGE="${PURGE:-5}"
SEEDS="${SEEDS:-42,43,44}"
LANES="${LANES:-gary,uci,fidas,sim}"
EXTRA_ARGS=()
[[ "${SKIP_DOWNLOAD:-0}" == "1" ]] && EXTRA_ARGS+=(--skip-download)
[[ "${SKIP_PUBLIC_PREPARE:-0}" == "1" ]] && EXTRA_ARGS+=(--skip-public-prepare)
[[ "${SKIP_LSTM:-0}" == "1" ]] && EXTRA_ARGS+=(--skip-lstm)
[[ "${FORCE:-0}" == "1" ]] && EXTRA_ARGS+=(--force)
[[ "${DRY_RUN:-0}" == "1" ]] && EXTRA_ARGS+=(--dry-run)
exec "$PY" scripts/laptop_bakeoff_runner.py \
  --python "$PY" \
  --device "$DEVICE" \
  --epochs "$EPOCHS" \
  --lstm-epochs "$LSTM_EPOCHS" \
  --patience "$PATIENCE" \
  --batch-size "$BATCH" \
  --horizon "$HORIZON" \
  --purge-gap "$PURGE" \
  --seeds "$SEEDS" \
  --lanes "$LANES" \
  "${EXTRA_ARGS[@]}" \
  "$@"
