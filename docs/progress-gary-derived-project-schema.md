# Progress Gary Derived Project Sensor Schema

> **Historical v1 evidence.** Dokumen ini mempertahankan workflow proxy/sintetis lama untuk reproduksi eksperimen. Kontrak firmware aktif adalah `compact_sensor.v3`; v2 hanya compatibility. Lihat `data-contract.md` dan ADR-001. Jangan gunakan hasil derived Gary sebagai bukti sensor RAB atau performa lapangan.

## Tujuan

Dokumen ini mencatat pembuatan dataset turunan dari Gary Stafford agar kolomnya lebih mudah dipahami dan lebih dekat dengan target sensor project. Dataset Gary asli tidak diubah.

## Riset Singkat Sensor

BME688/BME668 tidak diperlakukan sebagai sensor VOC mentah langsung di repo ini. Field mentah utama yang dipakai adalah temperature, humidity, pressure, dan BME gas raw/gas resistance style signal. Nilai VOC/IAQ sebaiknya menjadi output olahan tahap berikutnya, bukan kolom raw utama.

Referensi:

- Bosch Sensortec BME688 product page: https://www.bosch-sensortec.com/products/environmental-sensors/gas-sensors/bme688/
- Bosch Sensortec BME688 datasheet: https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme688-ds000.pdf

## Schema Dataset Turunan

File CSV turunan:

```text
data/derived/gary_project_sensor_schema.csv
```

Kolom:

```text
timestamp,node_id,sequence,temperature_c,humidity_pct,pressure_hpa,bme_gas_raw,co_raw
```

Mapping:

- `ts` menjadi `timestamp`.
- `device` menjadi `node_id`.
- sequence dibuat monoton per node.
- `temp` menjadi `temperature_c`.
- `humidity` menjadi `humidity_pct`.
- `co` menjadi `co_raw`.
- `lpg` dan `smoke` digabung menjadi dasar `bme_gas_raw`.
- `pressure_hpa` dibuat synthetic realistis karena Gary asli tidak punya pressure.
- `light` dan `motion` dibuang dari schema utama.

## Aturan Derivasi

`bme_gas_raw` dibuat dari rata-rata LPG/smoke, lalu dinormalisasi terhadap rentang gas-like Gary dan diskalakan ke 500-4500. Dengan cara ini pola naik-turun gas tetap mengikuti dataset Gary, bukan angka acak.

`pressure_hpa` dibuat sebagai tren barometrik halus sekitar 1008-1014 hPa, dengan variasi kecil berbasis waktu dan node. Tujuannya membuat field pressure realistis untuk simulasi schema, bukan mengklaim pressure asli dari Gary.

## Command

```powershell
py -3.13 run_gateway.py derive-gary-schema --input-csv iot_telemetry_data.csv --output-csv data/derived/gary_project_sensor_schema.csv --output-jsonl data/derived/gary_project_sensor_schema.jsonl --output-payloads data/derived/gary_project_sensor_payloads.jsonl
py -3.13 run_gateway.py run --input-file data/derived/gary_project_sensor_payloads.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/derived/gary_project_sensor_payloads.jsonl --windows data/processed/windows.jsonl --output data/evaluation/gary_project_schema_eval.json --input-source gary_derived_project_schema_payload --simulation-layer project_schema_derivation --gateway-layer raspberry_pi_pre_model_pipeline
```

## Hasil Evaluasi

- `pipeline_status`: PASS.
- `dataset_coverage_status`: FULL.
- `lstm_readiness`: READY.
- Total data: 405184 baris.
- Jumlah node/device: 3.
- Valid data: 405184.
- Invalid data: 0.
- Missing rate target field: 0.0 untuk temperature, humidity, pressure, BME gas raw, dan CO.
- Pressure range: 1008.68-1013.68 hPa.
- BME gas raw range: 500.0-4500.0.
- Jumlah window: 34536.
- Shape akhir: `[34536, 12, 16]`.
- NaN count: 0.
- Inf count: 0.

## Interpretasi

Dataset turunan berhasil membuat bentuk data yang lebih dekat dengan sensor project dan lebih mudah dipakai untuk belajar pipeline. Status `FULL` berarti semua field target tersedia di dataset turunan, bukan berarti semua field berasal dari sensor asli Gary.

Dataset ini boleh dipakai untuk simulasi pipeline, debugging, dan uji awal LSTM input shape. Untuk evaluasi model final, data real ESP32-C6 dan sensor BME688/BME668 tetap wajib dikumpulkan.

## Rencana Lanjut

- Gunakan dataset turunan ini sebagai jalur belajar utama.
- Tetap simpan jalur Gary asli sebagai referensi parsial.
- Integrasikan data real BME688, SEN0466, SEN0574, SEN0321, CO₂, PM, dan INA226 ketika hardware siap.
- Dataset Bristol tetap menjadi kandidat berikutnya karena lebih dekat ke multi-device indoor sensor dengan pressure dan gas signal.
