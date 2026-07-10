# Pembagian Preprocessing ESP32-C6 dan Raspberry Pi

## Arsitektur

```text
sensor environment
-> ESP32-C6
-> compact_sensor.v2
-> Ebyte E32
-> Raspberry Pi receiver
-> canonical validation
-> resampling/features/normalization/windowing
-> baseline/model/decision
-> integration payload
```

## ESP32-C6

Tugas:

- baca sensor;
- sanity range ringan;
- moving average per field;
- status per sensor;
- boot ID dan sequence;
- payload compact v2;
- transport LoRa.

Bukan tugas ESP32-C6:

- forecasting utama;
- anomaly/drift model;
- training;
- multi-dataset adaptation;
- MQTT/backend logic.

## Raspberry Pi

Tugas:

- serial reconnect/backoff;
- append-only raw envelope;
- parse/version validation;
- authoritative receive time;
- duplicate/out-of-order handling;
- unit/canonical mapping;
- resampling dan valid-ratio gate;
- features dan normalization;
- windowing;
- baseline/model inference;
- quality/rules/abstain;
- event/status contract.

## Fail-Closed

- sensor missing → null + status;
- all sensors invalid → no valid event;
- invalid frame/version → reject;
- uptime → receive time authority;
- duplicate/out-of-order → reject;
- invalid checkpoint → reject;
- invalid/stale decision input → abstain.

## Current Boundary

Firmware mock dan gateway host path sudah end-to-end. Hardware read penuh, radio link, MQTT publisher, dan model promotion belum selesai.
