# Simulation Reference Pipeline

Simulation/reference pipeline adalah workflow eksperimen untuk membuktikan alur
AI Sensor Gateway sebelum data sensor real tersedia lengkap.

## Tujuan

Workflow ini dipakai untuk:

- menguji adapter dataset;
- menguji parser compact payload;
- menguji preprocessing Raspberry Pi;
- membangun LSTM-ready windows;
- menjalankan forecasting v1;
- membandingkan LSTM dengan baseline;
- menghasilkan forecast payload dan decision payload awal.

Workflow ini bukan validasi final sensor real.

## Alur Gary Derived Project Schema

```text
Gary public dataset
-> derive project schema
-> compact payload project-like
-> shared parser/validator
-> buffer per node
-> resampling
-> feature extraction
-> normalization
-> LSTM-ready windows
-> forecast dataset
-> LSTM forecasting v1
-> evaluator vs last-value baseline
-> model selector
-> forecast_payload_v1
-> forecast_decision_layer_v1
```

Command utama:

```powershell
py -3.13 run_gateway.py derive-gary-schema --pressure-profile dynamic --input-csv iot_telemetry_data.csv --output-csv data/derived/gary_project_sensor_schema_dynamic.csv --output-jsonl data/derived/gary_project_sensor_schema_dynamic.jsonl --output-payloads data/derived/gary_project_sensor_payloads_dynamic.jsonl
py -3.13 run_gateway.py run --input-file data/derived/gary_project_sensor_payloads_dynamic.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/derived/gary_project_sensor_payloads_dynamic.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_project_schema_dynamic_eval.json --input-source gary_derived_project_schema_payload --simulation-layer project_schema_derivation --gateway-layer raspberry_pi_pre_model_pipeline
```

## Batasan Gary Derived

- `pressure_hpa` adalah synthetic, bukan pressure asli dataset Gary.
- `bme_gas_raw` adalah proxy dari LPG/smoke, bukan BME688/BME668 real.
- `co_raw` dipakai sebagai SEN0377-like feature.
- Model dan threshold dari workflow ini hanya valid sebagai referensi awal.
- Hasil eksperimen harus dibandingkan dengan baseline dan tidak boleh diklaim
  sebagai model produksi.

## Output Reference

Output lokal berada di folder ignored:

```text
data/derived/
data/processed/
data/modeling/
data/evaluation/
models/
```

Karena artifact ini tidak masuk Git, hasil penting harus diringkas dalam
dokumen `docs/`, misalnya laporan progres model selection atau evaluasi
forecasting.

## Hubungan Dengan Real Pipeline

Simulation/reference pipeline memberi knowledge dan komponen reusable:

- canonical field naming;
- parser alias compact;
- validator awal;
- preprocessing/windowing;
- evaluator dan baseline;
- model selector;
- forecast/decision payload shape.

Data dan model simulasi tidak boleh dicampur dengan data/model real. Real
pipeline harus memakai adapter real sendiri, lalu masuk ke canonical schema yang
sama.
