# Pembagian Preprocessing ESP32-C6 dan Raspberry Pi

## Keputusan Aktif

Project memakai **Gateway-Centric Semantic Preprocessing**.

```text
sensor environment
→ ESP32-C6 hardware acquisition/integrity
→ compact_sensor.v3 hardware observation
→ Ebyte E32
→ Raspberry Pi raw receiver
→ parser/time/order/semantic validation
→ per-sensor filter + resampling + missing policy
→ features + normalization + windowing
→ baseline/model/anomaly/drift/decision
→ sensor_ai.v1 / sensor_status.v1
```

ADR: `docs/adr/ADR-001-gateway-centric-sensor-preprocessing.md`.

## ESP32-C6 Ownership

Wajib tetap di node:

- I²C/UART/ADC transaction;
- protocol frame/header/length/checksum;
- timeout dan bus recovery;
- warm-up, heater/fan, not-ready state;
- official vendor compensation;
- register/ADC ke engineering unit;
- broad hardware-impossibility gate;
- per-sensor hardware state;
- rail/sample scheduling;
- node/room identity;
- boot ID, sequence, time basis;
- firmware/config/calibration version;
- bounded payload dan E32 transport.

Tidak boleh di node:

- semantic moving average/EMA/median;
- project-level sanity/alert threshold;
- interpolation/resampling;
- normalization;
- feature engineering/windowing;
- anomaly/drift/forecast/model;
- final semantic quality, battery health, node health, atau environment decision.

## Raspberry Pi Ownership

- raw append-only durability;
- contract/profile/version validation;
- authoritative receive/event-time;
- duplicate/out-of-order/sequence-gap/reboot classification;
- canonical mapping;
- project-level range/quality;
- optional per-sensor filter;
- resampling dan valid-ratio gate;
- missing/interpolation policy;
- normalization with clipping telemetry;
- feature extraction/windowing;
- baseline/model/anomaly/drift;
- rules-first decision dan abstain;
- event/status contract.

## Contract Migration

```text
v2 = legacy node-preprocessed observation
v3 = hardware observation before semantic preprocessing
```

Gateway:

- membaca v2 dan v3;
- default tidak memfilter v2 lagi;
- hanya memproses v3 melalui `gateway_preprocess.v1`;
- menolak unknown profile/version;
- menyimpan preprocessing provenance;
- menolak mixed preprocessing version dalam satu bucket.

## State Isolation

Filter state:

```text
(gateway_id, node_id, boot_id, field_name)
```

Resampling bucket:

```text
(gateway_id, node_id, room_id, bucket_timestamp)
```

Dengan demikian:

- node berbeda tidak pernah dirata-ratakan bersama;
- reboot tidak mewarisi filter state boot lama;
- state bounded;
- replay dapat direproduksi dari raw + config/version yang sama.

## Data Layers

| Layer | Isi | Mutable/derived |
|---|---|---|
| L0 | exact raw payload/envelope + receive metadata | source of truth, append-only |
| L1 | parsed hardware observation | derived from L0 |
| L2 | canonical/semantic observation | derived/versioned |
| L3 | filtered/resampled timeseries | derived/versioned |
| L4 | model input/output/decision | derived/versioned |

Derived output tidak menggantikan L0.

## Initial Filter Policy

Default: `none`.

Available:

- EMA;
- median;
- moving average untuk compatibility/shadow only.

Pemilihan filter wajib per sensor dan berdasarkan real data. Untuk anomaly, raw/filter/residual harus dibandingkan agar spike tidak hilang diam-diam.

## Fail-Closed

- checksum/frame invalid → node value null/error;
- warm-up → null + `warming`;
- payload version/profile invalid → reject;
- uptime → gateway receive-time authority;
- duplicate/out-of-order → reject;
- sequence gap/reboot → explicit issue;
- preprocessing version mixed → reject bucket;
- checkpoint/feature schema mismatch → reject;
- invalid/stale decision input → abstain;
- target constant/saturated → model promotion blocked.

## Rollback

- parser v2 tetap aktif selama migration;
- firmware dapat rollback v3 → v2;
- gateway filter/config dapat rollback tanpa reflashing node;
- L0 raw dapat direprocess;
- v2 parser tidak dihapus pada hari node terakhir di-upgrade.

## Evidence Boundary

Host/build evidence saat ini membuktikan source, schema, tests, dan compile. Belum membuktikan sensor/bridge/E32/rail/calibration/power/Pi/MQTT/field soak.
