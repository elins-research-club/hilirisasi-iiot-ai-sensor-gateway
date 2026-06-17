# IIoT AI Sensor Gateway

Repo ini berisi dua bagian yang saling tersambung untuk Industrial Environment Monitoring berbasis IIoT:

- firmware ESP32-C6 sensor node untuk preprocessing ringan dan payload LoRa;
- pipeline Python Raspberry Pi untuk preprocessing AI/pre-model, dataset publik, evaluasi, window LSTM-ready, dan LSTM forecasting awal.

Python di repo ini tidak di-upload ke ESP32-C6. Firmware ESP32-C6 ditulis sebagai C++ skeleton di folder `firmware/esp32-c6-sensor-node`.

## Arsitektur

```text
BME688/BME668 + SEN0377
-> ESP32-C6 firmware preprocessing ringan
-> compact payload via LoRa/Ebyte E32
-> Raspberry Pi gateway Python pipeline
-> validation lanjutan, resampling, feature extraction, normalization, windowing
-> LSTM-ready dataset -> LSTM forecasting multi-target atau AI inference
```

## Scope

ESP32-C6 firmware:

- sensor read;
- range check kasar;
- missing/sensor error flag;
- moving average ringan;
- sequence number;
- compact payload JSON;
- kirim payload ke LoRa/Ebyte E32.

Raspberry Pi Python pipeline:

- convert dataset publik ke canonical JSONL;
- parse payload ESP32/dataset;
- validasi lanjutan;
- logging dan grouping per node;
- resampling;
- feature extraction;
- normalisasi;
- windowing `[samples, timesteps, features]`.

Non-scope repo ini:

- backend FastAPI, Redis, TimescaleDB, Next.js, EMQX, atau MQTT broker;
- computer vision;
- OpenClaw runtime;
- model AI/prescriptive final penuh; LSTM forecasting v1 hanya model awal;
- deployment produksi final.

## Sensor Target Project

- BME688/BME668: temperature, humidity, pressure, dan BME gas raw/gas resistance style signal.
- SEN0377: CO/gas tambahan.

## Simulation, Real Offline File, and Real Live Pipeline

Repo ini memisahkan tiga jalur data:

- Simulation/reference pipeline: dataset publik atau dummy dipakai untuk proof of
  concept, eksperimen preprocessing, forecasting, evaluator, model selector, dan
  decision layer awal. Workflow Gary berada di jalur ini.
- Real offline file pipeline: data mentah dari ESP32-C6/Raspberry Pi disimpan
  dulu sebagai file raw JSONL/CSV, lalu diproses untuk training, evaluation, dan
  model selection. Jalur ini adalah cara paling aman untuk memakai data sensor
  sendiri sebelum receiver live matang.
- Real live receiver pipeline: Raspberry Pi membaca payload dari LoRa/serial
  untuk inference dan decision lokal. Receiver live penuh belum diimplementasikan
  dan menjadi handoff integrasi hardware.

Semua jalur harus bertemu di canonical schema sebelum memakai shared core logic:

```text
simulation adapter     -> canonical schema
real file adapter      -> canonical schema
real stream receiver   -> canonical schema
canonical schema       -> preprocessing -> windowing -> model/decision
```

Data/model/artifact simulasi dan real harus tetap dipisah. `data/`, `models/`,
`*.pt`, dan `*.npz` tetap ignored dari Git. Detail boundary ada di
`docs/data-source-boundary.md`, `docs/shared-canonical-schema.md`,
`docs/real-offline-file-pipeline.md`, `docs/real-live-receiver-pipeline.md`, dan
`docs/real-receiver-contract.md`.

## Folder Utama

```text
firmware/esp32-c6-sensor-node/  C++ skeleton firmware ESP32-C6
src/iiot_ai_sensor_gateway/     Python pipeline Raspberry Pi/dataset
config/                         konfigurasi pipeline Python
docs/                           dokumentasi teknis dan laporan progres
tests/                          unittest pipeline Python
systemd/                        contoh service Raspberry Pi
```

## Firmware ESP32-C6

```powershell
cd firmware/esp32-c6-sensor-node
pio run
pio run -t upload
pio device monitor
```

Default firmware memakai `IIOT_USE_MOCK_SENSORS=1` di `platformio.ini`, sehingga modul bisa dibaca/dibuild tanpa wiring sensor real. Untuk hardware real, ubah ke `0`, lengkapi BME688/BME668 library di `src/sensors.cpp`, dan kalibrasi SEN0377.

## Gary Stafford Project Schema Workflow

Dataset Gary dipakai sebagai uji awal pipeline karena punya temperature, humidity, CO, LPG, dan smoke. `co` dipakai sebagai SEN0377-like feature; `lpg/smoke` dipakai sebagai dasar `bme_gas_raw` turunan. Gary asli tidak punya pressure, jadi repo membuat dataset turunan project-like agar kolomnya sama dengan target sensor.

Jalur yang paling mudah dipahami sekarang:

```text
iot_telemetry_data.csv
-> derive project sensor schema dataset
-> compact payload JSONL project-like
-> Raspberry Pi Python pre-model pipeline
-> window LSTM-ready
```

```powershell
py -3.13 run_gateway.py derive-gary-schema --input-csv iot_telemetry_data.csv --output-csv data/derived/gary_project_sensor_schema.csv --output-jsonl data/derived/gary_project_sensor_schema.jsonl --output-payloads data/derived/gary_project_sensor_payloads.jsonl
py -3.13 run_gateway.py run --input-file data/derived/gary_project_sensor_payloads.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/derived/gary_project_sensor_payloads.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_project_schema_eval.json --input-source gary_derived_project_schema_payload --simulation-layer project_schema_derivation --gateway-layer raspberry_pi_pre_model_pipeline
```

Untuk eksperimen model yang tidak terlalu menguntungkan baseline pressure,
buat dataset turunan baru dengan pressure synthetic yang lebih dinamis. Jangan
menimpa output lama; gunakan nama file baru:

```powershell
py -3.13 run_gateway.py derive-gary-schema --pressure-profile dynamic --input-csv iot_telemetry_data.csv --output-csv data/derived/gary_project_sensor_schema_dynamic.csv --output-jsonl data/derived/gary_project_sensor_schema_dynamic.jsonl --output-payloads data/derived/gary_project_sensor_payloads_dynamic.jsonl
```

Dataset turunan CSV berisi kolom:

```text
timestamp,node_id,sequence,temperature_c,humidity_pct,pressure_hpa,bme_gas_raw,co_raw
```

Jalur lama tetap tersedia untuk debugging parser/pipeline tanpa dataset turunan:

```powershell
py -3.13 run_gateway.py convert-gary --input-csv iot_telemetry_data.csv --output data/canonical/gary_stafford_canonical.jsonl
py -3.13 run_gateway.py run --input-file data/canonical/gary_stafford_canonical.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/canonical/gary_stafford_canonical.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_preprocessing_eval.json
```

Hasil terakhir jalur Gary derived project schema -> Raspberry Pi:

- `pipeline_status`: PASS.
- `dataset_coverage_status`: FULL.
- `lstm_readiness`: READY.
- Shape: `[34536, 12, 16]`.
- NaN/Inf: `0/0`.
- Output CSV dataset: `data/derived/gary_project_sensor_schema.csv`.
- Output payload compact: `data/derived/gary_project_sensor_payloads.jsonl`.
- Output window: `data/processed/lstm_windows.jsonl`.
- Output evaluasi: `data/evaluation/gary_project_schema_eval.json`.

## LSTM Forecasting Workflow

Setelah `data/processed/lstm_windows.jsonl` terbentuk, repo dapat membuat dataset
forecasting dan melatih model LSTM multi-target dengan PyTorch. Input model tetap
`[samples, timesteps, features]`, sedangkan target prediksi adalah sensor utama:
`temperature_c`, `humidity_pct`, `pressure_hpa`, `bme_gas_raw`, dan `co_raw`.

Install dependency ML opsional:

```powershell
py -3.13 -m pip install -e ".[ml]"
```

Siapkan dataset, train, evaluasi, dan prediksi:

```powershell
py -3.13 run_gateway.py prepare-forecast-dataset --windows data/processed/lstm_windows.jsonl --output-npz data/modeling/lstm_forecast_dataset.npz --output-meta data/modeling/lstm_forecast_dataset_meta.json --horizon-steps 5
py -3.13 run_gateway.py train-lstm-forecast --dataset data/modeling/lstm_forecast_dataset.npz --output-dir models/lstm_forecast/latest --epochs 30 --batch-size 64 --hidden-size 64 --device auto
py -3.13 run_gateway.py evaluate-lstm-forecast --dataset data/modeling/lstm_forecast_dataset.npz --model models/lstm_forecast/latest/model.pt --eval-batch-size 1024
py -3.13 run_gateway.py predict-lstm-forecast --windows data/processed/lstm_windows.jsonl --model models/lstm_forecast/latest/model.pt --output models/lstm_forecast/latest/predictions.jsonl --max-windows 10
py -3.13 run_gateway.py build-forecast-payload-v1 --predictions models/lstm_forecast/latest/predictions.jsonl --metrics models/lstm_forecast/latest/metrics.json --output models/lstm_forecast/latest/forecast_payloads.jsonl
py -3.13 run_gateway.py build-forecast-decision-v1 --forecast-payloads models/lstm_forecast/latest/forecast_payloads.jsonl --output models/lstm_forecast/latest/decision_payloads.jsonl
```

Untuk membandingkan beberapa horizon dan ukuran LSTM sekaligus:

```powershell
py -3.13 run_gateway.py run-forecast-experiments --windows data/processed/lstm_windows.jsonl --output-dir models/forecast_experiments/latest --horizons 5,15,30 --window-sizes 12,24,36 --hidden-sizes 32,64 --epochs 30 --batch-size 64 --device auto --eval-batch-size 1024
py -3.13 run_gateway.py select-best-forecast-model --summary models/forecast_experiments/latest/summary.csv --output models/forecast_experiments/latest/best_model_selection.json
```

Untuk eksperimen CUDA, gunakan `--device cuda`; jika VRAM penuh saat evaluasi, turunkan `--eval-batch-size`.

Output model dan dataset training berada di `data/modeling/` dan `models/`, lalu
di-ignore dari Git. Detail ada di `docs/lstm-forecasting.md`.

Evaluator forecasting sekarang memisahkan status data, perbandingan baseline,
dan kesiapan model. Jika LSTM kalah dari `last_value_baseline`, pipeline tetap
valid, tetapi model masih dianggap tahap eksperimen.
Metrics juga tersedia dalam skala normalized dan satuan asli hasil denormalisasi.
Model selector memilih kandidat eksperimen dari `summary.csv`, sedangkan
decision layer lokal rule-based mengubah forecast semua target sensor utama
menjadi status awal `normal`, `warning`, atau `critical`. Detail ada di
`docs/lstm-forecasting.md`, `docs/progress-lstm-forecasting-v1.md`, dan
`docs/forecast-decision-layer.md`.

## Verifikasi Python

```powershell
py -3.13 -m compileall -q .
py -3.13 scripts/check_environment.py
py -3.13 -m unittest discover -s tests -p 'test_*.py' -q
```

## Dokumentasi

- `docs/architecture-preprocessing-split.md`
- `docs/esp32-preprocessing.md`
- `docs/raspberry-pi-pipeline.md`
- `docs/lstm-forecasting.md`
- `docs/progress-lstm-forecasting-v1.md`
- `docs/forecast-decision-layer.md`
- `docs/data-source-boundary.md`
- `docs/shared-canonical-schema.md`
- `docs/simulation-reference-pipeline.md`
- `docs/real-offline-file-pipeline.md`
- `docs/real-live-receiver-pipeline.md`
- `docs/real-receiver-contract.md`
- `docs/data-contract.md`
- `docs/progress-gary-derived-project-schema.md`
- `docs/progress-gary-stafford-preprocessing.md`
- `firmware/esp32-c6-sensor-node/README.md`
