# Shared Canonical Schema

Semua lane harus masuk ke canonical schema sebelum preprocessing/modeling:

```text
simulation/reference adapter
real offline adapter
real live receiver
-> canonical SensorReading / dataset record
-> validation
-> resampling/features/normalization/windowing
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

Missing tetap `null`. Nilai nol tidak dipakai sebagai missing marker.

## Metadata Runtime

```text
schema_version
event_id
gateway_id
node_id
room_id
timestamp
receive_timestamp
node_timestamp
time_quality
boot_id
sequence
status
quality
flags
sensor_status
radio
source
```

## Reference/Migration Fields

Field proxy lama seperti `voc_raw`, `bme_gas_raw`, `co_raw`, dan `gas_raw` hanya dipertahankan pada lane migration/reference. Field tersebut tidak boleh masuk event canonical v2 sebagai pengganti hardware RAB.

## Dataset Record

Dataset adapter menggunakan `iiot.dataset_record.v1` dengan:

- dataset/lane/record identity;
- timestamp/device/site;
- canonical `sensor` object;
- unit-asli `reference` object;
- quality;
- provenance.

Reference field tidak otomatis menjadi sensor field. Contoh:

- CO mg/m³ tidak otomatis menjadi CO ppm;
- NO₂ µg/m³ tidak otomatis menjadi sensor ratio;
- IAQ index tidak otomatis menjadi gas resistance.

## Time dan Split

- runtime uptime memakai gateway receive time;
- dataset timestamp mempertahankan timezone/source assumption;
- sort time sebelum split;
- normalizer fit pada train;
- leave-device/site-out bila tersedia.

## Schema Files

- `schemas/compact_sensor.v2.schema.json`
- `schemas/sensor_ai.v1.schema.json`
- `schemas/sensor_status.v1.schema.json`

Detail lengkap: `docs/data-contract.md`.
