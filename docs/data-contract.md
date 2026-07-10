# Kontrak Data Node Sensor

Dokumen ini adalah sumber kontrak aktif untuk firmware, receiver Python, payload AI sensor, dan status sensor.

## 1. Compact Sensor v2 — ESP32-C6 ke Gateway

Schema: `schemas/compact_sensor.v2.schema.json`.

Top-level:

| Field | Arti |
|---|---|
| `v` | versi compact, wajib `2` |
| `gw` | gateway target/identity bila tersedia |
| `n` | node ID |
| `r` | room ID |
| `ts` | uptime node atau waktu absolut |
| `seq` | sequence per boot |
| `bid` | boot ID baru tiap reboot |
| `st` | `ok`, `degraded`, atau `sensor_error` |
| `q` | `valid`, `partial`, atau `invalid` |
| `f` | flags string dipisah `|` atau array string |
| `ok` | status per sensor |
| `radio` / `lora` | metadata inline opsional `rssi`/`snr`; wrapper-level `radio` lebih disarankan |
| `s` | object pembacaan sensor |

Field sensor:

| Compact | Canonical | Semantik |
|---|---|---|
| `tc` | `temperature_c` | °C |
| `h` | `humidity_pct` | %RH |
| `p` | `pressure_hpa` | hPa |
| `bme` | `bme_gas_ohm` | gas resistance BME688, ohm |
| `co` | `co_ppm` | CO SEN0466, ppm |
| `n2mv` | `no2_raw_mv` | tegangan NO₂ SEN0574, mV |
| `n2r` | `no2_ratio` | rasio kualitatif terhadap baseline |
| `o3` | `o3_ppm` | O₃, ppm |
| `co2` | `co2_ppm` | CO₂, ppm |
| `pm1` | `pm1_ug_m3` | µg/m³ |
| `pm25` | `pm25_ug_m3` | µg/m³ |
| `pm10` | `pm10_ug_m3` | µg/m³ |
| `bv` | `battery_voltage` | V |
| `bi` | `current_ma` | mA |
| `bp` | `power_mw` | mW |

Aturan:

- missing ditulis `null`;
- nol hanya berarti pengukuran nol yang valid;
- NO₂ tidak boleh dipromosikan menjadi ppm tanpa kalibrasi referensi;
- status sensor parsial boleh diteruskan;
- versi lain, bool sebagai angka, NaN/Inf, boot ID kosong, atau sequence negatif ditolak;
- compact v1 hanya migration/reference dan dapat dimatikan.

## 2. Time Policy

`ts` numeric kecil dari firmware adalah uptime, bukan Unix epoch. Gateway membentuk:

- `timestamp`: waktu authoritative untuk event;
- `source.node_timestamp`: nilai asli node;
- `source.receive_timestamp`: waktu gateway menerima frame;
- `source.time_quality`:
  - `gateway_received` bila node memberi uptime/tidak punya waktu absolut;
  - `node_epoch` bila node memberi epoch valid;
  - `node_synced` bila node memberi waktu ISO/RFC3339 valid.

Gateway tidak boleh mengubah uptime 77 detik menjadi tanggal 1970.

## 3. Identity, Retry, dan Reboot

Event ID diturunkan secara deterministik dari:

```text
gateway_id + node_id + boot_id + sequence + node_timestamp + sensor data
```

Konsekuensi:

- retry frame yang sama menghasilkan `event_id` sama;
- reboot membentuk `boot_id` baru;
- sequence boleh kembali ke nol setelah reboot;
- duplicate dalam boot yang sama ditolak;
- sequence lebih kecil dalam boot yang sama ditolak;
- sequence gap diterima dengan issue observability.

## 4. Canonical Internal Record

Parser menghasilkan `SensorReading` dengan:

```text
schema_version,event_id,gateway_id,node_id,room_id,timestamp,
receive_timestamp,node_timestamp,time_quality,boot_id,sequence,
status,quality,flags,sensor_status,radio,source,sensor
```

Canonical record hanya menyatukan nama dan unit. Ia tidak mengisi field yang tidak ada.

Field lama seperti proxy gas Gary tetap berada di lane migration/reference dan tidak disamakan dengan sensor RAB.

## 5. `sensor_ai.v1`

Schema: `schemas/sensor_ai.v1.schema.json`.

Payload target ke topic data:

```json
{
  "schema_version": "sensor_ai.v1",
  "event_id": "se_...",
  "gateway_id": "raspi_gateway_01",
  "node_id": "esp32c6_node_01",
  "room_id": "room_A",
  "timestamp": "2026-07-10T06:00:00+00:00",
  "source": {
    "compact_version": 2,
    "boot_id": "boot-a",
    "sequence": 10,
    "node_timestamp": 77,
    "receive_timestamp": "2026-07-10T06:00:00+00:00",
    "time_quality": "gateway_received",
    "transport": "lora_serial",
    "radio": {"rssi": null, "snr": null},
    "sensor_status": {"bme688": "ok"},
    "flags": []
  },
  "sensor": {
    "temperature_c": 28.1,
    "humidity_pct": 62.2,
    "pressure_hpa": 1008.1,
    "bme_gas_ohm": 18100.0,
    "co_ppm": 2.1,
    "no2_raw_mv": 423.0,
    "no2_ratio": 1.01,
    "o3_ppm": 0.03,
    "co2_ppm": 655.0,
    "pm1_ug_m3": 8.1,
    "pm25_ug_m3": 12.2,
    "pm10_ug_m3": 18.4,
    "battery_voltage": 4.04,
    "current_ma": 82.0,
    "power_mw": 331.3
  },
  "ai": {
    "env_status": "unknown",
    "anomaly_score": null,
    "forecast_status": "unavailable",
    "main_factor": null,
    "battery_status": "unknown",
    "node_health": "healthy",
    "confidence": null,
    "abstain": true
  },
  "deployment": {"config_version": "sensor-foundation-v2"}
}
```

AI default abstains sampai decision layer memiliki data/model yang layak.

## 6. `sensor_status.v1`

Schema: `schemas/sensor_status.v1.schema.json`.

Status berisi:

- gateway ID;
- timestamp;
- `online`, `degraded`, atau `offline`;
- node ID/room/boot/sequence terakhir;
- event ID terakhir;
- receive time dan age;
- node health.

`node_silent_after_sec` menentukan kapan node menjadi `stale`.

## 7. MQTT Topics

```text
data:   iot/{gateway_id}/data
status: iot/{gateway_id}/status/sensor
```

Topik status lama tanpa domain hanya migration-only. Data event tidak boleh retained; status dapat retained dan memiliki LWT bila publisher produksi ditambahkan. Status lama tidak boleh masuk event spool/replay.

Repo saat ini menyediakan contract/schema/builder, bukan broker atau publisher produksi.

## 8. Validation dan Fail-Closed

Hard invalid:

- identity hilang;
- tidak ada data sensor sama sekali;
- value di luar sanity range;
- status/quality error;
- duplicate/out-of-order;
- schema/version/flags malformed;
- timestamp absolut invalid.

Soft issue:

- sensor tertentu missing;
- sequence gap;
- reboot;
- compact v1 migration.

Soft issue boleh diteruskan bila minimal ada data valid dan status sesuai. Downstream harus membaca issue/quality, bukan menganggap partial sebagai full-quality.

## 9. Dataset Boundary

Dataset adapters harus menghasilkan canonical field hanya bila unit dan semantiknya sesuai. Contoh:

- CO UCI dalam mg/m³ tetap reference field, bukan `co_ppm`;
- NO₂ UCI dalam µg/m³ tetap reference field, bukan sinyal SEN0574;
- gas Bristol adalah IAQ index, bukan `bme_gas_ohm`;
- missing dataset tetap `null`.

Lihat `docs/dataset-catalog-and-adapters.md`.
