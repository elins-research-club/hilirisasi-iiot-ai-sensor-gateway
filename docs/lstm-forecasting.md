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

## Output Lokal

Artifact training disimpan lokal dan tidak masuk Git:

```text
data/modeling/lstm_forecast_dataset.npz
data/modeling/lstm_forecast_dataset_meta.json
models/lstm_forecast/latest/model.pt
models/lstm_forecast/latest/training.json
models/lstm_forecast/latest/metrics.json
models/lstm_forecast/latest/predictions.jsonl
```

## Evaluasi

Evaluator menghasilkan:

- MAE/RMSE per target sensor;
- overall MAE/RMSE;
- pembanding `last_value_baseline`;
- NaN/Inf check;
- status `PASS` atau `FAIL`.

Baseline penting karena model LSTM harus dibandingkan dengan prediksi sederhana:
nilai masa depan dianggap sama dengan nilai sensor terakhir di input window.

## Batasan

- Dataset awal masih berasal dari dataset publik/simulasi.
- `pressure_hpa` pada workflow Gary derived adalah synthetic realistis.
- `bme_gas_raw` masih proxy dari LPG/smoke, bukan pembacaan BME688 asli.
- Model v1 memprediksi nilai sensor, belum membuat status prescriptive final.
- Validasi final tetap membutuhkan data real dari BME688/BME668 dan SEN0377.