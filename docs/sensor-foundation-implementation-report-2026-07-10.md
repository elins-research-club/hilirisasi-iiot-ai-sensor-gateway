# Laporan Implementasi Sensor Node dan AI Gateway — 2026-07-10

> **HISTORICAL V2 FOUNDATION SNAPSHOT — SUPERSEDED 11 JULI 2026.** Current source memakai `compact_sensor.v3` hardware observation, firmware tanpa semantic moving average, dan gateway-centric semantic preprocessing. Gunakan ADR-001 dan `gateway-centric-sensor-preprocessing-migration-report-2026-07-11.md` untuk current state. Isi v2 di bawah dipertahankan sebagai audit trail.

## Ringkasan

Wave ini mengubah sensor gateway dari skeleton v1 menjadi fondasi `compact_sensor.v2` yang host-verified: receiver append-only, parser/validator fail-closed, waktu authoritative di gateway, full field RAB, firmware mock/hardware terpisah, dataset adapters ber-provenance, forecasting baseline/challenger, streaming anomaly/drift, decision layer, serta schema MQTT.

Status akhir adalah **software/compile foundation selesai**, bukan hardware-ready atau model production. Semua klaim sensor, daya, radio, akurasi, false-alert, dan performa Raspberry Pi tetap menunggu pengukuran fisik/data real.

## A0 — Safety

- LSTM dan edge checkpoint dimuat dengan `torch.load(..., weights_only=True)`;
- struktur checkpoint, key, dan strict state-dict divalidasi;
- receiver raw/accepted/rejected/events append-only dan atomic rotate;
- restart receiver tidak menghapus log lama;
- serial source memakai idle sleep dan exponential reconnect backoff;
- uptime firmware tidak dianggap Unix epoch;
- gateway selalu membuat `receive_timestamp` dan `time_quality`;
- mock BME gas konsisten dengan range validation/normalization;
- regression tests mencakup seluruh gate tersebut.

## A1 — Contract Freeze

Compact v2 fields:

```text
tc,h,p,bme,co,n2mv,n2r,o3,co2,pm1,pm25,pm10,bv,bi,bp
```

Keputusan kontrak:

- `co` = SEN0466 ppm;
- `n2mv`/`n2r` = NO₂ kualitatif, bukan ppm;
- missing = `null`, bukan nol;
- `boot_id + sequence` membentuk identitas reboot-safe;
- `event_id` deterministik untuk retry;
- schema version/flags/metadata radio divalidasi;
- duplicate dan out-of-order ditolak;
- `sensor_ai.v1` dan `sensor_status.v1` tersedia;
- topic target: `iot/{gateway_id}/data` dan `iot/{gateway_id}/status/sensor`;
- bare status hanya migration-only.

Publisher MQTT production, outbox, TLS/ACL, dan LWT runtime belum diimplementasikan di wave ini.

## A2/C1 — Firmware

### Mock dan payload

- profile `mock` default ON;
- seluruh field RAB memiliki nilai simulasi valid;
- partial validity per sensor;
- warm-up/error menjadi flags dan `null`;
- LoRa payload v2 memakai boot ID, sequence, uptime, quality, dan sensor health.

### Jalur hardware compile-tested

- SEN0466 I²C: request/response DFRobot, checksum, gas type, decimal scale, range gate;
- SEN0574: ADC oneshot + curve-fitting mV; ratio tetap unavailable sampai baseline;
- SEN0321: automatic mode/read dan ppb→ppm;
- INA226: calibration register dan bus/current/power register;
- E32: UART, AUX wait, timeout, dan error handling;
- SC16IS752: dual-UART I²C, channel select, divisor, FIFO level, bounded read/write;
- MH-Z19B/C: Winsen request/response, checksum, timeout, range gate, warm-up 180 s;
- PMS7003T: wake, passive mode/read, 32-byte frame, header/length/checksum, atmospheric PM, warm-up 30 s;
- power-enable hook tersedia; pin masih `-1` sampai PCB final;
- flash profile `mock=8 MB`, `hardware=16 MB`.

### BME688

Adapter official Bosch BME68x SensorAPI forced-mode tersedia, termasuk oversampling, heater, measurement delay, new-data/gas-valid/heater-stable gate, dan unit conversion. Dependency source resmi tidak dipasang otomatis. Default `hardware` menonaktifkan lane BME688; profile `hardware-bme68x` fail-fast bila header resmi belum ada.

Perintah dependency untuk user:

```bash
cd firmware/esp32-c6-sensor-node
mkdir -p lib
git clone --depth 1 https://github.com/boschsensortec/BME68x_SensorAPI.git lib/BME68x_SensorAPI
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware-bme68x
```

Perintah ini hanya didokumentasikan dan tidak dijalankan oleh agent.

## A3 — Pipeline dan Ops

- raw envelope menyimpan raw line, receive time, source/radio, dan parse status;
- accepted/rejected/events tidak truncate saat restart;
- systemd memakai receiver live kontinu dan `Restart=on-failure`;
- `min_valid_ratio` digunakan sebelum windowing;
- `node_silent_after_sec` digunakan pada node-health/status;
- CLI no-args dan angka invalid fail-fast;
- dataset downloader memiliki allow-list, size gate, optional SHA256, atomic rename, `--describe-only`, dan `--list`;
- config/schema/tests/docs disinkronkan.

## C2 — Dataset

Strategi tetap adapter-per-lane, bukan fusion horizontal yang mengarang field.

| Dataset | Lane | Keputusan |
|---|---|---|
| UCI Air Quality | air reference/regression | T/RH canonical; gas reference mempertahankan unit sumber; `-200`→`null` |
| Bristol BME680 | indoor BME-like | T/H/P canonical; IAQ tidak diubah menjadi gas ohm/CO₂ |
| Zenodo 7198378 Fidas 200S | PM reference | PM tetap `reference.*`; PMS7003T project fields `null` |
| SensEURCity 17858205 | multi-city research | manual-only karena besar; leave-site/city research, bukan chip-identical |
| Gary Stafford | regression compatibility | bukan main train dan bukan bukti sensor RAB |
| project real capture | deployment lane | satu-satunya lane untuk klaim perangkat proyek |

Tidak ada blob dataset yang dimasukkan ke Git. Untuk verifikasi nyata, UCI Air Quality dan Zenodo 7198378 diunduh ke `/tmp`, SHA-256 artifact dikunci di catalog, lalu adapter dijalankan pada file resmi.

Bukti dataset resmi:

```text
UCI archive: 1,543,989 bytes, SHA-256 d4a64013...b2b8a2a
UCI canonical: 9,357 rows, 0 rejected
Zenodo Fidas CSV: 19,506,656 bytes, SHA-256 c4f8f56f...6fdbe3eb
Zenodo canonical: 224,810 rows, 0 rejected
```

Artifact hanya berada di `/tmp/iiot-public-datasets` dan tidak masuk working tree.

## C3 — Model dan Decision

Semua model masih **EXPERIMENTAL**.

### Forecast gate

- LastValue dan SeasonalNaive: baseline wajib;
- DLinear: lightweight neural baseline;
- LSTM: comparator existing dengan safe load;
- FITS-inspired: challenger frekuensi ringan, bukan reproduksi bit-for-bit paper;
- metrics: MAE, RMSE, MASE, per-target, dan skill terhadap baseline;
- model tidak dipromosikan bila kalah baseline.

### Streaming anomaly/drift

- native RobustZScore + PageHinkley: E2E tanpa dependency tambahan;
- River Half-Space Trees + ADWIN: optional challenger; smoke aktual lulus di venv temporer terisolasi (20 processed, 0 rejected), tetapi benchmark data nyata belum ada;
- input wajib finite dan normalized 0–1;
- score-before-learn dan warm-up wajib;
- invalid record ditolak;
- smoke wiring River diklaim, bukan performa/akurasi River.

### Decision layer

Urutan `quality/rules → baseline/anomaly/drift → forecast`. Output berisi `env_status`, `main_factor`, `battery_status`, `node_health`, `confidence`, dan `abstain`. Data invalid/stale menghasilkan `unknown + abstain`, bukan normal palsu. NO₂ tetap ordinal/ratio only.

## Bukti End-to-End Host

Smoke pipeline dijalankan di `/tmp`, bukan artifact repo:

```text
240 compact v2 payloads
→ 229 windows
→ split train/val/test = 156/22/14 (purge gap 5 langkah)
→ FITS train/eval/predict = selesai, 3 predictions
→ DLinear train/eval/predict = selesai, 3 predictions
→ LSTM train/eval/predict = PASS, 3 predictions
→ native RobustZScore+PageHinkley = 20 processed, 0 rejected
```

Hasil smoke hanya membuktikan wiring pipeline. Karena datanya simulasi dan training satu epoch, hasil tidak dipakai sebagai benchmark kualitas model.

Receiver replay dijalankan dua kali pada folder yang sama:

```text
accepted 4 → 8
rejected 4 → 8
events   2 → 4
raw      8 → 16
```

Ini membuktikan restart tidak wipe log.

## Verifikasi Final

- Python compileall: PASS;
- unit tests: **51/51 PASS**;
- `check-config`: PASS;
- schema/catalog/CLI: PASS;
- simulator→pipeline→model→decision smoke: PASS;
- PlatformIO `mock`: SUCCESS;
- PlatformIO `hardware`: SUCCESS;
- PlatformIO `hardware-bme68x`: SUCCESS dengan official Bosch SensorAPI commit `80ea120a8b8ac987d7d79eb68a9ed796736be845`; profile tetap fail-fast bila dependency hilang;
- active sensor source guard untuk distance/ToF dan modul legacy: 0 match;
- `git diff --check`: PASS, selain warning line ending file lama bila muncul.

## Residual Hardware/Data/Produksi

- pembacaan BME688 nyata; source resmi dan adapter baru compile-validated;
- board ESP32-C6 16 MB aktual;
- address/pull-up/noise I²C;
- SC16IS752 address, crystal, level logic, channel wiring;
- rail 5 V dan peak current MH-Z19/PMS;
- response nyata SEN0466/SEN0321/MH-Z19/PMS;
- baseline NO₂, ADC input protection, dan compensation;
- shunt/current-LSB INA226;
- E32 packet loss/recovery;
- thermal/current/battery/soak 24–72 jam;
- real project dataset dan leave-device/site evaluation;
- River benchmark pada data real dan false-alert evaluation;
- false-alert/day dan drift delay;
- Raspberry Pi latency/RSS/power;
- MQTT TLS/ACL/outbox/LWT end-to-end.
