# IIoT AI Sensor Gateway

Repo ini menghubungkan node sensor ESP32-C6 dengan pipeline AI sensor di Raspberry Pi.

```text
sensor RAB
-> ESP32-C6: read + validasi ringan + moving average
-> compact_sensor.v2 melalui Ebyte E32
-> Raspberry Pi: receiver + parser + validasi + canonical data
-> resampling + features + normalization + windowing
-> baseline/model/decision
-> sensor_ai.v1 dan sensor_status.v1 untuk integrasi MQTT
```

Python tidak dijalankan di ESP32-C6. Firmware device berada di `firmware/esp32-c6-sensor-node`; pipeline gateway berada di `src/iiot_ai_sensor_gateway`.

## Hardware Target

| Perangkat | Field utama | Status implementasi saat ini |
|---|---|---|
| BME688 | temperatur, kelembapan, tekanan, gas resistance | adapter Bosch SensorAPI forced-mode tersedia; profile resmi compile-validated, default tetap disabled tanpa dependency lokal; belum hardware-verified |
| SEN0466 | CO ppm | jalur I²C digital ber-checksum tersedia dan compile-tested; belum hardware-verified |
| SEN0574 | NO₂ mV dan rasio kualitatif | ADC curve-fitting mV tersedia; rasio menunggu baseline commissioning |
| SEN0321 | O₃ ppm | jalur I²C automatic-read tersedia dan compile-tested; belum hardware-verified |
| MH-Z19B/C | CO₂ ppm | SC16IS752 channel A, checksum Winsen, timeout, range check, dan warm-up 180 s tersedia; belum hardware-verified |
| PMS7003T | PM1/PM2.5/PM10 | SC16IS752 channel B, wake/passive read, frame checksum, dan warm-up 30 s tersedia; belum hardware-verified |
| INA226 | tegangan, arus, daya | calibration register dan register read tersedia; kalibrasi final menunggu shunt aktual |
| Ebyte E32 | transport LoRa | UART/AUX/error handling tersedia; link fisik belum diverifikasi |

Sensor presence untuk subsistem kamera tidak termasuk node sensor ini.

## Kontrak Compact v2

Payload ESP32-C6 memakai:

```text
v,gw,n,r,ts,seq,bid,st,q,f,ok,s
```

Field `s`:

```text
tc,h,p,bme,co,n2mv,n2r,o3,co2,pm1,pm25,pm10,bv,bi,bp
```

Semantik penting:

- `ts` boleh uptime node; gateway selalu mencatat `receive_timestamp` dan `time_quality`;
- `bid + seq` membentuk identitas reboot-safe;
- `event_id` deterministik sehingga retry menghasilkan ID yang sama;
- missing sensor ditulis `null`, bukan nol;
- `co` adalah ppm dari SEN0466;
- NO₂ tetap mV/rasio kualitatif, bukan ppm;
- kegagalan sebagian sensor boleh diteruskan sebagai `partial` dengan status/flags;
- payload rusak, versi tidak dikenal, duplicate, dan out-of-order ditolak.

Schema:

- `schemas/compact_sensor.v2.schema.json`
- `schemas/sensor_ai.v1.schema.json`
- `schemas/sensor_status.v1.schema.json`

Dokumentasi rinci: `docs/data-contract.md`.

## Receiver Real-Live

Receiver mendukung replay file dan serial. Seluruh log bersifat append-only dan dapat dirotasi secara atomik:

```text
raw_envelopes.jsonl
accepted_payloads.jsonl
rejected_payloads.jsonl
receiver_events.jsonl
```

Replay tanpa hardware:

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY run_gateway.py receive-real-live \
  --replay-file tests/fixtures/real_payload_samples.jsonl \
  --output-dir data/real_live_logs \
  --max-messages 8
```

Serial memerlukan dependency opsional:

```bash
python -m pip install -e '.[serial]'
$PY run_gateway.py receive-real-live \
  --port /dev/ttyUSB0 \
  --baudrate 9600 \
  --timeout 1.0 \
  --output-dir data/real_live_logs
```

Serial source memakai idle sleep dan exponential reconnect backoff sehingga port kosong atau terputus tidak membentuk busy-loop.

## Firmware ESP32-C6

Default build memakai mock sensor:

```bash
cd firmware/esp32-c6-sensor-node
/home/ubuntu/.venvs/platformio/bin/pio run -e mock
```

Compile profile hardware:

```bash
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware
```

Profile ini mengompilasi bridge SC16IS752, MH-Z19, PMS7003T, SEN0466, SEN0574, SEN0321, INA226, dan E32. BME688 memakai profile terpisah karena source resmi tidak dipasang otomatis:

```bash
cd firmware/esp32-c6-sensor-node
mkdir -p lib
git clone --depth 1 https://github.com/boschsensortec/BME68x_SensorAPI.git lib/BME68x_SensorAPI
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware-bme68x
```

Profile mock dikunci 8 MB; profile hardware dikunci 16 MB sesuai target modul RAB. Build bukan bukti ukuran flash chip fisik. Verifikasi chip, pin, rail 5 V, bridge dual-UART, sensor, dan E32 tetap wajib sebelum upload lapangan.

Dokumentasi firmware: `firmware/esp32-c6-sensor-node/README.md`.

## Data Lanes dan Dataset

Tiga lane tidak boleh dicampur tanpa provenance:

1. simulation/reference;
2. raw real offline capture;
3. real live receiver.

Catalog riset berada di `datasets/catalog.json`. Dataset besar tidak disimpan di Git. Artifact UCI dan Zenodo yang allow-listed sudah diunduh ke `/tmp` untuk verifikasi adapter; SHA-256 resminya dikunci di catalog sehingga download berikutnya diverifikasi otomatis.

Dataset yang sudah memiliki adapter:

- UCI Air Quality: temperatur/RH menjadi canonical; CO dan NO₂ tetap pada unit referensi asal; missing `-200` menjadi `null`;
- Bristol BME680 smart building: temperatur/RH/tekanan menjadi canonical; nilai gas tetap IAQ index, bukan gas resistance dan bukan CO₂;
- Zenodo 7198378 Fidas 200S: PM referensi tetap berada di `reference.*`; field PMS7003T proyek sengaja `null` sampai tersedia pasangan low-cost yang benar;
- Gary Stafford: regression/compatibility lane saja; field turunan sintetis tidak boleh dipakai sebagai bukti hardware;
- SensEURCity: kandidat multi-city CC BY 4.0, tetapi arsip sekitar 5,7 GB tetap manual-only sampai schema dan kebutuhan storage disetujui.

Lihat katalog atau satu entry tanpa download:

```bash
$PY scripts/download_dataset.py --list
$PY scripts/download_dataset.py uci_air_quality_360 --describe-only
```

Download allow-listed yang kecil:

```bash
$PY scripts/download_dataset.py uci_air_quality_360 \
  --output-dir data/external/downloads \
  --max-bytes 52428800
```

Adapter:

```bash
$PY run_gateway.py adapt-uci-air-quality \
  --input-csv data/external/AirQualityUCI.csv \
  --output data/canonical/uci_air_quality.jsonl

$PY run_gateway.py adapt-bristol-bme680 \
  --input-csv data/external/bristol/device.csv \
  --output data/canonical/bristol_bme680.jsonl

$PY run_gateway.py adapt-zenodo-pm-reference \
  --input-csv data/external/df_pm_2min.csv \
  --output data/canonical/zenodo_fidas_pm_reference.jsonl
```

Detail: `docs/dataset-catalog-and-adapters.md`.

## Pipeline dan Modeling

Pipeline dasar:

```bash
$PY run_gateway.py simulate --scenario mixed --count 120 --nodes 2 \
  --output data/simulated/payloads.jsonl
$PY run_gateway.py run --input-file data/simulated/payloads.jsonl \
  --output-dir data/processed
```

Model saat ini semuanya **eksperimental**:

- LastValue dan SeasonalNaive sebagai gate;
- DLinear sebagai baseline neural ringan;
- LSTM existing;
- FITS-inspired edge forecaster, bukan reproduksi bit-for-bit paper;
- native RobustZScore + PageHinkley sebagai anomaly/drift E2E tanpa dependency tambahan;
- optional River Half-Space Trees + ADWIN sebagai challenger streaming; smoke aktual lulus di venv temporer, tetapi dependency tidak dipasang otomatis dan benchmark data nyata masih open.

Target forecasting canonical:

```text
temperature_c,humidity_pct,pressure_hpa,co_ppm,o3_ppm,co2_ppm,pm25_ug_m3
```

Siapkan dataset dan jalankan model:

```bash
$PY run_gateway.py prepare-forecast-dataset \
  --windows data/processed/lstm_windows.jsonl \
  --output-npz data/modeling/lstm_forecast_dataset.npz \
  --output-meta data/modeling/lstm_forecast_dataset_meta.json \
  --horizon-steps 5

$PY run_gateway.py train-edge-forecast \
  --dataset data/modeling/lstm_forecast_dataset.npz \
  --output-dir models/edge_forecast/fits \
  --model-type fits

$PY run_gateway.py evaluate-edge-forecast \
  --dataset data/modeling/lstm_forecast_dataset.npz \
  --model models/edge_forecast/fits/model.pt \
  --seasonal-period 24
```

Streaming native dapat langsung dijalankan dan tetap mewajibkan fitur 0–1:

```bash
$PY run_gateway.py stream-detect \
  --backend native \
  --input data/modeling/normalized_features.jsonl \
  --output data/modeling/streaming_detection.jsonl \
  --feature-names temperature_c,humidity_pct,co2_ppm,pm25_ug_m3
```

River adalah dependency opsional dan tidak diinstal otomatis:

```bash
python -m pip install -e '.[streaming]'
$PY run_gateway.py stream-detect \
  --backend river \
  --input data/modeling/normalized_features.jsonl \
  --output data/modeling/river_detection.jsonl \
  --feature-names temperature_c,humidity_pct,co2_ppm,pm25_ug_m3
```

Decision layer mengutamakan quality/rules, lalu baseline/model. Data invalid, stale, atau unavailable menghasilkan `abstain=true`, bukan status normal palsu.

Detail:

- `docs/modeling-fits-river-decision.md`
- `docs/lstm-forecasting.md`
- `docs/MODEL_COMPARISON.md`

## MQTT Integration Contract

Target topic:

```text
iot/{gateway_id}/data
iot/{gateway_id}/status/sensor
```

Topik status lama tanpa namespace domain hanya migration-only. Repo ini menyediakan schema dan payload builder, tetapi belum menjalankan broker/production publisher.

## Konfigurasi

```bash
$PY run_gateway.py check-config --config config/default.toml
```

`min_valid_ratio` digunakan untuk menyaring resampled point sebelum windowing. `node_silent_after_sec` digunakan untuk status node stale.

## Verifikasi

```bash
$PY -m compileall -q src tests run_gateway.py scripts/download_dataset.py
$PY -m unittest discover -s tests -p 'test_*.py' -q
$PY run_gateway.py check-config --config config/default.toml
git diff --check
```

## Batas Verifikasi Saat Ini

Sudah terverifikasi pada host:

- parser/validator/receiver/config/schema;
- append-only restart behavior;
- compact v2 end-to-end mock;
- model smoke train/eval/predict FITS-inspired, DLinear, dan LSTM;
- native streaming RobustZScore + PageHinkley end-to-end;
- firmware mock, hardware, dan `hardware-bme68x` dengan official Bosch SensorAPI dapat dikompilasi.

Belum terverifikasi:

- pembacaan seluruh sensor fisik;
- bridge dual-UART dan rail 5 V secara fisik;
- pembacaan BME688 fisik (official SensorAPI baru compile-validated di host);
- E32 end-to-end;
- flash 16 MB pada board final;
- kalibrasi NO₂/INA226/O₃/CO/PM/CO₂;
- MQTT broker produksi;
- benchmark model pada dataset real dan Raspberry Pi;
- false-alert/day, drift delay, dan battery endurance lapangan.

## Dokumentasi Utama

- `docs/sensor-foundation-implementation-report-2026-07-10.md`
- `docs/data-contract.md`
- `docs/real-live-receiver-pipeline.md`
- `docs/dataset-catalog-and-adapters.md`
- `docs/modeling-fits-river-decision.md`
- `docs/testing-troubleshooting.md`
- `firmware/esp32-c6-sensor-node/README.md`
