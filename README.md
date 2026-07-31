# IIoT AI Sensor Gateway

Repo ini menghubungkan node sensor ESP32-C6 dengan preprocessing semantik dan pipeline AI di Raspberry Pi.

```text
sensor RAB
→ ESP32-C6: acquisition + protocol/hardware integrity + engineering units
→ compact_sensor.v3 hardware observation melalui Ebyte E32
→ Raspberry Pi: raw durability + parser + semantic preprocessing
→ resampling + features + observable normalization + windowing
→ baseline/model/anomaly/drift/decision
→ sensor_ai.v1 dan sensor_status.v1 untuk MQTT/backend
```

Python tidak berjalan di ESP32-C6. Firmware berada di `firmware/esp32-c6-sensor-node`; gateway Python berada di `src/iiot_ai_sensor_gateway`.

## Status Arsitektur

Keputusan aktif adalah **Gateway-Centric Semantic Preprocessing**:

- ESP32-C6 memiliki driver, checksum/frame validation, warm-up, vendor compensation, ADC/register conversion, hardware state, broad impossibility gate, boot/sequence/version metadata, dan transport;
- Raspberry Pi memiliki project validation, event-time/order, filter per sensor, resampling, missing policy, features, normalization, windowing, model, dan decision;
- firmware aktif tidak memiliki semantic moving average;
- `compact_sensor.v3` adalah hardware observation;
- `compact_sensor.v2` tetap dibaca sebagai legacy node-preprocessed observation dan tidak difilter lagi secara default;
- v1 hanya migration/reference.

ADR: `docs/adr/ADR-001-gateway-centric-sensor-preprocessing.md`.

## Hardware Target

| Perangkat | Field | Status jujur |
|---|---|---|
| BME688 | temperatur, RH, tekanan, gas resistance | adapter Bosch forced-mode tersedia dan compile-validated; hardware open |
| SEN0466 | CO ppm | I²C/checksum/scaling/range gate tersedia; hardware open |
| SEN0574 | NO₂ mV + rasio kualitatif | ADC mV tersedia; baseline/ratio commissioning open; bukan ppm |
| SEN0321 | O₃ ppm | I²C automatic-read tersedia; hardware open |
| MH-Z19B/C | CO₂ ppm | SC16IS752 A, Winsen checksum, timeout, 180 s warm-up; hardware open |
| PMS7003T | PM1/PM2.5/PM10 | SC16IS752 B, wake/passive/checksum, 30 s warm-up; hardware open |
| INA226 | V/mA/mW | register/calibration path tersedia; shunt/current-LSB open |
| Ebyte E32 | LoRa transport | UART/AUX/error handling tersedia; RF link/soak open |

Tidak ada ToF/distance/VL53 pada node sensor. Presence sensor hanya milik hybrid camera.

## Contract `compact_sensor.v3`

Top-level wajib:

```text
v,n,r,ts,tb,seq,bid,pp,fw,cfg,cal,hs,f,ok,s
```

Aturan:

- `v=3`;
- `pp=hardware_only`;
- `tb` menyatakan basis waktu (`uptime_s`, epoch, atau RFC3339);
- `fw`, `cfg`, dan `cal` membawa provenance;
- `hs` adalah summary hardware, bukan final semantic quality;
- `ok` berisi state hardware per sensor;
- missing = `null`, bukan nol;
- NO₂ tetap `n2mv`/`n2r`, bukan ppm;
- retry mempertahankan deterministic `event_id`;
- unknown version/profile, NaN/Inf, identity invalid, duplicate, dan out-of-order fail-closed.

Field `s`:

```text
tc,h,p,bme,co,n2mv,n2r,o3,co2,pm1,pm25,pm10,bv,bi,bp
```

Schema:

- `schemas/compact_sensor.v3.schema.json` — aktif;
- `schemas/compact_sensor.v2.schema.json` — compatibility;
- `schemas/sensor_ai.v1.schema.json`;
- `schemas/sensor_status.v1.schema.json`.

Detail: `docs/data-contract.md`.

## Data Layer dan Replay

CLI `run` menghasilkan layer terpisah:

```text
raw_payloads.jsonl               L0 raw transport
hardware_observations.jsonl      L1 parsed hardware observation
canonical_observations.jsonl     L2 validation + semantic preprocessing
processed_timeseries.jsonl       L3 resampled, source_event_ids retained
windows.jsonl                    L4 model window
normalization_report.json        clipping/out-of-range telemetry
```

`windows.jsonl` adalah satu-satunya artifact window yang ditulis pipeline. Reader masih menerima nama lama `lstm_windows.jsonl` sebagai fallback migration bila file yang diminta tidak ada, tetapi pipeline tidak lagi membuat salinan kedua.

Filter gateway diisolasi oleh `(gateway_id,node_id,boot_id,field)`. Resampling diisolasi oleh `(gateway_id,node_id,room_id,time_bucket)`. Bucket dengan preprocessing version berbeda ditolak.

Default filter adalah `none`; tersedia `ema`, `median`, dan `moving_average` untuk compatibility/shadow study. Filter tidak boleh ditentukan tanpa data hardware nyata.

## Receiver Real-Live

Receiver tetap append-only dan memisahkan:

```text
raw_envelopes.jsonl
accepted_payloads.jsonl
rejected_payloads.jsonl
receiver_events.jsonl
```

Replay:

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY run_gateway.py receive-real-live \
  --replay-file tests/fixtures/real_payload_samples.jsonl \
  --output-dir data/real_live_logs \
  --max-messages 8
```

Serial opsional:

```bash
python -m pip install -e '.[serial]'
$PY run_gateway.py receive-real-live \
  --port /dev/ttyUSB0 --baudrate 9600 --timeout 1.0 \
  --output-dir data/real_live_logs
```

## Firmware ESP32-C6

```bash
cd firmware/esp32-c6-sensor-node
/home/ubuntu/.venvs/platformio/bin/pio run -e mock
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware-bme68x
```

`hardware-bme68x` fail-fast bila Bosch SensorAPI resmi tidak tersedia. Build hanya membuktikan kompilasi profile, bukan pin/rail/sensor/radio/calibration/flash fisik.

Detail: `firmware/esp32-c6-sensor-node/README.md`.

## Dataset Policy

Lane tidak boleh difusi sebagai satu raw corpus tanpa metodologi:

1. simulation/reference;
2. raw real offline capture;
3. real live receiver.

Catalog: `datasets/catalog.json`.

- UCI: temperatur/RH canonical; CO mg/m³ dan NO₂ µg/m³ tetap reference units;
- Bristol BME680: IAQ index bukan gas resistance/CO₂;
- Fidas: PM reference bukan PMS7003T chip-identical;
- Gary: regression/compatibility saja;
- real RAB capture adalah satu-satunya lane untuk deployment claim.

Download filename dan SHA-256 dikunci di catalog. Script tidak menebak file berdasarkan ukuran:

```bash
$PY scripts/download_dataset.py --list
$PY scripts/download_dataset.py uci_air_quality_360 --describe-only
$PY scripts/download_dataset.py uci_air_quality_360 \
  --output-dir data/external/downloads --max-bytes 52428800
```

## Forecast Dataset Quality

`prepare-forecast-dataset` sekarang:

- menginfer cadence dari timestamp per node, atau memvalidasi cadence deklaratif;
- membedakan gap integer-multiple dari irregular cadence;
- menyimpan horizon steps **dan** horizon duration;
- memakai time-ordered split + purge/no-overlap assertions;
- memilih active feature schema dari train only;
- menyimpan ordered feature manifest dan SHA-256;
- mendeteksi target constant/near-constant;
- melaporkan boundary saturation/clipping dan effective target count;
- fail-closed bila schema target tidak tersedia.

```bash
$PY run_gateway.py prepare-forecast-dataset \
  --windows data/processed/windows.jsonl \
  --output-npz data/modeling/forecast.npz \
  --output-meta data/modeling/forecast-meta.json \
  --horizon-steps 5 \
  --cadence-sec 0
```

`--cadence-sec 0` berarti infer timestamp. Nilai positif divalidasi terhadap timestamp, bukan dipercaya buta.

## Baseline dan Model Evaluation

Model tetap **EXPERIMENTAL**:

- LastValue;
- window mean;
- drift;
- SeasonalNaive hanya bila period/window/horizon applicable dan tidak identik dengan LastValue;
- DLinear;
- FITS-inspired;
- FITS official-style path;
- LSTM residual;
- native RobustZScore + PageHinkley;
- optional River HST + ADWIN.

Baseline dipilih **per target pada validation split**, lalu dikunci untuk test. Test set tidak dipakai memilih pembanding. Kandidat baseline dengan prediction identik tidak dihitung dua kali.

`PROMISING` hanya mungkin bila:

- model mengalahkan validation-selected baseline pada test;
- target coverage/effective targets cukup;
- target tidak constant/saturated;
- data-quality gate `PASS`;
- schema checkpoint cocok.

Tidak ada model production winner tanpa repeated real-RAB evaluation dan benchmark Raspberry Pi aktual.

## Anomaly/Drift Harness

Streaming detector tetap score-before-learn, warm-up aware, dan input 0–1. Harness event-level tersedia:

```bash
$PY run_gateway.py inject-anomaly-fixture \
  --output data/modeling/anomaly_fixture.jsonl \
  --labels data/modeling/anomaly_fixture_labels.json

$PY run_gateway.py stream-detect \
  --backend native \
  --input data/modeling/anomaly_fixture.jsonl \
  --output data/modeling/anomaly_detections.jsonl \
  --feature-names temperature_c,pm25_ug_m3

$PY run_gateway.py benchmark-anomaly-events \
  --detections data/modeling/anomaly_detections.jsonl \
  --labels data/modeling/anomaly_fixture_labels.json \
  --output data/modeling/anomaly_benchmark.json \
  --merge-gap-sec 60 --match-tolerance-sec 120
```

Output: event precision/recall/F1, false-alert/day, detection delay, unmatched events. Fixture synthetic hanya memverifikasi harness, bukan accuracy field.

## Laptop CUDA Bake-off

Satu runner cross-platform menggantikan orchestration duplikat:

```bash
python scripts/laptop_bakeoff_runner.py \
  --device cuda \
  --seeds 42,43,44 \
  --lanes gary,uci,fidas,sim
```

Windows:

```powershell
py -3.13 scripts\laptop_bakeoff_runner.py --python py --device cuda
```

Properties:

- atomic `models/bakeoff/state/RUN_STATE.json`;
- command + source fingerprint;
- resume hanya bila output dan fingerprint cocok;
- per-seed model directory;
- exact dataset filename/checksum;
- data-quality FAIL memblokir training lane;
- repeated-seed mean/std summary;
- tidak mengklaim hasil laptop sebagai Raspberry Pi.

Wrappers `.sh`, `.ps1`, dan `.cmd` hanya meneruskan ke runner Python.

## V3D Real-Data Benchmark

Branch riset V3D menambahkan benchmark terisolasi untuk tiga dataset nyata: UCI Air Quality, Beijing Multi-Site Air Quality, dan Intel Berkeley Lab Sensor Data. Benchmark memakai split kronologis, normalisasi train-only, baseline selection pada validation, lima seed, serta pengukuran resource pada mesin aktual.

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
PYTHONPATH=src $PY scripts/v3d_real_data_benchmark.py prepare \
  --datasets uci,beijing,intel
PYTHONPATH=src $PY scripts/v3d_real_data_benchmark.py benchmark \
  --datasets uci,beijing,intel \
  --models dlinear,fits,lstm,patchtst \
  --seeds 42,43,44,45,46 \
  --epochs 12 --patience 3 --batch-size 256
$PY scripts/v3d_analysis_pack.py
```

PatchTST yang diuji merupakan adaptasi project kompak, bukan reproduksi bit-for-bit kode resmi. Detail protokol, checksum, hasil 60 run, dan evidence boundary tersedia di `docs/v3d-real-data-benchmark.md`.

## MQTT Contract

```text
iot/{gateway_id}/data
iot/{gateway_id}/status/sensor
```

Data event non-retained; status retained. Bare status migration-only. Repo menyediakan schema/builder, belum broker/publisher/outbox/TLS/LWT sensor production E2E.

## Verifikasi

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
export PYTHONPATH=src
$PY -m compileall -q src tests scripts run_gateway.py
$PY -m unittest discover -s tests -p 'test_*.py' -q
$PY run_gateway.py check-config --config config/default.toml
git diff --check
```

Firmware:

```bash
cd firmware/esp32-c6-sensor-node
/home/ubuntu/.venvs/platformio/bin/pio run -e mock
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware-bme68x
```

## Evidence Boundary

Software/host evidence tidak membuktikan:

- sensor/ADC/bridge/E32/rail nyata;
- calibration/reference accuracy;
- packet loss/range;
- battery/power/thermal;
- MQTT broker production;
- Raspberry Pi latency/RSS/power;
- false-alert/day pada data field berlabel;
- 24–72 jam soak.

Lihat:

- `docs/adr/ADR-001-gateway-centric-sensor-preprocessing.md`
- `docs/gateway-centric-sensor-preprocessing-migration-report-2026-07-11.md`
- `docs/data-contract.md`
- `docs/architecture-preprocessing-split.md`
- `docs/LAPTOP_CUDA_BAKEOFF.md`
- `docs/MODEL_COMPARISON.md`
- `docs/BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md`
