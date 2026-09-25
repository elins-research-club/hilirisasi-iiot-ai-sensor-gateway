# Kontrak Data Node Sensor

Dokumen ini adalah sumber kontrak aktif firmware, receiver/parser Python, preprocessing gateway, payload AI sensor, dan status sensor.

## 1. Version Matrix

| Versi | Makna | Status |
|---|---|---|
| `compact_sensor.v1` | legacy/proxy reference | migration-only, dapat disabled |
| `compact_sensor.v2` | legacy node-preprocessed observation | compatibility aktif, semantics tidak berubah |
| `compact_sensor.v3` | hardware observation sebelum semantic preprocessing gateway | contract aktif firmware |
| `chirpstack_live.v1` | source envelope live dari ChirpStack v4 untuk node yang belum memakai compact v3 | integration contract; bukan firmware contract |

Perubahan v2 → v3 adalah perubahan makna sehingga wajib version bump. V2 tidak boleh diam-diam diartikan sebagai raw/hardware observation.

## 2. `compact_sensor.v3`

Schema: `schemas/compact_sensor.v3.schema.json`.

### 2.1 Top-level

| Field | Arti |
|---|---|
| `v` | integer `3` |
| `gw` | gateway target/identity opsional |
| `n` | node ID |
| `r` | room ID |
| `ts` | waktu menurut basis `tb` |
| `tb` | `uptime_s`, `epoch_s`, `epoch_ms`, atau `rfc3339` |
| `seq` | sequence per boot |
| `bid` | boot identity baru tiap reboot |
| `pp` | wajib `hardware_only` |
| `fw` | firmware version |
| `cfg` | hardware/board config version |
| `cal` | calibration version; placeholder harus diberi label `unverified` |
| `hs` | hardware summary: `ok`, `warming`, `partial`, `error` |
| `f` | flags string `|` atau array string |
| `ok` | state per sensor |
| `radio` | metadata `rssi`/`snr` opsional |
| `s` | hardware observations dalam engineering units |

### 2.2 Sensor fields

| Compact | Canonical | Unit/semantik |
|---|---|---|
| `tc` | `temperature_c` | °C |
| `h` | `humidity_pct` | %RH |
| `p` | `pressure_hpa` | hPa |
| `bme` | `bme_gas_ohm` | BME688 gas resistance, ohm |
| `co` | `co_ppm` | SEN0466 ppm |
| `n2mv` | `no2_raw_mv` | SEN0574 mV |
| `n2r` | `no2_ratio` | rasio kualitatif terhadap baseline |
| `o3` | `o3_ppm` | O₃ ppm |
| `co2` | `co2_ppm` | CO₂ ppm |
| `pm1` | `pm1_ug_m3` | µg/m³ |
| `pm25` | `pm25_ug_m3` | µg/m³ |
| `pm10` | `pm10_ug_m3` | µg/m³ |
| `bv` | `battery_voltage` | V |
| `bi` | `current_ma` | mA |
| `bp` | `power_mw` | mW |

Rules:

- missing/not-ready/invalid sensor value = `null`, bukan nol;
- NO₂ tidak boleh dipromosikan menjadi ppm tanpa reference calibration;
- bool, NaN, Inf, identity kosong, sequence negatif, unknown version/profile ditolak;
- camera/ToF/distance field tidak diizinkan;
- `hs` bukan semantic quality final;
- node tidak menghasilkan moving average, imputation, feature, normalization, model output, final battery/node health, atau gateway event time.

### 2.3 Per-sensor hardware state

Schema mengizinkan:

```text
ok
warming
timeout
checksum_error
protocol_error
range_error
missing
disabled
error
```

Firmware encoder saat ini mempublikasikan `ok`, `warming`, atau generic `error`; flags/log masih membawa konteks tambahan. Reason code granular harus diverifikasi dan diperkaya berdasarkan hardware bring-up, bukan diarang pada mock.

## 3. `compact_sensor.v2` Compatibility

Schema: `schemas/compact_sensor.v2.schema.json`.

V2 tetap bermakna:

```text
legacy node-preprocessed observation
```

Gateway default:

```text
processing_profile = legacy_node_preprocessed
preprocessing_version = legacy_node_preprocessed.v2
apply_to_v2 = false
```

Dengan demikian v2 tidak terkena double smoothing. V2 parser dipertahankan selama migration/rollback, tetapi firmware aktif menghasilkan v3.

## 4. Time Policy

Node membawa `ts` dan `tb`. Gateway membentuk:

- `timestamp`: event-time authoritative yang digunakan downstream;
- `source.node_timestamp`: nilai asli node;
- `source.receive_timestamp`: waktu gateway menerima frame;
- `source.time_basis`: basis node;
- `source.time_quality`: `gateway_received`, `node_epoch`, atau `node_synced`.

`uptime_s=77` tidak boleh menjadi tanggal 1970. Sequence gap, late, duplicate, out-of-order, dan reboot adalah semantics berbeda.

## 5. Identity, Retry, Reboot

Deterministic event identity berasal dari:

```text
gateway_id + node_id + boot_id + sequence + node_timestamp + sensor content
```

- retry frame sama → event ID sama;
- boot ID baru → sequence boleh kembali nol;
- duplicate/out-of-order dalam boot sama → fail-closed;
- sequence gap → diterima dengan issue observability;
- backend dedupe menggunakan event ID, bukan timestamp saja.

## 6. Canonical `SensorReading`

Parser mempertahankan:

```text
schema_version
compact_version
event_id
gateway_id/node_id/room_id
timestamp/receive_timestamp/node_timestamp
time_quality/time_basis
boot_id/sequence
status/quality/flags/sensor_status/radio/source
processing_profile
firmware_version
hardware_config_version
calibration_version
hardware_summary
preprocessing_version
source_event_id
source_contract/source_session_id/source_metadata
sensor
```

Canonical mapping tidak mengisi sensor yang tidak ada dan tidak mengubah unit reference dataset menjadi unit RAB.

## 7. Data Layers dan Provenance

```text
L0 raw transport payload/envelope
L1 hardware observation
L2 canonical/semantic observation
L3 processed timeseries
L4 model input/output/decision
```

CLI replay menghasilkan:

```text
raw_payloads.jsonl
hardware_observations.jsonl
canonical_observations.jsonl
processed_timeseries.jsonl
windows.jsonl
normalization_report.json
```

`windows.jsonl` adalah nama canonical tunggal. `lstm_windows.jsonl` hanya alias reader-side untuk artifact lokal lama dan tidak ditulis oleh run baru.

L3 memiliki `source_event_ids` dan `preprocessing_version`. Mixed preprocessing versions dalam bucket yang sama ditolak.

## 8. Gateway Semantic Preprocessing

Default config:

```toml
[preprocessing]
version = "gateway_preprocess.v1"
apply_to_v2 = false
state_max_entries = 100000
```

State filter diisolasi oleh:

```text
gateway_id + node_id + boot_id + field
```

Resampling diisolasi oleh:

```text
gateway_id + node_id + room_id + time_bucket
```

Filter registry:

```text
none
ema
median
moving_average   # compatibility/shadow only
```

Default raw-first memakai `none`. Filter production harus dipilih per sensor dari real hardware/noise/reference evidence.

## 9. `sensor_ai.v1`

Topic:

```text
iot/{gateway_id}/data
```

Payload membawa:

- deterministic event ID;
- source compact version/time/boot/sequence;
- hardware/config/calibration/preprocessing provenance;
- canonical sensor values;
- AI decision/status;
- deployment config version.

AI harus abstain bila source invalid/stale/unavailable atau model/data gate tidak layak. Drift tidak otomatis berarti kondisi lingkungan bahaya.

### 9.1 Batas origin `sensor_ai.v1`

`sensor_ai.v1` hanya untuk origin `compact_sensor.v1/v2/v3`. Builder sekarang
fail-closed bila dipanggil dengan source non-compact. Ini sengaja untuk mencegah
current ChirpStack uplink diberi `compact_version`, boot ID, firmware, config,
atau calibration metadata yang sebenarnya tidak tersedia.

## 9A. `chirpstack_live.v1` dan `sensor_ai.v2`

Deployment current `iiotgw` menerima uplink ChirpStack v4 dari:

```text
application/{application_id}/device/{dev_eui}/event/up
```

`chirpstack_live.v1` mempertahankan:

- DevEUI sebagai node identity;
- DevAddr sebagai transport/session identity, **bukan firmware boot ID**;
- fCnt sebagai sequence;
- deduplicationId, fPort, device/application metadata;
- event/receive timestamp;
- RSSI/SNR dari `rxInfo`;
- payload format;
- raw source metadata yang belum aman dipromosikan ke canonical sensor.

Current Node 2 `extra_raw_u16` tetap source metadata. Current Node 1 field
`no2` juga dipertahankan sebagai `no2_source_unmapped` sampai unit/semantic
hardware dikonfirmasi; ia tidak diam-diam diubah menjadi `no2_ratio` atau ppm.

`sensor_ai.v2` adalah source-agnostic event untuk integration lane tersebut.
Perbedaan utama terhadap v1:

```text
source.contract
source.source_event_id
source.session_id
source.sequence
source.metadata
deployment.runtime_mode
deployment.model_manifest_id
```

Dengan demikian compact origin lama tetap kompatibel sementara live ChirpStack
dapat membawa provenance yang jujur.

### 9A.1 Forecast payload pada v2

Ketika forecast runtime aktif, `ai.forecast` membawa state yang eksplisit:

```text
status
predicted{target: value}
horizon_steps
horizon_duration_seconds
model_type/model_version
runtime_backend
model_readiness
model_manifest_id
inference_latency_ms
```

Event yang bukan titik resample tidak lagi dilabeli `disabled` bila model
sebenarnya aktif. Runtime mempertahankan forecast state terakhir per node;
sebelum bucket pertama statusnya `waiting_for_resample`, lalu `warming`,
dan baru `available_shadow` setelah window artifact lengkap. Node yang bukan
target model diberi `not_target_node`.

## 10. `sensor_status.v1`

Topic:

```text
iot/{gateway_id}/status/sensor
```

Status retained, domain-specific, dan tidak masuk event outbox/replay. `node_silent_after_sec` menentukan stale/offline policy. Bare `iot/{gateway_id}/status` hanya migration-only.

## 11. Validation dan Fail-Closed

Hard invalid:

- identity/contract/profile invalid;
- tidak ada sensor observation;
- value non-finite atau di luar configured semantic range;
- invalid source status/quality;
- duplicate/out-of-order;
- invalid timestamp/time basis;
- mixed preprocessing version dalam satu resampling bucket;
- checkpoint/feature schema mismatch.

Soft issues:

- sebagian sensor missing/warming;
- sequence gap;
- reboot;
- v1/v2 migration path;
- clipping/out-of-domain telemetry yang belum melewati promotion threshold.

## 12. Dataset Boundary

- UCI CO mg/m³ tidak menjadi `co_ppm`;
- UCI NO₂ µg/m³ tidak menjadi SEN0574 signal;
- Bristol IAQ index tidak menjadi `bme_gas_ohm` atau CO₂;
- Fidas PM adalah reference lane, bukan PMS7003T chip-identical;
- missing tetap null;
- public/synthetic lane tidak membuktikan deployment;
- real RAB capture wajib untuk promotion/field claims.

## 13. Migration dan Rollback

- parser menerima v2/v3 secara eksplisit;
- gateway v3 dapat di-deploy sebelum firmware v3;
- firmware v3 rollout bertahap per node;
- rollback firmware ke v2 tidak memerlukan rollback gateway;
- raw L0 tetap source of truth untuk reprocessing;
- v2 hanya retired setelah tidak ada active node v2 selama periode yang disepakati dan rollback artifact aman.

Lihat `docs/adr/ADR-001-gateway-centric-sensor-preprocessing.md`.
