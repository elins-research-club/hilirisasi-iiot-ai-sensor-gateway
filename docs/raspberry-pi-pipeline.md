# Pipeline Raspberry Pi dan Dataset Publik

Pipeline Raspberry Pi adalah preprocessing AI/pre-model. Ini bukan firmware ESP32 dan bukan preprocessing ringan yang berjalan di node.

## Input

Raspberry Pi menerima:

- compact payload dari ESP32-C6 via LoRa/Ebyte E32;
- canonical JSONL dari dataset publik untuk eksperimen;
- data synthetic dari simulator.

## Tahapan

1. Terima compact payload ESP32-C6 atau hasil simulator ESP32-like.
2. Parse payload.
3. Validasi lanjutan temperature, humidity, pressure, gas signal, CO, status, quality, dan sequence gap.
4. Group atau buffer per node/device.
5. Resampling per interval tetap, default 60 detik.
6. Feature extraction: raw value, delta, rolling gas mean/std, missing count, valid ratio, sequence gap count.
7. Normalisasi min-max dari `config/default.toml`.
8. Windowing per node dengan default 12 timestep.
9. Tulis `data/processed/lstm_windows.jsonl`.
10. Opsional: buat dataset forecasting X/y, train LSTM multi-target, lalu evaluasi MAE/RMSE.

## Workflow Gary yang Direkomendasikan

Untuk belajar dan simulasi dengan schema yang mirip sensor project, gunakan dataset turunan project schema:

```powershell
py -3.13 run_gateway.py derive-gary-schema --input-csv iot_telemetry_data.csv --output-csv data/derived/gary_project_sensor_schema.csv --output-jsonl data/derived/gary_project_sensor_schema.jsonl --output-payloads data/derived/gary_project_sensor_payloads.jsonl
py -3.13 run_gateway.py run --input-file data/derived/gary_project_sensor_payloads.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/derived/gary_project_sensor_payloads.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_project_schema_eval.json --input-source gary_derived_project_schema_payload --simulation-layer project_schema_derivation --gateway-layer raspberry_pi_pre_model_pipeline
```

`simulate-gary-esp32` dan `convert-gary` tetap tersedia sebagai jalur debugging. Jalur derived schema lebih mudah dipahami karena field-nya sudah sama dengan target sensor: temperature, humidity, pressure, BME gas raw, dan CO.

## Beda Dengan ESP32 Preprocessing

ESP32-C6 hanya melakukan preprocessing ringan agar payload stabil untuk LoRa. Raspberry Pi melakukan preprocessing yang dibutuhkan model AI/time-series, termasuk resampling, feature extraction, normalisasi, dan windowing.

## Dataset Roadmap

- Gary Stafford: uji awal pipeline karena tersedia temperature, humidity, CO, LPG, dan smoke. CO dipakai sebagai SEN0377-like feature; LPG/smoke dipakai untuk membuat BME gas raw proxy di dataset turunan.
- Bristol: dataset utama berikutnya karena lebih mirip BME688-like multi-device indoor sensor dengan temperature, humidity, pressure, gas, dan RSSI.
- GAMS/AQUAIR: referensi lanjutan untuk VOC dan IAQ.

## Batasan Saat Ini

- Input hardware serial/LoRa real belum diikat langsung ke reader Python.
- MQTT publish belum diaktifkan di repo ini.
- LSTM forecasting v1 sudah tersedia sebagai workflow opsional setelah windowing; model ini memprediksi sensor utama dan belum menjadi decision layer final.
- Gary asli tidak memiliki pressure_hpa dan BME gas raw asli. Workflow derived schema membuat pressure synthetic realistis dan BME gas raw proxy agar bentuk data sesuai sensor project.

## LSTM Forecasting Setelah Windowing

Workflow LSTM forecasting memakai `data/processed/lstm_windows.jsonl` sebagai input.
Dataset builder membuat pasangan:

```text
X = window 12 timestep x 16 fitur
y = temperature_c, humidity_pct, pressure_hpa, bme_gas_raw, co_raw pada horizon berikutnya
```

Default horizon adalah 5 step. Dengan resampling 60 detik, target berarti sekitar
5 menit setelah akhir window input. Evaluasi membandingkan LSTM dengan
`last_value_baseline` agar performa model tidak hanya terlihat kompleks, tetapi
punya pembanding sederhana.
