# LSTM Forecasting

LSTM tetap menjadi kandidat forecast **eksperimental**, bukan default production.

## Targets

```text
temperature_c
humidity_pct
pressure_hpa
co_ppm
o3_ppm
co2_ppm
pm25_ug_m3
```

NO₂ tidak menjadi target ppm. Missing target harus ditangani pada dataset preparation; proxy legacy tidak boleh menggantikan field RAB.

## Workflow

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY run_gateway.py prepare-forecast-dataset \
  --windows data/processed/windows.jsonl \
  --output-npz data/modeling/lstm_forecast_dataset.npz \
  --output-meta data/modeling/lstm_forecast_dataset_meta.json \
  --horizon-steps 5

$PY run_gateway.py train-lstm-forecast \
  --dataset data/modeling/lstm_forecast_dataset.npz \
  --output-dir models/lstm_forecast/latest \
  --epochs 30 --batch-size 64 --hidden-size 64 --device auto

$PY run_gateway.py evaluate-lstm-forecast \
  --dataset data/modeling/lstm_forecast_dataset.npz \
  --model models/lstm_forecast/latest/model.pt \
  --eval-batch-size 1024
```

## Safe Checkpoint

Loader memakai:

- `weights_only=True`;
- required-key validation;
- state-dict mapping check;
- strict load;
- fail-closed pada checkpoint invalid.

## Evaluation

- time-ordered train/validation/test;
- normalizer fit hanya pada train;
- LastValue/SeasonalNaive comparison;
- MAE/RMSE/MASE dan per-target metrics;
- denormalized metrics;
- model tetap experimental bila kalah baseline.

## Experiment Grid

```bash
$PY run_gateway.py run-forecast-experiments \
  --windows data/processed/windows.jsonl \
  --output-dir models/forecast_experiments/latest \
  --horizons 5,15,30 \
  --window-sizes 12,24,36 \
  --hidden-sizes 32,64 \
  --epochs 30 --batch-size 64 --device auto
```

Model selector memakai canonical target weights. Selection pada reference/synthetic lane tidak membuat model production-ready.

## Baseline Gate

Bandingkan LSTM dengan:

- LastValue;
- SeasonalNaive;
- DLinear;
- FITS-inspired.

LSTM dipertahankan hanya bila memberi improvement yang konsisten pada data real, lintas target/device/site, dengan resource budget yang dapat diterima.

## Batasan

- belum ada training panjang pada full RAB real data;
- belum ada Pi benchmark;
- belum ada false-alert/day;
- output forecast belum boleh dianggap safety control;
- threshold decision default hanya commissioning defaults.
