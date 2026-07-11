# Laporan Implementasi Gateway-Centric Sensor Preprocessing dan Model Methodology Hardening

**Tanggal:** 11 Juli 2026  
**Repo:** `/home/ubuntu/projects/iiot-project/iiot-ai-sensor-gateway`  
**Baseline Git:** `5e096bc` + working tree user/Hermes yang sudah dirty  
**Commit/push:** tidak dilakukan  
**Evidence level:** source-reviewed, host-tested, firmware compile-validated; hardware/broker/Pi/field masih open

## 1. Tujuan Terpadu

Tiga mandat digabung tanpa overlap:

1. rekonsiliasi handoff IIoT/Hermes dan current source;
2. migrasi semantic/AI preprocessing dari node sensor ke gateway;
3. audit/optimasi dataset, forecasting, anomaly/drift, evaluator, dan laptop pack.

Dependency order yang dipakai:

```text
contract/responsibility boundary
→ v3 parser/schema/provenance
→ gateway preprocessing + state isolation
→ firmware hardware-only
→ L0–L4 replay
→ dataset cadence/schema/quality gates
→ fair baseline/evaluator
→ anomaly benchmark
→ cross-platform runner
→ docs/Project Context
→ full verification
```

Model optimization tidak dilakukan sebelum input semantics dan evaluator dibekukan.

## 2. Temuan Prioritas

### P0 — Cross-node contamination pada resampling

Sebelumnya time bucket hanya keyed timestamp. Batch multi-node dapat dirata-ratakan bersama dan identity diambil dari record pertama.

Perbaikan:

- key `(gateway_id,node_id,room_id,bucket)`;
- source event IDs retained;
- mixed preprocessing version bucket rejected;
- regression test dua node pada timestamp sama.

### P0 — Identity state hanya `node_id`

Validator, buffer, processed-feature dedupe, dan window builder menggunakan `node_id` saja. Node ID sama pada gateway berbeda atau room change dapat mencemari sequence/window.

Perbaikan:

- validation sequence `(gateway,node,boot)`;
- validation last boot `(gateway,node)`;
- buffer/window `(gateway,node,room)`;
- feature dedupe `(gateway,node,room,timestamp)`;
- regression test lintas gateway.

### P0 — Semantic preprocessing ganda dan raw observation hilang

Firmware moving-average lima sampel lalu gateway melakukan bucket average. Distribution, latency, debugging, replay, dan ownership tidak bersih.

Perbaikan:

- ADR gateway-centric;
- firmware `HardwareIntegrityGate` tanpa semantic smoothing;
- `compact_sensor.v3` hardware observation;
- v2 tetap compatibility dan bypass filter default;
- gateway per-field versioned filter state.

### P1 — Cadence/horizon metadata tidak jujur

Horizon steps pernah otomatis dilabeli menit dari hardcoded 60 detik, padahal lane dapat hourly/2-minute.

Perbaikan:

- robust per-node timestamp cadence inference;
- declared cadence divalidasi terhadap timestamp;
- integer-multiple missing gap dibedakan dari irregular cadence;
- horizon steps + duration disimpan;
- irregular fraction gate.

### P1 — Baseline selection leakage dan baseline duplicate

Evaluator memilih best baseline dari test. `seasonal_period=0` membuat SeasonalNaive identik LastValue tetapi tetap tampak sebagai baseline lain.

Perbaikan:

- shared baseline suite;
- validation-selected baseline per target;
- selection locked untuk test;
- duplicate prediction exclusion;
- SeasonalNaive applicability/fallback reason;
- window mean dan drift baseline tambahan.

### P1 — Target degenerate dan simulator saturation

Simulator lama dapat naik tanpa recovery sampai normalized target konstan di 1.0. Model gate dapat terlihat lulus dengan effective target lebih sedikit.

Perbaikan:

- bounded rise/recovery simulator v3;
- warm-up/dropout/error/battery regimes;
- constant/near-constant detector per split;
- boundary-saturation gate;
- effective target count dan blocked reasons;
- readiness tetap EXPERIMENTAL bila quality gate gagal.

### P1 — Sparse 53-feature schema tanpa deployment manifest

Public lane mengisi banyak feature konstan. Checkpoint tidak memiliki exact active schema yang cukup kuat.

Perbaikan:

- train-only active feature selection;
- target tetap dipertahankan walau konstan agar gate dapat melaporkan masalah;
- ordered feature manifest + SHA-256;
- checkpoint/data schema compatibility;
- live LSTM window boleh superset tetapi required trained features wajib tersedia.

### P1 — Clipping tidak terlihat

Physical min-max clipping dapat menyembunyikan out-of-domain public reference values.

Perbaikan:

- normalization lower/upper clip count per field;
- aggregate clip fraction;
- NPZ data-quality boundary fraction per split/target;
- promotion blocked bila saturation melampaui gate;
- raw/reference units tetap preserved.

### P1 — Laptop scripts duplikat dan resume rapuh

PowerShell/shell/manual resume memiliki drift, quoting risk, file selection berdasarkan ukuran, dan no source-aware resume.

Perbaikan:

- canonical Python state-machine runner;
- atomic state;
- command+source fingerprint;
- output-aware resume;
- exact download filename/checksum;
- exact UCI archive member;
- per-seed directories;
- repeated-seed aggregate;
- data-quality lane blocking;
- thin wrappers Windows/Linux.

### P2 — Anomaly claims tanpa event harness

Streaming smoke hanya menunjukkan processed/anomaly count, belum event precision/recall/false-alert/day/delay.

Perbaikan:

- timezone-safe event labels;
- contiguous detection interval collapse;
- one-to-one event matching;
- precision/recall/F1;
- false-alert/day;
- detection delay;
- deterministic synthetic injection fixture berlabel harness-only.

## 3. Arsitektur Implementasi

### Node

```text
SensorReader
→ protocol/checksum/warm-up/vendor compensation
→ HardwareIntegrityGate
→ HardwareObservation
→ compact_sensor.v3
→ E32
```

Node tidak memiliki semantic moving average, resampling, normalization, feature, model, atau final decision.

### Gateway

```text
L0 raw payload
→ v1/v2/v3 parser
→ L1 hardware observation
→ identity/time/order validation
→ versioned per-field semantic filter
→ L2 canonical observation
→ identity-isolated resampling
→ L3 processed timeseries + provenance
→ features + clipping-observable normalization
→ active schema/window
→ L4 baseline/model/anomaly/drift/decision
```

## 4. Contract v3

Required:

```text
v,n,r,ts,tb,seq,bid,pp,fw,cfg,cal,hs,f,ok,s
```

Hard rules:

- `v=3`;
- `pp=hardware_only`;
- non-empty firmware/config/calibration version;
- hardware state explicit;
- missing null;
- NO₂ mV/ratio only;
- no ToF/distance;
- unknown profile/version fail-closed.

Schema: `schemas/compact_sensor.v3.schema.json`.

## 5. Data Layers dan Provenance

CLI replay outputs:

```text
raw_payloads.jsonl
hardware_observations.jsonl
canonical_observations.jsonl
processed_timeseries.jsonl
windows.jsonl
lstm_windows.jsonl
normalization_report.json
```

L3 menyimpan source-event IDs dan preprocessing version. V2 diberi `legacy_node_preprocessed.v2`; v3 menggunakan config version seperti `gateway_preprocess.v1`.

## 6. Dataset/Forecast Methodology

Forecast NPZ/meta sekarang menyimpan:

- cadence diagnostics;
- cadence seconds;
- horizon steps/duration;
- split time ranges;
- active feature names;
- dropped train-constant features;
- schema SHA-256;
- target quality report;
- effective target names/count;
- clipping/boundary saturation;
- normalization ranges;
- purge gap.

Baseline selection:

```text
fit/train model
select applicable independent baseline per target on validation
freeze selection
compare model vs selected baseline on test
apply data-quality gate
```

Test tidak memilih baseline.

## 7. Anomaly/Drift

Native/River detector contract tetap:

- finite normalized input;
- score-before-learn;
- warm-up;
- drift terpisah dari environmental danger;
- optional dependency fail clearly.

Harness baru mengukur event-level metrics bila label tersedia. Synthetic fixture tidak menjadi field accuracy evidence.

## 8. Laptop Runner

Canonical:

```text
scripts/laptop_bakeoff_runner.py
```

Default 3 seeds dan per-seed output. Runner dapat dry-run, resume, block quality-failed lane, menyiapkan exact public artifacts, dan menulis summary atomik.

Historical one-seed CUDA ranking belum diulang; seluruh winner claim tetap dibekukan.

## 9. File Utama Berubah/Dibuat

### Contract/preprocessing

- `schemas/compact_sensor.v3.schema.json`
- `contracts.py`
- `parser.py`
- `validation.py`
- `buffer.py`
- `resampling.py`
- `windowing.py`
- `pipeline.py`
- `preprocessing/filters.py`
- `preprocessing/pipeline.py`
- `config.py`
- `config/default.toml`

### Firmware

- `sensor_types.h`
- `preprocessing.h/.cpp`
- `payload.h/.cpp`
- `main.cpp`
- `include/config.h`

### Dataset/model/evaluation

- `simulator.py`
- `dataset_quality.py`
- `forecast_baselines.py`
- `forecasting.py`
- `edge_forecasting.py`
- `normalization.py`
- `anomaly_benchmark.py`
- `cli.py`

### Laptop/data tooling

- `scripts/laptop_bakeoff_runner.py`
- thin `.ps1/.cmd/.sh` wrappers
- `scripts/prepare_lane_forecast.py`
- `scripts/summarize_bakeoff.py`
- `scripts/download_dataset.py`
- `datasets/catalog.json`

### Regression tests

- `test_gateway_preprocessing_v3.py`
- `test_dataset_quality_and_baselines.py`
- `test_anomaly_benchmark.py`
- expanded `test_bakeoff_helpers.py`

### Documentation

- ADR-001;
- README/AGENTS/firmware README;
- data contract/architecture/shared schema/receiver/testing/simulation;
- model/bakeoff docs;
- Project Context current-state sync.

## 10. Verification Evidence

Final verification commands/results:

- final full Python suite: 80 PASS, termasuk identity/preprocessing, dataset/baseline, anomaly benchmark, runner helpers, dan pipeline regression;
- compileall: PASS;
- config: PASS;
- 4 JSON schemas: PASS;
- legacy semantic-preprocess guard: PASS;
- forbidden ToF/SEN0377 guard: PASS;
- `git diff --check`: PASS with non-fatal existing CRLF notice on `.gitignore`;
- PlatformIO `mock`: SUCCESS;
- PlatformIO `hardware`: SUCCESS;
- PlatformIO `hardware-bme68x`: SUCCESS;
- v3 replay: 480 payload, 480 processed points, 458 windows, source provenance present, clipping fraction 0 on smoke;
- laptop runner dry-run: 11 steps, seluruhnya `dry_run`, termasuk mandatory simulator regeneration step.

## 11. Tidak Diverifikasi

- flash/boot board fisik;
- I²C/ADC/SC16IS752/sensor responses;
- E32 RF/channel/range/packet loss;
- calibration/reference accuracy;
- 5 V rail/current/thermal;
- real RAB dataset;
- full CUDA rerun evaluator baru;
- Raspberry Pi latency/RSS/CPU/power;
- MQTT publisher/outbox/LWT/TLS/ACL E2E;
- real anomaly labels/false-alert/day;
- 24–72 jam soak.

## 12. Rollback

- parser v2 tetap aktif;
- v2 tidak difilter ulang;
- firmware dapat kembali ke v2;
- gateway preprocessing config dapat rollback;
- L0 raw dapat direprocess;
- old CUDA artifacts retained sebagai historical evidence;
- tidak ada commit/push/reset/clean.

## 13. Next Order

1. review working tree independen oleh Hermes;
2. bench real v3 payload + sensor states;
3. one-node dual-version rollout/replay;
4. real raw RAB capture + reference comparison;
5. tune filter/calibration per sensor;
6. full repeated-seed CUDA rerun;
7. Pi benchmark;
8. sensor MQTT reliability/TLS/ACL E2E;
9. 24–72 jam soak;
10. retire v2 setelah rollback window selesai.
