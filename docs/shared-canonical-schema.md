# Shared Canonical Schema

Semua lane masuk ke canonical schema sebelum semantic preprocessing/modeling:

```text
simulation/reference adapter
real offline adapter
real live receiver
→ L0 raw payload/envelope
→ L1 hardware observation / dataset record
→ L2 canonical SensorReading + semantic validation
→ L3 processed timeseries
→ L4 model input/output
```

## Sensor Fields

```text
temperature_c
humidity_pct
pressure_hpa
bme_gas_ohm
co_ppm
no2_raw_mv
no2_ratio
o3_ppm
co2_ppm
pm1_ug_m3
pm25_ug_m3
pm10_ug_m3
battery_voltage
current_ma
power_mw
```

Missing = `null`; nol bukan missing marker. NO₂ tidak memiliki canonical ppm field.

## Runtime Metadata

```text
schema_version / compact_version
event_id / source_event_id
gateway_id / node_id / room_id
timestamp / receive_timestamp / node_timestamp
time_quality / time_basis
boot_id / sequence
status / quality / hardware_summary
flags / sensor_status / radio / source
processing_profile
firmware_version
hardware_config_version
calibration_version
preprocessing_version
```

Metadata provenance tidak boleh dibuang saat membentuk payload backend atau artifact modeling.

## Version Semantics

```text
compact_sensor.v1 = legacy/proxy migration
compact_sensor.v2 = legacy node-preprocessed observation
compact_sensor.v3 = hardware observation before gateway semantic preprocessing
```

V2 tidak difilter ulang secara default. V3 wajib `processing_profile=hardware_only`.

## Reference/Migration Fields

`voc_raw`, `bme_gas_raw`, `co_raw`, dan `gas_raw` hanya lane migration/reference. Mereka tidak boleh menjadi pengganti field hardware RAB pada v3.

## Dataset Record

`iiot.dataset_record.v1` memuat:

- dataset/lane/record identity;
- timestamp/device/site;
- canonical sensor object;
- source-unit `reference` object;
- quality/provenance;
- missing/cadence assumptions.

Reference field tidak otomatis menjadi sensor field:

- CO mg/m³ ≠ CO ppm;
- NO₂ µg/m³ ≠ SEN0574 mV/ratio;
- IAQ index ≠ gas resistance;
- Fidas PM ≠ PMS7003T chip-identical.

## Time, Split, dan Feature Schema

- node uptime memakai gateway receive-time authority;
- dataset timestamp mempertahankan timezone/source assumption;
- cadence diinfer per node atau dideklarasikan lalu divalidasi;
- horizon disimpan sebagai steps dan duration;
- split time-ordered + purge/no-overlap;
- active feature schema dipilih train-only;
- ordered feature manifest + SHA-256 disimpan;
- normalizer/clipping report tidak memakai val/test untuk fit;
- leave-device/site-out bila tersedia.

## Schema Files

- `schemas/compact_sensor.v3.schema.json` — active hardware observation;
- `schemas/compact_sensor.v2.schema.json` — migration compatibility;
- `schemas/sensor_ai.v1.schema.json`;
- `schemas/sensor_status.v1.schema.json`.

Detail: `docs/data-contract.md` dan ADR-001.
