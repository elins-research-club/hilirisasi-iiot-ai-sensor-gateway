# Progress Gary ESP32-Like ke Raspberry Pi Preprocessing

> **Historical v1 evidence.** Isi ini mendokumentasikan simulasi proxy lama, bukan firmware atau contract aktif. Current source memakai `compact_sensor.v3` hardware observation dan gateway-centric semantic preprocessing; lihat `data-contract.md`, ADR-001, dan laporan migrasi 11 Juli 2026.

## Tujuan

Laporan ini mencatat progres simulasi end-to-end AI sensor pre-model memakai dataset publik Gary Stafford. Targetnya adalah memastikan alur dari pembacaan sensor simulatif, preprocessing ringan ESP32-C6, payload compact LoRa, sampai pipeline Raspberry Pi dapat menghasilkan window siap LSTM.

Model LSTM penuh belum diimplementasikan pada tahap ini.

## Dataset dan Mapping

Dataset lokal:

```text
C:\vscode\IIOT-Project\iiot-ai-sensor-gateway\iot_telemetry_data.csv
```

Kolom Gary:

```text
ts, device, co, humidity, light, lpg, motion, smoke, temp
```

Mapping yang dipakai:

- `ts` menjadi timestamp.
- `device` menjadi node_id.
- `temp` menjadi `temperature_c`.
- `humidity` menjadi `humidity_pct`.
- `co` menjadi generic CO proxy pada field legacy `co_raw`; bukan representasi chip SEN0466.
- `lpg` dan `smoke` menjadi gas proxy untuk `bme_gas_raw`.
- `pressure_hpa` unavailable karena Gary tidak punya pressure.
- Gary asli tidak memiliki BME gas raw dari sensor Bosch; LPG/smoke hanya dipakai sebagai gas-like proxy.
- `light` dan `motion` disimpan sebagai ignored columns dan tidak masuk fitur utama default.

Gary tidak dianggap sebagai representasi final sensor 1:1. Gary hanya uji awal pipeline karena punya temperature, humidity, CO, LPG, dan smoke.

## Alur Preprocessing

Alur yang diuji:

```text
Gary CSV
-> simulasi sensor read
-> ESP32-C6-like light preprocessing
-> compact LoRa payload JSONL
-> Raspberry Pi parser dan validator
-> resampling 60 detik
-> feature extraction
-> min-max normalization
-> windowing 12 timestep
-> LSTM-ready JSONL
```

Preprocessing ringan ESP32-like di simulator:

- range check temperature, humidity, CO, dan gas proxy;
- missing check field target/proxy;
- moving average 3 sampel per device;
- pembulatan nilai payload;
- sequence number per device;
- flag `pressure_unavailable`, `voc_not_native`, dan `gas_proxy_from_lpg_smoke`;
- payload compact dengan alias `s.tc`, `s.h`, `s.bme`, dan `s.co`.

## Command yang Dipakai

```powershell
py -3.13 run_gateway.py simulate-gary-esp32 --input-csv iot_telemetry_data.csv --output data/simulated/gary_esp32_lora_payloads.jsonl
py -3.13 run_gateway.py run --input-file data/simulated/gary_esp32_lora_payloads.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/simulated/gary_esp32_lora_payloads.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_esp32_to_raspi_eval.json --input-source gary_esp32_simulated_lora_payload --simulation-layer esp32_light_preprocessing --gateway-layer raspberry_pi_pre_model_pipeline
```

## Hasil Evaluasi

- `pipeline_status`: PASS.
- `dataset_coverage_status`: PARTIAL.
- `lstm_readiness`: READY_WITH_LIMITATIONS.
- Input source: `gary_esp32_simulated_lora_payload`.
- Simulation layer: `esp32_light_preprocessing`.
- Gateway layer: `raspberry_pi_pre_model_pipeline`.
- Total data: 405184 baris.
- Jumlah node/device: 3.
- Node/device: `00:0f:00:70:91:0a`, `1c:bf:ce:15:ec:4d`, `b8:27:eb:bf:9d:51`.
- Rentang waktu: 2020-07-12T00:01:34.385975+00:00 sampai 2020-07-20T00:03:37.264313+00:00.
- Valid data: 405184.
- Invalid data: 0.
- Missing rate `temperature_c`: 0.0.
- Missing rate `humidity_pct`: 0.0.
- Missing rate `co_raw`: 0.0.
- Missing rate `bme_gas_raw`: 0.0.
- Missing rate `gas_raw`: 1.0.
- Missing rate `pressure_hpa`: 1.0.
- Kolom dipakai: `temp`, `humidity`, `co`, `lpg`, `smoke`.
- Kolom proxy: `lpg`, `smoke`.
- Kolom diabaikan: `light`, `motion`.
- Jumlah window: 34536.
- Shape akhir: `[34536, 12, 16]`.
- NaN count: 0.
- Inf count: 0.

## Interpretasi

Pipeline berhasil karena payload compact dari simulator ESP32-like dapat diparse, divalidasi, diresampling, diekstrak fiturnya, dinormalisasi, dan di-window tanpa NaN/Inf. Status teknis pipeline adalah PASS.

Coverage dataset tetap PARTIAL karena workflow legacy ini tidak mengisi pressure dari dataset turunan. LPG/smoke hanya dipakai sebagai gas proxy awal melalui `bme_gas_raw`. Pada payload compact ini `gas_raw` memang kosong karena sinyal gas utama sudah dibawa lewat alias `s.bme`.

Dengan hasil ini, output sudah siap untuk uji awal model time-series sebagai `READY_WITH_LIMITATIONS`, bukan sebagai dataset final produksi.

## Keterbatasan Gary

- Tidak ada `pressure_hpa`.
- Tidak ada BME gas raw asli dari sensor Bosch.
- LPG/smoke adalah gas-like proxy, bukan pengganti penuh BME gas raw dari sensor real.
- CO tersedia sebagai proxy generik, tetapi skala/karakteristik SEN0466 real tetap harus divalidasi.
- Dataset publik Gary tidak merepresentasikan wiring, noise, latency, RSSI, packet loss LoRa, atau kondisi sensor real.

## Rencana Lanjut

- Integrasikan receiver LoRa real di Raspberry Pi agar payload ESP32-C6 real bisa masuk ke parser yang sama.
- Lengkapi firmware ESP32-C6 dengan driver BME688, SEN0466, SEN0574, SEN0321, CO₂, PM, dan INA226 setelah kontrak/pin/bus final.
- Pakai Bristol sebagai dataset utama berikutnya karena lebih dekat ke BME688-like multi-device indoor sensor dengan temperature, humidity, pressure, gas, dan RSSI.
- Pakai GAMS/AQUAIR sebagai referensi lanjutan VOC/IAQ.
- Setelah data real cukup, latih baseline anomaly/forecasting sebelum implementasi LSTM penuh.
