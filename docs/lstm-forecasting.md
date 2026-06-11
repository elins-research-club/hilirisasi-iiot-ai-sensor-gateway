# LSTM Forecasting Multi-Target

Dokumen ini menjelaskan tahap model awal setelah pipeline pre-model menghasilkan
window time-series.

## Tujuan

LSTM forecasting v1 memprediksi sensor utama beberapa menit ke depan. Model ini
belum membuat keputusan `normal`, `warning`, atau `critical` secara penuh.
Status prescriptive akan dibuat di tahap berikutnya dari hasil forecast dan rule
atau decision layer.

## Input dan Target

Input model berasal dari:

```text
data/processed/lstm_windows.jsonl
```

Shape input:

```text
[samples, timesteps, features]
```

Pada konfigurasi sekarang:

```text
[34536, 12, 16]
```

Target prediksi v1 hanya fitur sensor utama:

```text
temperature_c
humidity_pct
pressure_hpa
bme_gas_raw
co_raw
```

Fitur turunan seperti delta, rolling mean, missing count, valid ratio, dan
sequence gap tetap dipakai sebagai input, tetapi tidak menjadi target output.

## Horizon Prediksi

Default `horizon_steps` adalah 5. Karena pipeline melakukan resampling 60 detik,
model memprediksi nilai sensor sekitar 5 menit setelah akhir input window.

Contoh:

```text
input: 12 timestep terakhir
output: 5 sensor utama pada 5 timestep berikutnya
```

## Workflow Command

Siapkan dataset forecasting:

```powershell
py -3.13 run_gateway.py prepare-forecast-dataset --windows data/processed/lstm_windows.jsonl --output-npz data/modeling/lstm_forecast_dataset.npz --output-meta data/modeling/lstm_forecast_dataset_meta.json --horizon-steps 5
```

Train model:

```powershell
py -3.13 run_gateway.py train-lstm-forecast --dataset data/modeling/lstm_forecast_dataset.npz --output-dir models/lstm_forecast/latest --epochs 30 --batch-size 64 --hidden-size 64 --device auto
```

Evaluasi model:

```powershell
py -3.13 run_gateway.py evaluate-lstm-forecast --dataset data/modeling/lstm_forecast_dataset.npz --model models/lstm_forecast/latest/model.pt
```

Prediksi dari window:

```powershell
py -3.13 run_gateway.py predict-lstm-forecast --windows data/processed/lstm_windows.jsonl --model models/lstm_forecast/latest/model.pt --output models/lstm_forecast/latest/predictions.jsonl --max-windows 10
```

Jalankan beberapa eksperimen sekaligus:

```powershell
py -3.13 run_gateway.py run-forecast-experiments --windows data/processed/lstm_windows.jsonl --output-dir models/forecast_experiments/latest --horizons 5,15,30 --hidden-sizes 32,64 --epochs 30 --batch-size 64 --device auto
```

## Output Lokal

Artifact training disimpan lokal dan tidak masuk Git:

```text
data/modeling/lstm_forecast_dataset.npz
data/modeling/lstm_forecast_dataset_meta.json
models/lstm_forecast/latest/model.pt
models/lstm_forecast/latest/training.json
models/lstm_forecast/latest/metrics.json
models/lstm_forecast/latest/predictions.jsonl
models/forecast_experiments/latest/summary.json
models/forecast_experiments/latest/summary.csv
models/forecast_experiments/latest/runs/<run_id>/
```

## Evaluasi

Evaluator menghasilkan:

- MAE/RMSE per target sensor;
- overall MAE/RMSE;
- pembanding `last_value_baseline`;
- delta LSTM terhadap baseline;
- NaN/Inf check;
- `data_status`: `PASS` atau `FAIL`;
- `baseline_comparison_status`: `BEATS_BASELINE`, `MIXED`, atau `UNDER_BASELINE`;
- `model_readiness`: `PROMISING`, `EXPERIMENTAL`, atau `NOT_READY`.

Baseline penting karena model LSTM harus dibandingkan dengan prediksi sederhana:
nilai masa depan dianggap sama dengan nilai sensor terakhir di input window.
Jika LSTM kalah dari baseline, itu bukan berarti pipeline rusak. Artinya model
masih tahap eksperimen dan perlu tuning, data real, horizon berbeda, atau fitur
tambahan sebelum dipakai sebagai dasar keputusan.

## Output Prediksi

`predict-lstm-forecast` menulis dua bentuk output:

- `prediction_normalized`: nilai model dalam skala normalisasi 0-1;
- `prediction_values`: nilai hasil denormalisasi memakai range
  `config/default.toml`, misalnya Celsius, persen humidity, hPa, dan raw gas.

Nilai denormalized memudahkan debugging dan presentasi, tetapi tetap perlu
dibaca bersama batasan dataset publik/derived.

## Experiment Runner

`run-forecast-experiments` membuat dataset forecasting per horizon, melatih LSTM,
mengevaluasi LSTM vs baseline, lalu menulis ringkasan JSON/CSV. Default horizon
adalah `5,15,30` step. Karena resampling pipeline adalah 60 detik, angka ini
dapat dibaca sebagai sekitar 5, 15, dan 30 menit setelah akhir window input.

Untuk smoke test cepat, gunakan konfigurasi kecil:

```powershell
py -3.13 run_gateway.py run-forecast-experiments --windows data/processed/lstm_windows.jsonl --output-dir models/forecast_experiments/smoke --horizons 5 --hidden-sizes 32 --epochs 2 --batch-size 64 --device cpu
```

## Batasan

- Dataset awal masih berasal dari dataset publik/simulasi.
- `pressure_hpa` pada workflow Gary derived adalah synthetic realistis.
- `bme_gas_raw` masih proxy dari LPG/smoke, bukan pembacaan BME688 asli.
- Model v1 memprediksi nilai sensor, belum membuat status prescriptive final.
- Experiment runner sekarang belum rebuild window size 24/36; window masih
  mengikuti `data/processed/lstm_windows.jsonl` yang sudah dibuat.
- Validasi final tetap membutuhkan data real dari BME688/BME668 dan SEN0377.
