# AGENTS.md — IIOT AI Sensor Gateway

Instruksi untuk agent yang bekerja di repo `iiot-ai-sensor-gateway`.

## Scope Repo

Repo ini khusus node sensor dan AI sensor gateway:

- firmware ESP32-C6 sensor node;
- payload compact melalui LoRa/Ebyte E32;
- receiver serial/replay, parser, validation, dan canonical record;
- preprocessing Raspberry Pi: resampling, feature extraction, normalization, windowing;
- dataset adapter, baseline, forecasting, anomaly/drift, dan decision layer;
- kontrak `sensor_ai.v1` dan `sensor_status.v1` menuju MQTT/backend.

Jangan mengerjakan computer vision, backend/dashboard penuh, broker produksi, OpenClaw runtime, atau deployment produksi final kecuali diminta eksplisit.

## Hardware dan Pembagian Device

Target RAB node sensor:

- BME688: temperatur, kelembapan, tekanan, dan gas resistance;
- SEN0466: CO digital terkalibrasi dalam ppm;
- SEN0574: sinyal NO₂ analog dalam mV dan rasio kualitatif, bukan ppm;
- SEN0321: O₃;
- MH-Z19B/C: CO₂;
- PMS7003T: PM1, PM2.5, PM10;
- INA226: tegangan, arus, daya.

Nama modul CO analog lama hanya artefak desain terdahulu dan bukan target hardware aktif. Sensor presence milik subsistem kamera tidak boleh masuk firmware, payload, fitur, dataset canonical, atau target model node sensor.

ESP32-C6 hanya menangani:

- pembacaan sensor;
- validasi ringan dan moving average;
- status per sensor;
- sequence dan boot identity;
- payload `compact_sensor.v2`;
- komunikasi LoRa.

AI utama tetap di Raspberry Pi/laptop/server Python. ESP32-C6 tidak menjalankan LSTM, FITS, DLinear, Half-Space Trees, atau model berat lain.

## State Aktual

- Default firmware adalah environment `mock`; environment `hardware` terpisah.
- Firmware mock menghasilkan payload v2 lengkap dan valid.
- Hardware path yang tersedia: SEN0466 ber-checksum, SEN0321 automatic-read, ADC terkalibrasi SEN0574, INA226, SC16IS752 dual-UART, MH-Z19 checksum/warm-up, PMS7003T passive-frame/checksum/warm-up, serta adapter Bosch BME68x SensorAPI. Profile BME dengan source resmi sudah compile-validated; default tetap disabled bila dependency lokal tidak ada. Semua jalur baru belum hardware-verified dan harus fail-closed.
- E32 memakai UART1 dan AUX handshake. MH-Z19/PMS memakai channel A/B SC16IS752 dengan crystal 1.8432 MHz. Address, crystal, PCB, rail 5 V, dan level logic belum tervalidasi hardware.
- Receiver real-live menulis append-only `raw_envelopes.jsonl`, `accepted_payloads.jsonl`, `rejected_payloads.jsonl`, dan `receiver_events.jsonl`, dengan atomic rotation.
- Waktu node berbasis uptime tidak dianggap Unix epoch. Gateway menambahkan `receive_timestamp` dan `time_quality`.
- Kontrak compact v2 memiliki stable `event_id`, `boot_id`, `sequence`, status per sensor, serta field RAB penuh.
- Compact v1 hanya migration/reference dan dapat dimatikan melalui config.
- `min_valid_ratio` digunakan sebelum windowing; `node_silent_after_sec` digunakan untuk node-health/status.
- LSTM, FITS-inspired, DLinear, dan River streaming tetap `EXPERIMENTAL` sampai mengalahkan baseline dengan data real yang layak.

## Kontrak dan MQTT

Topik yang dibekukan:

```text
iot/{gateway_id}/data
iot/{gateway_id}/status/sensor
```

Topik status lama tanpa namespace domain hanya migration-only.

Compact v2 fields:

```text
tc,h,p,bme,co,n2mv,n2r,o3,co2,pm1,pm25,pm10,bv,bi,bp
```

Makna penting:

- `co` = SEN0466 ppm;
- `n2mv`/`n2r` = NO₂ kualitatif;
- missing = `null`, bukan nol;
- partial sensor failure boleh diteruskan dengan status/flags;
- frame invalid, duplicate, out-of-order, model invalid, atau timestamp invalid harus fail-closed.

Schema resmi:

- `schemas/compact_sensor.v2.schema.json`
- `schemas/sensor_ai.v1.schema.json`
- `schemas/sensor_status.v1.schema.json`

## Data Policy

Pisahkan lane:

1. simulation/reference;
2. raw real offline capture;
3. real live receiver.

Aturan:

- raw real tidak boleh ditimpa atau dinormalisasi permanen;
- adapter per dataset wajib menjaga unit, provenance, quality, dan missing value;
- jangan mengisi field sensor yang tidak tersedia;
- UCI Air Quality dan Bristol BME680 bukan bukti chip-identical terhadap hardware proyek;
- Gary hanya regression/compatibility lane;
- jangan commit dataset besar, `data/`, `models/`, checkpoint, cache, log, credential, atau artifact runtime.

## Model Policy

Baseline gate wajib:

- LastValue;
- SeasonalNaive;
- DLinear;
- Isolation Forest/rules bila relevan.

Model tambahan:

- LSTM existing: experimental;
- FITS-inspired edge forecaster: experimental dan bukan reproduksi bit-for-bit paper;
- native RobustZScore + PageHinkley: challenger anomaly/drift E2E tanpa dependency tambahan, input wajib 0–1 dan warm-up;
- River Half-Space Trees + ADWIN: optional dependency challenger, input wajib 0–1 dan warm-up;
- decision layer: quality/rules dahulu, model kemudian, abstain bila data tidak layak.

Metrik minimum:

- forecast: MAE, RMSE, MASE, skill terhadap baseline;
- anomaly: precision, recall, F1, false-alert/day bila label tersedia;
- drift: detection delay dan false drift bila ground truth tersedia;
- resource: hanya hardware tempat ukur aktual, jangan mengarang angka Raspberry Pi.

## Dokumentasi Wajib Sinkron

Setiap perubahan firmware, contract, parser, validation, feature, receiver, dataset, model, CLI, atau deployment harus memperbarui pada pass yang sama:

- `README.md`;
- firmware README;
- docs terkait;
- schema/config/tests;
- `../Project Context/AI_SENSOR.md`;
- `../Project Context/AI_SENSOR_GATEWAY_PRE_MODEL.md`;
- `../Project Context/DATASETS_TESTING.md`;
- `../Project Context/BACKEND_MQTT_OPENCLAW.md`;
- `../Project Context/DEV_TASKS.md`.

## Coding Rules

- Baca sebelum edit dan periksa dirty state.
- Jangan overwrite perubahan lokal tanpa review.
- Runtime receiver/core stdlib-first; NumPy/PyTorch/River opsional.
- Firmware ESP32 ditulis C++/ESP-IDF melalui PlatformIO.
- Jangan print secret, token, password, `.env`, atau endpoint privat.
- Jangan install dependency berat, menjalankan training panjang, restart service, commit, atau push tanpa izin.
- Jangan menyatakan hardware lulus hanya karena build host berhasil.

## Verifikasi

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY -m compileall -q src tests run_gateway.py scripts/download_dataset.py
$PY -m unittest discover -s tests -p 'test_*.py' -q
$PY run_gateway.py check-config --config config/default.toml
git diff --check
```

Firmware:

```bash
cd firmware/esp32-c6-sensor-node
pio run -e mock
pio run -e hardware
# setelah user memasang official Bosch BME68x SensorAPI:
pio run -e hardware-bme68x
```

Build hardware hanya membuktikan kompilasi profile. Sensor fisik, bridge UART, E32, rail 5 V, flash aktual, kalibrasi, dan konsumsi daya tetap membutuhkan pengujian board.

## Handoff

Final response harus menyebut:

- file kode/firmware yang berubah;
- docs dan Project Context yang diperbarui;
- command verifikasi dan hasil nyata;
- level bukti software vs hardware/data/produksi;
- Git status/branch/commit/push bila ada.
