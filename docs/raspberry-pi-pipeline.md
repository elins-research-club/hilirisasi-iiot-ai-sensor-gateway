# Raspberry Pi AI Sensor Pipeline

## Alur Aktif

```text
serial/replay line
-> raw envelope append-only
-> compact v2 parser
-> validation
-> node buffer
-> resample
-> min-valid-ratio gate
-> feature extraction
-> normalization
-> windowing
-> baseline/model/decision
```

## Parser dan Validation

Parser:

- compact v2 default;
- compact v1 migration optional;
- finite numeric only;
- robust flags/status/radio;
- uptime-aware time policy;
- stable event ID.

Validator:

- sanity range;
- partial sensor support;
- duplicate/out-of-order rejection;
- sequence-gap/reboot observability;
- source status/quality gate.

## Resampling dan Windowing

- buffer bounded per node;
- points dikelompokkan per interval;
- `valid_ratio` dihitung;
- points di bawah `min_valid_ratio` tidak masuk window;
- feature memiliki presence mask agar nol valid tidak dianggap missing;
- normalisasi min-max dijepit 0–1;
- window tetap `[samples, timesteps, features]`.

## Features

Canonical sensor values, presence masks, delta, rolling mean/std, missing count, valid ratio, dan sequence-gap count.

Legacy proxy features hanya untuk migration/reference lane dan tidak menjadi target produksi.

## Modeling

Forecast targets:

```text
temperature_c, humidity_pct, pressure_hpa,
co_ppm, o3_ppm, co2_ppm, pm25_ug_m3
```

Baseline/model:

- LastValue;
- SeasonalNaive;
- DLinear;
- FITS-inspired;
- LSTM;
- optional streaming anomaly/drift.

Decision layer mengutamakan quality/rules dan dapat abstain.

## Output

Runtime canonical/event builder mendukung `sensor_ai.v1`. Status builder mendukung `sensor_status.v1` dan stale-node policy. MQTT publisher produksi belum ada di repo.

## Data Lanes

- simulation/reference;
- real offline;
- real live.

Setiap lane harus memiliki provenance dan adapter. Synthetic/reference result tidak boleh dilaporkan sebagai field performance.

## Current Verification

Host tests mencakup parser, validation, receiver restart, serial sleep/backoff, time policy, schema, data adapters, model smoke, dan decision layer. Raspberry Pi runtime/resource belum diukur.
