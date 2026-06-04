# IIoT AI Sensor Gateway

Repo ini berisi dua bagian yang saling tersambung untuk Industrial Environment Monitoring berbasis IIoT:

- firmware ESP32-C6 sensor node untuk preprocessing ringan dan payload LoRa;
- pipeline Python Raspberry Pi untuk preprocessing AI/pre-model, dataset publik, evaluasi, dan window LSTM-ready.

Python di repo ini tidak di-upload ke ESP32-C6. Firmware ESP32-C6 ditulis sebagai C++ skeleton di folder `firmware/esp32-c6-sensor-node`.

## Arsitektur

```text
BME688/BME668 + SEN0377
-> ESP32-C6 firmware preprocessing ringan
-> compact payload via LoRa/Ebyte E32
-> Raspberry Pi gateway Python pipeline
-> validation lanjutan, resampling, feature extraction, normalization, windowing
-> LSTM-ready dataset atau AI inference
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
- model LSTM penuh;
- deployment produksi final.

## Sensor Target Project

- BME688/BME668: temperature, humidity, pressure, gas/VOC-like raw value.
- SEN0377: CO/gas tambahan.

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

## Gary Stafford End-to-End Simulation Workflow

Dataset Gary dipakai sebagai uji awal pipeline karena punya temperature, humidity, CO, LPG, dan smoke. `co` dipakai sebagai SEN0377-like feature; `lpg/smoke` dipakai sebagai gas proxy awal untuk pendekatan BME688 gas/VOC. Gary tetap parsial karena tidak punya pressure dan tidak punya VOC/BME688 gas asli.

Jalur simulasi yang direkomendasikan sekarang meniru alur hardware awal:

```text
iot_telemetry_data.csv
-> simulasi sensor read Gary
-> ESP32-C6-like light preprocessing
-> compact LoRa payload JSONL
-> Raspberry Pi Python pre-model pipeline
-> window LSTM-ready
```

```powershell
py -3.13 run_gateway.py simulate-gary-esp32 --input-csv iot_telemetry_data.csv --output data/simulated/gary_esp32_lora_payloads.jsonl
py -3.13 run_gateway.py run --input-file data/simulated/gary_esp32_lora_payloads.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/simulated/gary_esp32_lora_payloads.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_esp32_to_raspi_eval.json --input-source gary_esp32_simulated_lora_payload --simulation-layer esp32_light_preprocessing --gateway-layer raspberry_pi_pre_model_pipeline
```

Jalur direct canonical masih tersedia untuk debugging parser/pipeline tanpa simulasi firmware:

```powershell
py -3.13 run_gateway.py convert-gary --input-csv iot_telemetry_data.csv --output data/canonical/gary_stafford_canonical.jsonl
py -3.13 run_gateway.py run --input-file data/canonical/gary_stafford_canonical.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/canonical/gary_stafford_canonical.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_preprocessing_eval.json
```

Hasil terakhir jalur Gary -> ESP32-like -> Raspberry Pi:

- `pipeline_status`: PASS.
- `dataset_coverage_status`: PARTIAL.
- `lstm_readiness`: READY_WITH_LIMITATIONS.
- Shape: `[34536, 12, 16]`.
- NaN/Inf: `0/0`.
- Output payload compact: `data/simulated/gary_esp32_lora_payloads.jsonl`.
- Output window: `data/processed/lstm_windows.jsonl`.
- Output evaluasi: `data/evaluation/gary_esp32_to_raspi_eval.json`.

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
- `docs/data-contract.md`
- `docs/progress-gary-stafford-preprocessing.md`
- `firmware/esp32-c6-sensor-node/README.md`
