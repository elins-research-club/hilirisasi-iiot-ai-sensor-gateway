# AGENTS.md — IIOT AI Sensor Gateway

> **POST-MONEV SCOPE OVERRIDE — 6 Oktober 2026:** current AI priorities in this
> repo are the **existing environmental forecasting/anomaly monitoring pipeline**.
> Sensor health/lifetime has moved to sibling repo `../iiot-sensor-health-ai` and
> must not be reimplemented here. Leak detection/prediction is a planned
> extension that must reuse/evaluate this node-sensor pipeline first; do not
> create a separate gas-risk subsystem without new evidence. Keep `sensor_ai.v2`
> live contract backward-compatible. Camera/vision is paused and remains outside
> this repo.

Instruksi untuk agent yang bekerja di repo `iiot-ai-sensor-gateway`.

## Scope Repo

Repo ini khusus node sensor dan AI sensor gateway:

- firmware ESP32-C6 sensor node;
- payload compact melalui LoRa/Ebyte E32;
- receiver serial/replay, parser, validation, dan canonical record;
- preprocessing Raspberry Pi: resampling, feature extraction, normalization, windowing;
- dataset adapter, baseline, forecasting, anomaly/drift, dan decision layer;
- existing forecasting/anomaly/event benchmark remains the environmental lane; leak detection is plan-only until controlled labels exist;
- kontrak `sensor_ai.v1` compact-origin, `sensor_ai.v2` source-agnostic live, dan `sensor_status.v1` menuju MQTT/backend.

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

- pembacaan sensor dan scheduling;
- protocol/frame/checksum integrity;
- warm-up/heater/fan state;
- official vendor compensation dan conversion ke engineering unit;
- broad hardware-impossibility gate, bukan project semantic threshold;
- status per sensor, sequence, boot identity, time basis, firmware/config/calibration version;
- payload `compact_sensor.v3` dengan `processing_profile=hardware_only`;
- komunikasi LoRa.

Semantic filtering, project validation, resampling, missing policy, feature engineering, normalization, windowing, model, dan final decision hanya berjalan di gateway. `compact_sensor.v2` tetap dibaca sebagai legacy node-preprocessed observation dan tidak difilter ulang secara default.

AI utama tetap di Raspberry Pi/laptop/server Python. ESP32-C6 tidak menjalankan LSTM, FITS, DLinear, Half-Space Trees, atau model berat lain.

## State Aktual

- Deployment current `iiotgw` memakai source `chirpstack_live.v1` dan publish canonical `sensor_ai.v2` pada `iot/iiotgw/data`; `sensor_ai.v1` tetap compact-origin contract.
- Provenance HardProg test current: Node 1 RF/LoRaWAN live tetapi nilai sensornya dummy JSON; Node 2 contract terbaru 5 Oktober 2026 adalah 7 byte big-endian `>BhHH` = packet ID, temperature x10 signed, humidity x10, current mA x100. Node fisik sedang mati saat contract refresh sehingga source HardProg adalah authority sementara sampai replay/hardware correlation tersedia.
- Runtime current memiliki durable raw/canonical logs, SQLite event outbox, QoS 1 publish confirmation, retained `sensor_status.v1`, reconnect handling, restart-state restore, dan versioned Pi deployment/rollback.
- Full cold reboot current sudah diuji; unit `iiot-node-usb-release.service` menangani dua USB-attached ESP/LoRa test nodes agar OTAA rejoin kembali berjalan tanpa mengedit firmware/WM1302/HardProg protected source.
- Default firmware adalah environment `mock`; environment `hardware` terpisah.
- Firmware mock menghasilkan payload v3 hardware observation lengkap dan valid; v2 tetap parser-compatible untuk migration/replay.
- Hardware path yang tersedia: SEN0466 ber-checksum, SEN0321 automatic-read, ADC terkalibrasi SEN0574, INA226, SC16IS752 dual-UART, MH-Z19 checksum/warm-up, PMS7003T passive-frame/checksum/warm-up, serta adapter Bosch BME68x SensorAPI. Profile BME dengan source resmi sudah compile-validated; default tetap disabled bila dependency lokal tidak ada. Semua jalur baru belum hardware-verified dan harus fail-closed.
- E32 memakai UART1 dan AUX handshake. MH-Z19/PMS memakai channel A/B SC16IS752 dengan crystal 1.8432 MHz. Address, crystal, PCB, rail 5 V, dan level logic belum tervalidasi hardware.
- Receiver real-live menulis append-only `raw_envelopes.jsonl`, `accepted_payloads.jsonl`, `rejected_payloads.jsonl`, dan `receiver_events.jsonl`, dengan atomic rotation.
- Waktu node berbasis uptime tidak dianggap Unix epoch. Gateway menambahkan `receive_timestamp` dan `time_quality`.
- Kontrak compact v3 memiliki stable `event_id`, `boot_id`, `sequence`, time basis, firmware/config/calibration provenance, status hardware per sensor, serta field RAB penuh.
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

Compact v3 sensor fields:

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

- `schemas/compact_sensor.v3.schema.json` — hardware observation aktif;
- `schemas/compact_sensor.v2.schema.json` — migration/legacy node-preprocessed;
- `schemas/sensor_ai.v1.schema.json` — compact-origin event;
- `schemas/sensor_ai.v2.schema.json` — live/source-agnostic event;
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
- artifact window canonical hanya `windows.jsonl`; nama lama `lstm_windows.jsonl` didukung reader sebagai fallback migration tanpa membuat copy kedua.

## Model Policy

Baseline gate wajib:

- LastValue;
- drift dan window-mean sederhana;
- SeasonalNaive hanya bila period/window/horizon applicable dan tidak identik dengan LastValue;
- DLinear sebagai challenger neural ringan;
- Isolation Forest/rules bila relevan.

Baseline dipilih pada validation split per target lalu dikunci untuk test. Dataset promotion gate wajib memeriksa cadence/horizon duration, target konstan, boundary saturation/clipping, active feature schema train-only, no-overlap/purge, dan target coverage.

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

## Graphify + CRG (wajib — hard gate root)

Ikuti root `../AGENTS.md` § **Graphify + CRG — HARD GATE** dan `../Project Context/GRAPH_MEMORY_OPERATIONS.md`.

Untuk setiap task audit/implementasi/bugfix/review/model/pipeline di repo ini:

1. **Graphify dulu** (≥4 calls): stats + query task (contoh `compact_sensor.v3 HardwareIntegrityGate`, `sensor_ai.v1 preprocessing windowing`, `ESP32-C6 vs gateway boundary`) + neighbors/community/path.
2. **CRG sebelum patch/review selesai** (≥3 calls): `detect_changes` + impact/review_context + affected flows / tests_for.
3. Final response sertakan `GRAPH_GATE: PASS|FAIL` + tools + findings.
4. Graph tidak menggantikan source/test/firmware build; verifikasi file aktual setelah navigasi graph.

## Coding Rules

- Jalankan hard gate Graphify+CRG sebelum eksplorasi besar/edit. Baca sebelum edit dan periksa dirty state.
- Jangan overwrite perubahan lokal tanpa review.
- Runtime receiver/core stdlib-first; NumPy/PyTorch/River opsional.
- Firmware ESP32 ditulis C++/ESP-IDF melalui PlatformIO.
- Jangan print secret, token, password, `.env`, atau endpoint privat.
- Jangan install dependency berat, menjalankan training panjang, restart service, commit, atau push tanpa izin.
- Jangan menyatakan hardware lulus hanya karena build host berhasil.
- Dari GPT/DevSpace, jalankan laptop CUDA hanya lewat
  `python3 scripts/remote/devspace_laptop_exec.py probe` dan subcommand `run`; jangan
  memakai SSH raw ke IP laptop. Entrypoint ini memetakan cwd ke
  `C:\vscode\IIOT-Project\iiot-ai-sensor-gateway` dan menolak shell/path di luar scope.

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

- blok `GRAPH_GATE` (Graphify tools + CRG tools + findings);
- file kode/firmware yang berubah;
- docs dan Project Context yang diperbarui;
- command verifikasi dan hasil nyata;
- level bukti software vs hardware/data/produksi;
- Git status/branch/commit/push bila ada.
