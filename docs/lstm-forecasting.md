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

## Catatan Fitur Gas

Fitur gas utama diambil dengan prioritas `voc_raw`, `bme_gas_raw`, `co_raw`,
lalu `gas_raw`. Nilai `0.0` diperlakukan sebagai nilai valid, bukan otomatis
missing. Ini penting karena pembacaan sensor atau hasil normalisasi bisa bernilai
nol dan tidak boleh membuat pipeline pindah ke sensor gas lain secara diam-diam.

Range normalisasi `gas_delta`, `gas_mean_3`, dan `gas_std_3` mengikuti skala
gas raw/BME-style, bukan skala CO kecil. Alasannya, pada workflow project schema
sinyal gas dominan berasal dari `bme_gas_raw` proxy LPG/smoke.

## Horizon Prediksi

Default `horizon_steps` adalah 5. Karena pipeline melakukan resampling 60 detik,
model memprediksi nilai sensor sekitar 5 menit setelah akhir input window.

Contoh:

```text
input: 12 timestep terakhir
output: 5 sensor utama pada 5 timestep berikutnya
```

## Temporal Split

Dataset forecasting memakai split temporal per node, bukan random split. Builder
X/y membuat sample dari window input dan label pada horizon berikutnya, lalu
memberi purge gap antar train/validation/test. Default purge gap sama dengan
`horizon_steps`.

Metadata `split_time_range` disimpan di file meta dataset untuk membuktikan
bahwa rentang input/label antar split tidak overlap. Jika data terlalu pendek
untuk split non-overlap, command gagal dengan error eksplisit.

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
py -3.13 run_gateway.py evaluate-lstm-forecast --dataset data/modeling/lstm_forecast_dataset.npz --model models/lstm_forecast/latest/model.pt --eval-batch-size 1024
```

Prediksi dari window:

```powershell
py -3.13 run_gateway.py predict-lstm-forecast --windows data/processed/lstm_windows.jsonl --model models/lstm_forecast/latest/model.pt --output models/lstm_forecast/latest/predictions.jsonl --max-windows 10
```

Buat payload forecast v1 untuk tahap decision layer berikutnya:

```powershell
py -3.13 run_gateway.py build-forecast-payload-v1 --predictions models/lstm_forecast/latest/predictions.jsonl --metrics models/lstm_forecast/latest/metrics.json --output models/lstm_forecast/latest/forecast_payloads.jsonl
```

Jalankan beberapa eksperimen sekaligus:

```powershell
py -3.13 run_gateway.py run-forecast-experiments --windows data/processed/lstm_windows.jsonl --output-dir models/forecast_experiments/latest --horizons 5,15,30 --window-sizes 12,24,36 --hidden-sizes 32,64 --epochs 30 --batch-size 64 --device auto --eval-batch-size 1024
```

Untuk GPU CUDA dengan VRAM terbatas, `--eval-batch-size` penting karena training sudah berjalan per batch, tetapi evaluasi juga harus diproses bertahap agar tidak memindahkan seluruh split dataset ke GPU sekaligus. Jika masih terjadi CUDA out-of-memory, turunkan nilai ini, misalnya `512` atau `256`.
Di Windows/WDDM, tidak perlu mengatur `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` karena opsi itu dapat memunculkan warning PyTorch dan tidak diperlukan setelah evaluasi dibuat batch-based.

## Output Lokal

Artifact training disimpan lokal dan tidak masuk Git:

```text
data/modeling/lstm_forecast_dataset.npz
data/modeling/lstm_forecast_dataset_meta.json
models/lstm_forecast/latest/model.pt
models/lstm_forecast/latest/training.json
models/lstm_forecast/latest/metrics.json
models/lstm_forecast/latest/predictions.jsonl
models/lstm_forecast/latest/forecast_payloads.jsonl
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
- denormalized MAE/RMSE dalam satuan asli target sensor;
- `rmse_skill_score`, yaitu `1 - (lstm_rmse / baseline_rmse)`;
- NaN/Inf check;
- `data_status`: `PASS` atau `FAIL`;
- `baseline_comparison_status`: `BEATS_BASELINE`, `MIXED`, atau `UNDER_BASELINE`;
- `model_readiness`: `PROMISING`, `EXPERIMENTAL`, atau `NOT_READY`.

Baseline penting karena model LSTM harus dibandingkan dengan prediksi sederhana:
nilai masa depan dianggap sama dengan nilai sensor terakhir di input window.
Jika LSTM kalah dari baseline, itu bukan berarti pipeline rusak. Artinya model
masih tahap eksperimen dan perlu tuning, data real, horizon berbeda, atau fitur
tambahan sebelum dipakai sebagai dasar keputusan.

Skill score positif berarti LSTM lebih baik dari baseline untuk RMSE. Skill
score negatif berarti baseline masih lebih baik. Untuk laporan awal, baca metrik
per target karena gas/CO bisa punya perilaku berbeda dari temperature/humidity.

## Output Prediksi

`predict-lstm-forecast` menulis dua bentuk output:

- `prediction_normalized`: nilai model dalam skala normalisasi 0-1;
- `prediction_values`: nilai hasil denormalisasi memakai range
  `config/default.toml`, misalnya Celsius, persen humidity, hPa, dan raw gas.

Nilai denormalized memudahkan debugging dan presentasi, tetapi tetap perlu
dibaca bersama batasan dataset publik/derived.

## Forecast Payload v1

`build-forecast-payload-v1` mengubah output prediksi menjadi JSONL yang rapi
untuk tahap decision layer berikutnya. Payload ini belum alert final dan belum
berisi rekomendasi prescriptive.

Field utama:

- `schema`: selalu `iiot.ai_sensor.forecast.v1`;
- `gateway_id`, `node_id`, `room_id`;
- `input_start_timestamp`, `input_end_timestamp`;
- `forecast_horizon_steps`, `forecast_horizon_minutes`;
- `predicted_sensor`: nilai prediksi dalam satuan asli;
- `model_version`, `metrics_ref`, `model_readiness`.

Setelah payload forecast terbentuk, decision layer lokal v1 dapat dibuat dengan:

```powershell
py -3.13 run_gateway.py build-forecast-decision-v1 --forecast-payloads models/lstm_forecast/latest/forecast_payloads.jsonl --output models/lstm_forecast/latest/decision_payloads.jsonl
```

Decision layer ini rule-based, mengevaluasi semua target sensor utama, dan
menghasilkan status `normal`, `warning`, atau `critical`. Detail rule ada di
`docs/forecast-decision-layer.md`.

## Experiment Runner

`run-forecast-experiments` membuat dataset forecasting per horizon/window size,
melatih LSTM, mengevaluasi LSTM vs baseline, lalu menulis ringkasan JSON/CSV.
Default horizon adalah `5,15,30` step. Karena resampling pipeline adalah 60
detik, angka ini dapat dibaca sebagai sekitar 5, 15, dan 30 menit setelah akhir
window input.

Window size `12/24/36` dibangun ulang dari timeline existing
`lstm_windows.jsonl`. Ini realistis untuk tahap sekarang karena tidak perlu
rerun preprocessing besar, tetapi tetap mengasumsikan window input berasal dari
timeline regular per node.

Untuk smoke test cepat, gunakan konfigurasi kecil:

```powershell
py -3.13 run_gateway.py run-forecast-experiments --windows data/processed/lstm_windows.jsonl --output-dir models/forecast_experiments/smoke --horizons 5 --window-sizes 12 --hidden-sizes 32 --epochs 2 --batch-size 64 --device cpu
```

## Model Selection

`select-best-forecast-model` membaca `summary.csv` dari experiment runner dan
memilih kandidat model sementara:

```powershell
py -3.13 run_gateway.py select-best-forecast-model --summary models/forecast_experiments/latest/summary.csv --output models/forecast_experiments/latest/best_model_selection.json
```

Selector memakai skill score semua target utama: `temperature_c`,
`humidity_pct`, `pressure_hpa`, `bme_gas_raw`, dan `co_raw`. CO dan gas tetap
punya bobot penting karena paling dekat dengan risiko udara, tetapi target lain
tetap ikut dihitung.

Jika overall, `co_raw`, dan `bme_gas_raw` masih kalah baseline, selector tetap
memilih kandidat dengan LSTM RMSE terendah dan memberi status
`NEEDS_TUNING`. Status ini berarti kandidat berguna untuk eksperimen berikutnya,
bukan model final.

Ringkasan eksperimen CUDA terakhir dan interpretasinya dicatat di
`docs/progress-lstm-forecasting-v1.md`.

## Batasan

- Dataset awal masih berasal dari dataset publik/simulasi.
- `pressure_hpa` pada workflow Gary derived adalah synthetic realistis.
- `bme_gas_raw` masih proxy dari LPG/smoke, bukan pembacaan BME688 asli.
- Model v1 memprediksi nilai sensor, belum membuat status prescriptive final.
- Window size tambahan dibangun dari timeline existing, bukan dari receiver
  LoRa realtime. Validasi hardware tetap perlu dilakukan setelah data sensor
  real tersedia.
- Validasi final tetap membutuhkan data real dari BME688/BME668 dan SEN0377.
