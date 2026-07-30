# Capability/Evidence Map — Node Sensor IIoT AI Sensor Gateway

> **Audit tanggal:** 29 Juli 2026
> **Repo:** `iiot-ai-sensor-gateway`
> **Bahasa:** Indonesia, seluruh referensi `file:line`
> **Prinsip:** Tidak ada klaim tanpa evidence. Tidak ada fabricated field deployment. Kamera/yolo_vision_gateway dianggap tidak ada.

---

## 1. Hardware Fields per Sensor

### 1.1 BME688 — Temperatur, Kelembapan, Tekanan, Gas Resistance

| Field | Compact key | Unit | Sumber |
|-------|-------------|------|--------|
| temperature_c | `tc` | °C | `sensor_types.h:21` |
| humidity_pct | `h` | %RH | `sensor_types.h:22` |
| pressure_hpa | `p` | hPa | `sensor_types.h:23` |
| bme_gas_ohm | `bme` | ohm | `sensor_types.h:24` |

- Driver: Bosch BME68x SensorAPI v4.4.8 via I²C forced-mode (`sensors.cpp:22-32`, `lib/BME68x_SensorAPI/`)
- Oversampling: humidity 16×, pressure 1×, temperature 2×, filter off (`sensors.cpp:303-307`)
- Heater: 300 °C, 100 ms (`sensors.cpp:310-311`)
- Integriti: new-data + gas-valid + heat-stabil flags wajib (`sensors.cpp:467-471`)
- Gate broad: -40–85 °C, 0–100 %RH, 300–1100 hPa, >0 ohm (`preprocessing.cpp:37-40`, `sensors.cpp:485-489`)
- **Status:** Adapter Bosch compile-validated; default disabled (`config.h:10`, `platformio.ini:35-38`). Hardware path belum hardware-verified.

### 1.2 SEN0466 — CO (Digital Calibrated ppm)

| Field | Compact key | Unit | Sumber |
|-------|-------------|------|--------|
| co_ppm | `co` | ppm CO | `sensor_types.h:25` |

- Protokol: I²C 0x74, command 0xFF-0x01-0x86, two's complement checksum (`sensors.cpp:40-43,502-508`)
- Scaling: response byte[5] menentukan 0.1× atau 0.01× (`sensors.cpp:526-530`)
- Range kontrak: 0–1000 ppm (`sensors.cpp:531`)
- **Status:** Code path lengkap, hardware open. Checksum terverifikasi.

### 1.3 SEN0574 — NO₂ (Analog ADC mV, Ratio Kualitatif)

| Field | Compact key | Unit | Sumber |
|-------|-------------|------|--------|
| no2_raw_mv | `n2mv` | mV | `sensor_types.h:26` |
| no2_ratio | `n2r` | rasio kualitatif | `sensor_types.h:27` |

- ADC: GPIO0, ESP32-C6 ADC1 channel 0, attenuation 12 dB, curve-fitting calibration (`sensors.cpp:231-276`)
- `no2_ratio` set to NAN — tidak dihitung di firmware (`sensors.cpp:559`), hanya placholder untuk gateway-side baseline
- Range: 0–3300 mV (`sensors.cpp:554`)
- **Status:** ADC path jalan. Baseline/ratio commissioning masih open. Bukan ppm.

### 1.4 SEN0321 — O₃

| Field | Compact key | Unit | Sumber |
|-------|-------------|------|--------|
| o3_ppm | `o3` | ppm O₃ | `sensor_types.h:28` |

- I²C 0x73, automatic-read mode (`sensors.cpp:44-47,278-282`)
- Trigger + delay 100 ms, read register 0x09, 2-byte response/1000 (`sensors.cpp:568-580`)
- Range: 0–10 ppm (`sensors.cpp:581`)
- **Status:** Code path lengkap, hardware open.

### 1.5 MH-Z19B/C — CO₂

| Field | Compact key | Unit | Sumber |
|-------|-------------|------|--------|
| co2_ppm | `co2` | ppm CO₂ | `sensor_types.h:29` |

- UART via SC16IS752 channel A, 9600 baud (`sensors.cpp:590-622`, `config.h:57`)
- Winsen checksum (0xFF-sum+1) (`sensors.cpp:68-77`)
- Warm-up 180 detik (`config.h:59`)
- Range: 0–10000 ppm (`sensors.cpp:615`)
- **Status:** UART bridge jalan di code. Crystal 1.8432 MHz, address 0x48. PCB, 5V rail, level logic belum hardware-verified.

### 1.6 PMS7003T — PM1, PM2.5, PM10

| Field | Compact key | Unit | Sumber |
|-------|-------------|------|--------|
| pm1_ug_m3 | `pm1` | µg/m³ | `sensor_types.h:30` |
| pm25_ug_m3 | `pm25` | µg/m³ | `sensor_types.h:31` |
| pm10_ug_m3 | `pm10` | µg/m³ | `sensor_types.h:32` |

- UART via SC16IS752 channel B, 9600 baud (`sensors.cpp:624-654`, `config.h:58`)
- Plantower wake + passive mode (`sensors.cpp:348-355`)
- Frame: 0x42-0x4D header, 32-byte, big-endian, checksum 16-bit (`sensors.cpp:79-106`)
- Warm-up 30 detik (`config.h:60`)
- Range PM: 0–5000 µg/m³ (`sensors.cpp:650-651`)
- **Status:** Frame validation lengkap. Hardware open.

### 1.7 INA226 — Tegangan, Arus, Daya

| Field | Compact key | Unit | Sumber |
|-------|-------------|------|--------|
| battery_voltage | `bv` | V | `sensor_types.h:33` |
| current_ma | `bi` | mA | `sensor_types.h:34` |
| power_mw | `bp` | mW | `sensor_types.h:35` |

- I²C 0x40, register 0x02/0x03/0x04 (`sensors.cpp:704-725`)
- Voltage = bus_raw × 0.00125 V (`sensors.cpp:718`)
- Current LSB: 0.1 mA, calibration reg 5120 (`config.h:65-66`)
- Power = power_raw × 25 × current_LSB (`sensors.cpp:720`)
- **Status:** Shunt resistor value belum ditetapkan. Kalibrasi register adalah placeholder (`config.h:65-67`). Hardware open.

---

## 2. Firmware ESP32-C6

### 2.1 Arsitektur Firmware

```
main.cpp → SensorReader::begin() + loop → HardwareIntegrityGate::process() → buildPayload() → LoRa E32
```

- `platformio.ini:16-18` — default environment `mock` (IIOT_USE_MOCK_SENSORS=1)
- `platformio.ini:25-32` — environment `hardware` (IIOT_USE_MOCK_SENSORS=0)
- `platformio.ini:34-38` — environment `hardware-bme68x` (+IIOT_ENABLE_BOSCH_BME68X=1)

### 2.2 Main Loop (`main.cpp:36-62`)

1. Baca semua sensor → `SensorSample` raw (`main.cpp:40`)
2. `HardwareIntegrityGate::process()` → `HardwareObservation` (`main.cpp:41`)
3. `buildPayload()` → JSON compact_sensor.v3 (`main.cpp:44-45`)
4. Kirim via LoRa E32 (`main.cpp:49-54`)
5. Delay `SAMPLE_INTERVAL_MS=60000` (`config.h:23`, `main.cpp:61`)

**Tidak ada** semantic moving average, LSTM, MQTT, atau AI preprocessing di firmware.

### 2.3 HardwareIntegrityGate (`preprocessing.cpp:30-148`)

Broad hardware-impossibility gate per sensor, bukan project threshold:

| Sensor | Gate range |
|--------|-----------|
| BME688 | -40–85 °C, 0–100 %RH, 300–1250 hPa, 1–1e9 ohm |
| SEN0466 | 0–10000 ppm |
| SEN0574 | 0–3300 mV, ratio 0–100 |
| SEN0321 | 0–100 ppm |
| MH-Z19 | 0–50000 ppm |
| PMS7003T | 0–10000 µg/m³ |
| INA226 | 0–100 V, -100k–100k mA, -10M–10M mW |

Summary: `ok` = all 7 groups valid; `partial` ≥1 valid; `warming` = warm-up pending; `error` = none valid (`preprocessing.cpp:119-144`).

### 2.4 LoRa E32 (`lora_e32.h`, `lora_e32.cpp`)

- UART1, pins TX=16, RX=17, M0=18, M1=19, AUX=20 (`config.h:43-48`)
- AUX handshake, 9600 baud (`config.h:49`)
- TX timeout 5000 ms (`config.h:51`)
- **Status:** Kode tersedia, RF link/soak belum terverifikasi.

---

## 3. Schemas

### 3.1 compact_sensor.v3 — Hardware Observation (Aktif)

- File: `schemas/compact_sensor.v3.schema.json`
- `v=3`, `pp=hardware_only`
- 15 field sensor (`tc,h,p,bme,co,n2mv,n2r,o3,co2,pm1,pm25,pm10,bv,bi,bp`)
- 7 hardware state (`bme688,sen0466,sen0574,sen0321,mhz19,pms7003t,ina226`)
- Metadata: `bid,fw,cfg,cal,hs,f`, `tb` (basis waktu), `radio` opsional
- Missing = `null`, NO₂ bukan ppm
- Build di `payload.cpp:54-74`

### 3.2 compact_sensor.v2 — Legacy Node-Preprocessed (Compatibility)

- `schemas/compact_sensor.v2.schema.json`
- Tetap dibaca sebagai `legacy_node_preprocessed.v2`, tidak difilter ulang

### 3.3 sensor_ai.v1 — AI Event

- `schemas/sensor_ai.v1.schema.json`
- Wajib: `schema_version, event_id, gateway_id, node_id, room_id, timestamp, source, sensor, ai, deployment`
- `ai` block: `env_status, anomaly_score, forecast_status, main_factor, battery_status, node_health, confidence, abstain`
- `event_id` pola `se_[0-9a-f]{32}` — deterministic dari identity hash

### 3.4 sensor_status.v1 — Retained Status

- `schemas/sensor_status.v1.schema.json`
- Wajib: `schema_version, gateway_id, timestamp, status, nodes`
- Health: `healthy, degraded, stale, invalid, offline, unknown`

---

## 4. Preprocessing Pipeline (Gateway Python)

### 4.1 Arsitektur Pipeline

```
PayloadParser → ReadingValidator → GatewaySemanticPreprocessor → resample → features → MinMaxNormalizer → WindowBuilder
```

Layers:
- **L0**: `raw_payloads.jsonl` — raw transport
- **L1**: `hardware_observations.jsonl` — parsed observation
- **L2**: `canonical_observations.jsonl` — validated + semantic preprocessed
- **L3**: `processed_timeseries.jsonl` — resampled
- **L4**: `windows.jsonl` — model window

### 4.2 Parser (`parser.py`)

- `PayloadParser` — menangani v1/v2/v3, identity fallback, deterministic event ID
- V3 memerlukan `pp=hardware_only`, tidak bisa diparse sebagai v2

### 4.3 Validator (`validation.py:52-147`)

- `ReadingValidator` — sequence/duplicate/out-of-order/reboot detection
- State bounded LRU per `(gateway_id, node_id, boot_id)`
- Range validation per field dari config
- Soft issues (missing field, gap, reboot) vs hard invalid

### 4.4 GatewaySemanticPreprocessor (`preprocessing/pipeline.py:11-91`)

- Filter state diisolasi per `(gateway_id, node_id, boot_id, field)` (`pipeline.py:43`)
- Default `apply_to_v2=False` — v2 tidak kena double smoothing
- Filter registry: `none`, `ema`, `median`, `moving_average` (shadow only)

### 4.5 Resampling (`resampling.py:19-82`)

- Bucket per `(gateway_id, node_id, room_id, time_bucket)` (`resampling.py:30-34`)
- Mixed preprocessing version dalam satu bucket = error (`resampling.py:59-64`)

### 4.6 Feature Extraction (`features.py:1-96`)

- 15 canonical sensor fields + 4 legacy reference fields
- Delta (8 fields), rolling mean/std (4 fields × 2), presence flags (15), `missing_count`, `valid_ratio`, `seq_gap_count`
- Total: 45 fitur (`FEATURE_NAMES`)

### 4.7 Normalization (`normalization.py:1-108`)

- `MinMaxNormalizer` — configured min-max dengan observable clipping
- Laporan clipping per field (`normalization.py:86-108`)

### 4.8 Windowing (`windowing.py`)

- Sliding window → `windows.jsonl` (single canonical name)
- Nama lama `lstm_windows.jsonl` hanya reader fallback

---

## 5. MQTT / Security

### 5.1 Topik

| Topik | Schema | Retained | Status |
|-------|--------|----------|--------|
| `iot/{gateway_id}/data` | `sensor_ai.v1` | Tidak | Implemented (`mqtt_contracts.py:31-34`) |
| `iot/{gateway_id}/status/sensor` | `sensor_status.v1` | Ya | Implemented (`mqtt_contracts.py:37-40`) |
| `iot/{gateway_id}/status` | legacy | - | Migration-only |

### 5.2 Security Status

- **TLS:** Belum diimplementasikan (`README.md:281`)
- **Publisher MQTT operasional:** Belum — hanya schema/builder (`README.md:281`)
- **Outbox sensor:** Belum — masih roadmap (`AI_SENSOR.md:269`)
- **LWT sensor:** Belum — masih roadmap
- **ACL/auth:** Belum ditentukan

### 5.3 Event ID

Deterministik dari `gateway_id + node_id + boot_id + sequence + node_timestamp + sensor content` → SHA-256[:32] → `se_` (`contracts.py:73-93`). Retry frame sama → event ID sama → dedupe backend.

---

## 6. Forecasting / Anomaly Models

### 6.1 Forecasting Models

| Model | File | Status |
|-------|------|--------|
| **LastValue** | `forecast_baselines.py:26-29` | Wajib (baseline) |
| **Window Mean** | `forecast_baselines.py:31-35` | Wajib (baseline) |
| **Drift** | `forecast_baselines.py:38-48` | Wajib (baseline) |
| **SeasonalNaive** | `forecast_baselines.py:51-84` | Baseline bila applicable |
| **FITS-inspired** | `edge_forecasting.py:175-204` | **EXPERIMENTAL** — default edge candidate |
| **FITS official-style** | `edge_forecasting.py:205-307` | **EXPERIMENTAL** — research comparator |
| **DLinear** | `edge_forecasting.py:313-351` | **EXPERIMENTAL** — neural sanity baseline |
| **LSTM residual** | `forecasting.py` (legacy), `edge_forecasting.py` | **EXPERIMENTAL** — challenger khusus lane |

### 6.2 Anomaly/Drift Models

| Model | File | Status |
|-------|------|--------|
| **Native RobustZScore + PageHinkley** | `streaming_detection.py:29-92` | **EXPERIMENTAL** — tanpa dependency, input 0-1 wajib |
| **River Half-Space Trees + ADWIN** | `streaming_detection.py:201-234` | **EXPERIMENTAL** — opsional dependency |

### 6.3 Model Methodology (Hard Gate)

1. Baseline dipilih **per target pada validation split** lalu dikunci untuk test (`forecast_baselines.py:88-152`)
2. Duplicate predictions (e.g. SeasonalNaive identik LastValue) dihapus (`forecast_baselines.py:113-129`)
3. Temporal split + purge gap — tidak overlap antar split (`forecasting.py:260-288`)
4. Metrik: MAE, RMSE, MASE, skill score terhadap baseline
5. PROMISING hanya bila: menang baseline test + target non-constant + data quality PASS + schema cocok

### 6.4 Bake-off Results (14 Juli 2026)

| Lane | Best Model | Mean Skill | Status |
|------|-----------|------------|--------|
| Gary (proxy) | LSTM residual | +0.13896 | EXPERIMENTAL — pressure sintetis |
| UCI (proxy) | LSTM residual | +0.37891 | EXPERIMENTAL — proxy outdoor |
| Fidas (PM ref) | FITS-inspired | +0.05722 | EXPERIMENTAL — bukan PMS7003T |
| Sim v3 | FITS-inspired | +0.36261 | EXPERIMENTAL — sintetis bounded |

Sumber: `docs/BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:40-77`
Kesimpulan: FITS-inspired adalah default edge candidate paling konsisten, tetapi **belum ada production winner** (`BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:128`).

### 6.5 Decision Layer

- `decision.py:65-143` — rule-based threshold untuk `env_status`, `battery_status`, `node_health`
- Threshold: `warning_high/critical_high/warning_low/critical_low` per field
- Anomaly threshold: warning=0.7, critical=0.9
- Abstain bila data invalid/stale
- **Komisioning default saja — bukan regulatory limits** (`decision.py:21-22`)
- NO₂ diakui sebagai `ordinal_ratio_only`, bukan ppm (`decision.py:142`)

---

## 7. Tests

| Test File | Coverage |
|-----------|----------|
| `test_sensor_foundation.py` | Contract, time policy, event ID, sequence, validator state bounded, schema validation, safety regression, firmware UART paths, BME profile |
| `test_real_payload_contract.py` | Fixture parsing, validation range/issue, uptime policy |
| `test_real_live_receiver.py` | Receiver replay, accepted/rejected/events, invalid samples |
| `test_gateway_preprocessing_v3.py` | V3 parser, gateway preprocessing, resampling identity isolation, mixed version rejection |
| `test_forecasting.py` | Dataset prep shapes, baseline selection, LSTM train/eval/predict, experiment runner, decision layer |
| `test_dataset_and_edge_models.py` | Dataset adapters (UCI/Bristol/Fidas), streaming anomaly, native pipeline, decision abstain, FITS/DLinear edge train/eval |
| `test_dataset_quality_and_baselines.py` | Cadence inference, declared validation, baseline selection, normalizer clipping |
| `test_anomaly_benchmark.py` | Event interval, benchmark metrics (precision/recall/F1/false-alert/day/delay), synthetic fixture |
| `test_bakeoff_helpers.py` | Bakeoff runner helpers |
| `test_window_artifact_migration.py` | LSTM windows → windows.jsonl migration |
| `test_pipeline.py` | End-to-end pipeline |

---

## 8. Simulations

### 8.1 Simulator (`simulator.py:1-162`)

- `write_simulation()` menghasilkan compact_sensor.v3 sintetis bounded
- 8 skenario: `normal, air_rise, battery_drop, missing_data, sensor_error, node_silent, sequence_gap, mixed`
- Periodic + event pulses + noise — bukan data real
- Warm-up period, protocol error, CO₂ timeout, battery drop

### 8.2 Dataset Lane Policy

Tiga lane terpisah (`docs/data-source-boundary.md:1-48`):
1. **Simulation/Reference** — tidak boleh disebut field measurement
2. **Real Offline Capture** — raw, append-only, tidak dinormalisasi
3. **Real Live Receiver** — runtime, masih skeleton

Dataset publik (UCI, Bristol, Fidas, Gary):
- UCI: `co_mg/m³` bukan `co_ppm`; NO₂ µg/m³ bukan SEN0574 signal
- Bristol: IAQ index bukan `bme_gas_ohm`
- Fidas: PM reference-grade, bukan PMS7003T chip-identical
- Gary: pressure sintetis, CO proxy `co_raw` bukan SEN0466

---

## 9. Limitations — Tidak Bisa Diklaim

### 9.1 Hardware — BELUM TERVERIFIKASI

- **Seluruh jalur hardware belum hardware-verified.** Firmware compile-validated tapi belum pernah dijalankan di board nyata (satu-satunya board adalah ESP32-C6-DevKitC-1 dengan platformio environment `mock`). Sumber: `platformio.ini:1-2` (`default_envs = mock`), `README.md:139` ("Build hanya membuktikan kompilasi profile, bukan pin/rail/sensor/radio/calibration/flash fisik").
- BME688 Bosch SensorAPI compile-validated tetapi **disabled by default** (`config.h:10`, `platformio.ini:34-38`).
- SC16IS752 dual-UART bridge: crystal 1.8432 MHz, address 0x48, 5 V rail, level logic **belum tervalidasi PCB** (`config.h:35-36`).
- INA226 shunt resistor dan calibration register **masih placeholder** (`config.h:65-66`).
- SEN0574 ADC baseline/ratio commissioning **open**.
- Ebyte E32: RF link, range, packet loss, airtime **belum diuji**.
- Power-enable pin **disabled** (nilai -1 di `config.h:70-71`).

### 9.2 Dataset — PROXY, BUKAN FIELD MEASUREMENT

- Tidak ada dataset dari sensor RAB nyata. Semua hasil forecasting dari dataset proxy (Gary, UCI, Fidas, Sim v3).
- Pressure Gary adalah sintetis (`docs/BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:34`).
- UCI CO dalam mg/m³, bukan ppm project (`test_dataset_and_edge_models.py:53-54`).
- Fidas adalah PM reference-grade, bukan PMS7003T (`test_dataset_and_edge_models.py:92-94`).
- Bristol BME680 IAQ index bukan gas resistance (`test_dataset_and_edge_models.py:74-75`).

### 9.3 Model — EXPERIMENTAL

- Semua model berstatus **EXPERIMENTAL** (`docs/BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:3,128`).
- Tidak ada production winner (`BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:128`).
- LSTM belum mengalahkan baseline secara konsisten lintas lane.
- FITS-inspired bukan reproduksi bit-for-bit paper FITS (`edge_forecasting.py:178-180`, `BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:136`).
- `fits_official` memakai label direct t+5 dengan internal pred_len=1 — bukan reproduksi horizon-5 FITS resmi (`BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:136`).
- Hasil laptop CUDA **bukan benchmark Raspberry Pi** (`BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:3`).
- Resource measurement Raspberry Pi **belum ada** (`test_dataset_and_edge_models.py:271`).

### 9.4 MQTT/Backend — BELUM PRODUCTION

- **Tidak ada MQTT publisher operasional** — hanya schema/builder Python (`README.md:281`).
- **Tidak ada broker production**, TLS, ACL, outbox sensor, LWT.
- Receiver live masih skeleton, bukan konfigurasi E32 final (`README.md:121-128`, `docs/real-live-receiver-pipeline.md`).
- Tidak ada backend (FastAPI/Redis/TimescaleDB/Next.js/OpenClaw) di scope repo ini.

### 9.5 Anomaly Detection — SYNTHETIC HARNESS ONLY

- Anomaly fixture synthetic (`test_anomaly_benchmark.py:74` — `status: SYNTHETIC_HARNESS_ONLY`)
- False-alert/day hanya bermakna bila label real/commissioning tersedia
- Streaming detection memerlukan real labeled data

### 9.6 Security

- Tidak ada autentikasi node, enkripsi payload LoRa, atau TLS MQTT.
- `boot_id` dari `esp_random()` — tidak ada secure element/TPM.

---

## 10. What CAN Be Claimed (dengan evidence)

### 10.1 Firmware & Kontrak

- [x] Firmware ESP32-C6 memiliki kode driver untuk **7 sensor RAB** lengkap dengan protocol/checksum/warm-up/vendor compensation
- [x] `compact_sensor.v3` adalah hardware observation dengan `processing_profile=hardware_only`
- [x] Firmware **tidak** menjalankan semantic moving average, LSTM, atau MQTT — sesuai ADR-001
- [x] Schema formal dan compile-validated untuk `compact_sensor.v3`, `sensor_ai.v1`, `sensor_status.v1`

### 10.2 Gateway Preprocessing Pipeline

- [x] Pipeline Python lengkap: parser → validator → semantic preprocessing → resampling → feature extraction → normalization → windowing
- [x] Filter state terisolasi per `(gateway_id, node_id, boot_id, field)`
- [x] Resampling terisolasi per `(gateway_id, node_id, room_id, time_bucket)`
- [x] Mixed preprocessing version dalam bucket = ditolak
- [x] L0–L4 layer file: `raw_payloads.jsonl` sampai `windows.jsonl`

### 10.3 Forecasting Methodology

- [x] Baseline gate wajib: LastValue, window-mean, drift, SeasonalNaive (bila applicable)
- [x] Baseline dipilih per target pada validation split, dikunci untuk test
- [x] Temporal split + purge gap — tidak overlap
- [x] Metrik: MAE, RMSE, MASE, skill score
- [x] Data quality gate: NaN/Inf, constant target, boundary saturation, cadence irregularity
- [x] Repeated-seed CUDA bake-off selesai untuk 4 lane proxy × 4 model × 3 seeds

### 10.4 Anomaly/Drift Harness

- [x] Native RobustZScore + PageHinkley E2E tanpa dependency tambahan
- [x] Score-before-learn, warm-up aware, input [0,1] wajib
- [x] Benchmark: event precision/recall/F1, false-alert/day, detection delay
- [x] River HST + ADWIN sebagai optional challenger

### 10.5 Dataset Policy

- [x] Tiga lane terpisah: simulation/reference, real offline, real live
- [x] Dataset adapter menjaga unit dan provenance — UCI mg/m³ ≠ ppm, Bristol IAQ ≠ gas_ohm, Fidas reference ≠ PMS7003T
- [x] Catalog formal dengan license, quality notes, SHA-256 pinned downloader
- [x] Simulator v3 bounded dengan 8 skenario

### 10.6 Testing

- [x] 12 file test, mencakup kontrak, parser, validasi, preprocessing, resampling, forecasting, anomaly, dataset adapter, edge model
- [x] Safety regression: tidak ada ToF/distance/VL53/SEN0377 di kode sensor

### 10.7 Evidence Boundary — Honest Declaration

- [x] `README.md:306-315` — daftar eksplisit apa yang software/host evidence **tidak** buktikan
- [x] Semua model berstatus `EXPERIMENTAL` — tidak ada klaim produksi
- [x] FITS-inspired dan fits_official-style diberi label adaptation, bukan reproduksi
- [x] Dokumentasi perbedaan `hardware-impossibility gate` vs `project range`

---

## 11. Ringkasan Gap untuk Essay

| Area | Gap | Dampak Essay |
|------|-----|-------------|
| **Hardware** | Semua jalur belum hardware-verified | Tidak bisa klaim "sensor bekerja pada board nyata" |
| **BME688** | Bosch API disabled by default; compile-validated only | Klaim terbatas pada "Bosch SensorAPI terintegrasi dan compile-validated" |
| **INA226** | Shunt/calibration placeholder | Tidak bisa klaim akurasi power measurement |
| **SEN0574** | Baseline/ratio commissioning open | Tidak bisa klaim NO₂ kuantitatif; hanya "ADC mV mentah tersedia" |
| **Dataset** | Semua proxy (Gary/UCI/Fidas/Sim) | Tidak bisa klaim validasi pada data lapangan RAB |
| **Model** | Semua EXPERIMENTAL; belum ada winner | Klaim terbatas pada "metodologi baseline gate siap, model challenger terimplementasi" |
| **FITS** | Bukan reproduksi bit-for-bit paper | Harus disebut "FITS-inspired adaptation untuk single-step horizon" |
| **Raspberry Pi** | Tidak ada benchmark | Tidak bisa klaim latency/RSS/power Pi |
| **MQTT** | Hanya schema/builder, belum publisher | Tidak bisa klaim "MQTT production berjalan" |
| **Security** | Tidak ada auth/encrypt | Hanya bisa klaim "event ID deterministic untuk dedupe" |
| **Anomaly** | Hanya synthetic harness | Tidak bisa klaim false-alert rate real |
| **Real Receiver** | Skeleton, bukan final E32 config | Tidak bisa klaim "receiver live beroperasi dengan LoRa" |

---

## 12. Evidence File Index

| Evidence | File:Line |
|----------|-----------|
| Sensor fields definition | `sensor_types.h:18-37` |
| HardwareIntegrityGate ranges | `preprocessing.cpp:36-117` |
| Payload build compact_v3 | `payload.cpp:31-80` |
| Main loop (no AI) | `main.cpp:36-62` |
| Mock sensors default | `platformio.ini:17-18` |
| BME68X disabled by default | `config.h:9-11` |
| INA226 calibration placeholder | `config.h:65-66` |
| All models EXPERIMENTAL | `BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:3` |
| No production winner | `BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:128` |
| FITS-inspired (bukan bit-for-bit) | `edge_forecasting.py:178-180` |
| fits_official single-step adaptation | `BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md:136` |
| Baseline selection per-target on validation | `forecast_baselines.py:88-152` |
| Temporal split non-overlap | `forecasting.py:260-288` |
| Anomaly fixture synthetic | `test_anomaly_benchmark.py:74` |
| MQTT hanya schema/builder | `README.md:281` |
| Evidence boundary declaration | `README.md:306-315` |
| Dataset lane separation | `docs/data-source-boundary.md:1-48` |
| UCI adapter menjaga unit | `test_dataset_and_edge_models.py:53-54` |
| No ToF/distance/SEN0377 guarantee | `test_sensor_foundation.py:195-209` |
| ADR-001 preprocessing split | `docs/adr/ADR-001-gateway-centric-sensor-preprocessing.md:24-47` |
| Raspberry Pi resource claim = None | `test_dataset_and_edge_models.py:271` |
