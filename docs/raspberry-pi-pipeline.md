# Raspberry Pi AI Sensor Pipeline

## Alur Aktif

```text
serial/replay line
→ L0 raw envelope append-only
→ compact v1/v2/v3 parser
→ L1 hardware/legacy observation
→ identity/time/order validation
→ gateway semantic preprocessing
→ L2 canonical observation
→ identity-isolated resampling
→ L3 processed timeseries + provenance
→ feature extraction
→ clipping-observable normalization
→ train-schema-aligned windowing
→ L4 baseline/model/anomaly/drift/decision
```

## Contract dan Migration

- `compact_sensor.v3`: hardware observation aktif, wajib `processing_profile=hardware_only`;
- `compact_sensor.v2`: legacy node-preprocessed compatibility, tidak difilter ulang secara default;
- v1: optional migration/reference;
- unknown version/profile: fail-closed.

Parser mempertahankan boot/sequence/time basis, firmware/config/calibration version, hardware state, event ID, dan source provenance.

## Validation dan Identity

Validator:

- finite/project range;
- partial sensor support;
- duplicate/out-of-order rejection;
- sequence-gap/reboot observability;
- source status/quality gate;
- state sequence key `(gateway_id,node_id,boot_id)`;
- last-boot key `(gateway_id,node_id)`.

Node ID yang sama pada gateway berbeda tidak berbagi state.

## Gateway Semantic Preprocessing

Filter state:

```text
(gateway_id,node_id,boot_id,field)
```

Available filters:

```text
none
ema
median
moving_average   # compatibility/shadow only
```

Default adalah `none`. Filter production menunggu real hardware noise/reference evidence.

## Resampling dan Windowing

- buffer/window identity `(gateway_id,node_id,room_id)`;
- resampling bucket `(gateway_id,node_id,room_id,time_bucket)`;
- source event IDs retained;
- mixed preprocessing version bucket rejected;
- `valid_ratio` dihitung;
- points di bawah `min_valid_ratio` tidak masuk window;
- feature memiliki presence mask agar nol valid tidak dianggap missing;
- normalization 0–1 mencatat lower/upper clipping;
- window tetap `[samples,timesteps,active_features]`;
- ordered active feature schema/hash berasal dari train-only dataset preparation;
- checkpoint/live schema mismatch fail-closed.

## Features

Canonical sensor values, presence masks, delta, rolling mean/std, missing count, valid ratio, dan sequence-gap count.

Legacy proxy features hanya untuk migration/reference lane dan tidak menjadi target produksi.

## Forecast Dataset

- cadence diinfer dari timestamp per node atau dideklarasikan lalu divalidasi;
- integer-multiple gap dibedakan dari irregular cadence;
- horizon disimpan sebagai steps dan duration;
- split time-ordered + purge/no-overlap;
- active feature schema dipilih train-only;
- constant/near-constant target dan boundary saturation dilaporkan;
- effective target count masuk promotion gate.

## Modeling

Forecast targets:

```text
temperature_c, humidity_pct, pressure_hpa,
co_ppm, o3_ppm, co2_ppm, pm25_ug_m3
```

Baseline candidates:

- LastValue;
- window mean;
- drift;
- SeasonalNaive hanya bila period/window/horizon applicable dan tidak duplicate.

Baseline dipilih per target pada validation split lalu dikunci untuk test. Test tidak memilih pembanding.

Challengers:

- DLinear;
- FITS-inspired;
- FITS official-style comparator;
- LSTM residual;
- native/optional streaming anomaly/drift.

Decision layer mengutamakan quality/rules dan dapat abstain. Drift tidak otomatis berarti bahaya lingkungan.

## Data Layers/Output

CLI replay menghasilkan:

```text
raw_payloads.jsonl
hardware_observations.jsonl
canonical_observations.jsonl
processed_timeseries.jsonl
windows.jsonl
normalization_report.json
```

Runtime builder mendukung `sensor_ai.v1` untuk compact-origin dan
`sensor_ai.v2` untuk source-agnostic live integration; status builder
mendukung `sensor_status.v1`.

**Update deployment 25 September 2026:** current `iiotgw` ChirpStack lane sudah
broker-E2E pada `sensor_ai.v2` dengan QoS1, retained status/LWT, SQLite event
outbox, publish confirmation, reconnect, restart-state restore, cold reboot,
dan versioned rollback. TLS/ACL dan downstream database/API/dashboard tetap
belum production-E2E.

## Data Lanes

- simulation/reference;
- real offline;
- real live.

Setiap lane memiliki provenance/adapter. Synthetic/reference result tidak boleh dilaporkan sebagai field performance.

## Current Verification

Host tests mencakup v3/parser/provenance, ChirpStack adapter, identity/session
isolation, receiver/restart, durable publisher/outbox, replay L0–L4,
cadence/quality/baseline gates, anomaly harness, live forecast state, deployment
helpers, dan decision layer. Final host suite 25 September: **126/126 PASS**.

Release yang sama sudah diuji di Raspberry Pi `iiotgw`: 126 tests discovered,
OK dengan expected host/development-only skips; critical source/config/unit
hashes cocok byte-for-byte dengan release; runtime sekitar 50 MB RSS pada
snapshot commissioning; cold reboot/OTAA recovery/rollback broker path PASS.

Data provenance current tetap dibatasi: Node 1 values = dummy JSON HardProg
meski transport live; Node 2 temp/RH = BME fisik test setup. Karena itu Node 1
forecast current hanya runtime/plumbing evidence. Final hardware definition,
real-RAB accuracy/calibration, 24–72 h soak, TLS/ACL, dan downstream
TimescaleDB/FastAPI/dashboard tetap open.
