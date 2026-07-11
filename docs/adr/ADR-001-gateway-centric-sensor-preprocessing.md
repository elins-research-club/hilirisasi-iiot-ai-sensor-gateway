# ADR-001 — Gateway-Centric Semantic Preprocessing

- **Status:** Accepted and source-implemented
- **Tanggal:** 11 Juli 2026
- **Scope:** `iiot-ai-sensor-gateway`
- **Contract baru:** `compact_sensor.v3`
- **Compatibility:** `compact_sensor.v2` tetap dibaca sebagai legacy node-preprocessed observation

## Konteks

Arsitektur lama menjalankan sanity range dan moving average lima sampel di ESP32-C6, kemudian Raspberry Pi kembali melakukan validation, resampling, feature extraction, normalization, dan windowing. Pembagian ini menimbulkan beberapa masalah:

1. observasi sebelum smoothing hilang;
2. perubahan filter memerlukan perubahan firmware;
3. preprocessing tersebar di C++ dan Python;
4. replay offline tidak dapat mereproduksi state node lama secara penuh;
5. moving average node lalu bucket average gateway menghasilkan transformasi ganda;
6. state dan debugging tersebar pada dua perangkat;
7. training-serving parity sulit dijamin;
8. batch multi-node dapat berbahaya bila state atau bucket tidak diisolasi oleh identity.

## Keputusan

Project memakai **Gateway-Centric Semantic Preprocessing**.

```text
ESP32-C6
= hardware acquisition
+ protocol/frame/checksum integrity
+ warm-up/heater/fan status
+ official vendor compensation
+ register/ADC → engineering unit
+ broad hardware-impossibility gate
+ node/boot/sequence/time-basis/version metadata
+ bounded payload + LoRa transport

Raspberry Pi
= raw durability
+ contract/semantic validation
+ authoritative receive/event-time policy
+ duplicate/out-of-order handling
+ project range/quality
+ optional per-sensor filtering
+ resampling/missing policy
+ features/normalization/windowing
+ baseline/model/anomaly/drift/decision
```

Node sensor tidak menjalankan:

- moving average semantik;
- EMA/median/Hampel project filter;
- project alert threshold;
- resampling/interpolation;
- normalization;
- feature extraction/windowing;
- model atau final AI quality/status.

Node tetap fail-closed untuk kegagalan yang hanya dapat dinilai di dekat hardware:

- checksum/frame/protocol invalid;
- timeout/not-ready/warm-up;
- vendor status invalid;
- non-finite;
- engineering conversion invalid;
- broad physical-impossibility range;
- payload overflow/transport failure.

## Contract Versioning

### `compact_sensor.v2`

Makna dipertahankan:

```text
legacy node-preprocessed observation
```

Gateway tidak memfilter v2 lagi secara default. Record diberi provenance:

```text
processing_profile = legacy_node_preprocessed
preprocessing_version = legacy_node_preprocessed.v2
```

### `compact_sensor.v3`

Makna baru:

```text
hardware observation before gateway semantic preprocessing
```

Metadata wajib:

```text
v,n,r,ts,tb,seq,bid,pp,fw,cfg,cal,hs,f,ok,s
```

Aturan utama:

- `pp` wajib `hardware_only`;
- `tb` menjelaskan basis waktu;
- `fw`, `cfg`, `cal` wajib non-empty;
- `hs` adalah summary hardware, bukan final semantic quality;
- per-sensor `ok` memakai hardware state eksplisit;
- missing tetap `null`;
- NO₂ tetap mV/rasio kualitatif, bukan ppm;
- tidak ada field kamera/ToF/distance;
- unknown version/profile ditolak.

Schema resmi:

```text
schemas/compact_sensor.v3.schema.json
```

## Data Layer Resmi

```text
L0 raw payload/envelope
L1 hardware observation
L2 canonical/semantic observation
L3 processed timeseries
L4 model input/output/decision
```

Output replay CLI:

```text
raw_payloads.jsonl
hardware_observations.jsonl
canonical_observations.jsonl
processed_timeseries.jsonl
windows.jsonl
lstm_windows.jsonl
normalization_report.json
```

L3 menyimpan `source_event_ids` dan `preprocessing_version`. Bucket yang mencampur preprocessing version ditolak.

## State dan Isolation

Gateway filter state di-key dengan:

```text
gateway_id + node_id + boot_id + field_name
```

Resampling bucket di-key dengan:

```text
gateway_id + node_id + room_id + time_bucket
```

Konsekuensi:

- tidak ada cross-node state contamination;
- reboot membentuk filter state baru;
- state dibatasi oleh `state_max_entries`;
- v2 tidak terkena double smoothing;
- input/config/version yang sama dapat direplay deterministik.

## Initial Filter Policy

Default production-like config memakai `kind = "none"` untuk field yang dikonfigurasi. Filter tersedia, tetapi tidak dipromosikan tanpa data hardware nyata:

```text
none
ema
median
moving_average   # compatibility/shadow study only
```

Satu filter untuk semua sensor dilarang. Pemilihan filter harus berbasis noise, cadence, anomaly preservation, calibration, dan reference comparison per sensor.

## Migration dan Rollback

Urutan implementasi:

1. ADR dan contract v3;
2. parser/schema/provenance v3;
3. gateway preprocessing dan state isolation;
4. simulator/replay v3;
5. firmware hardware-only cutover;
6. dual-read v2/v3;
7. bench/live shadow evidence;
8. calibration/filter study;
9. retire v2 setelah seluruh active node stabil.

Rollback:

- gateway tetap membaca v2;
- firmware dapat kembali ke v2 selama parser v2 aktif;
- preprocessing config/version dapat dikembalikan;
- L0 raw payload menjadi source of truth untuk reprocessing;
- derived L2–L4 tidak dianggap source of truth permanen.

## Acceptance Software

- schema/parser v3 formal;
- v2 semantics tidak berubah;
- node tidak memiliki semantic moving average;
- state gateway terisolasi per node/boot/field;
- resampling tidak mencampur node;
- provenance source-event dan preprocessing version tersedia;
- offline/live memakai modul preprocessing yang sama;
- tests, config check, schema validation, firmware compile, dan diff check lulus;
- docs + Project Context sinkron.

## Acceptance Hardware yang Masih Open

- v3 frame dari board nyata;
- sensor state/warm-up/error nyata;
- E32 packet/link behavior;
- payload size/airtime;
- 5 V rail/current/thermal;
- ADC/baseline/calibration;
- restart/recovery;
- 24–72 jam soak;
- reference instrument comparison.

## Konsekuensi

### Positif

- raw observation lebih terjaga;
- satu semantic preprocessing implementation;
- training-serving parity lebih kuat;
- filter/model dapat diubah tanpa reflashing node;
- deterministic replay dan debugging lebih jelas;
- ownership hardware vs AI lebih tegas.

### Negatif/Risiko

- gateway menjadi pusat state preprocessing;
- storage raw dan observability harus dikelola;
- migration v2/v3 harus eksplisit;
- gateway failure menghentikan semantic processing walau node masih mengukur;
- hardware state metadata harus disiplin;
- filter yang salah dapat menghapus anomaly sehingga raw/filter/residual perlu dibandingkan.

## Keputusan yang Ditolak

- mengubah makna v2 diam-diam;
- menghapus checksum/warm-up/compensation dari node;
- mengirim semua register mentah dan memindahkan driver ke gateway;
- raw+processed permanen dalam setiap packet;
- satu filter global untuk seluruh sensor;
- menyimpan hanya filtered data;
- memindahkan future local safety loop ke gateway.
