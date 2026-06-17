# Shared Canonical Schema

Canonical schema adalah titik temu semua sumber data sebelum masuk shared
preprocessing, forecasting, evaluator, model selector, atau decision layer.

## Posisi Canonical Schema

```text
simulation adapter
real offline file adapter
real live stream receiver
        |
        v
canonical sensor reading
        |
        v
validation -> buffer -> resampling -> features -> normalization -> windowing
        |
        v
forecasting/evaluation/decision logic
```

Dengan pola ini, repo dapat memakai ulang core logic tanpa mencampur asumsi
dataset publik, file raw real, dan live receiver.

## Field Wajib

Canonical sensor reading minimal memiliki field berikut:

| Field | Tipe | Keterangan |
| --- | --- | --- |
| `gateway_id` / `gw` | string | Identitas gateway Raspberry Pi. |
| `node_id` / `n` | string | Identitas ESP32-C6 node. |
| `room_id` / `r` | string | Lokasi atau ruangan. |
| `timestamp` / `ts` | ISO-8601 atau epoch | Waktu sampling sensor dari node jika tersedia. |
| `sequence` / `seq` | integer | Nomor urut payload per node. |
| `sensor` / `s` | object | Nilai sensor target. |

`status`, `quality`, `flags`, dan `radio` sangat direkomendasikan untuk
deployment real karena membantu validasi, troubleshooting, dan audit kualitas
data.

## Field Sensor Target

| Canonical field | Alias compact | Sensor project | Catatan |
| --- | --- | --- | --- |
| `temperature_c` | `tc` | BME688/BME668 | Fitur utama. |
| `humidity_pct` | `h` | BME688/BME668 | Fitur utama. |
| `pressure_hpa` | `p` | BME688/BME668 | Optional jika sensor belum tersedia. |
| `bme_gas_raw` | `bme` | BME688/BME668 | Gas resistance/raw style signal. |
| `co_raw` | `co` | SEN0377-like | CO/gas tambahan. |
| `voc_raw` | `v` | Optional | Dipakai jika sensor VOC tersedia. |
| `gas_raw` | `g` | Optional legacy/generic | Cadangan sinyal gas umum. |

Power/battery field (`battery_voltage`, `current_ma`, `power_mw`) boleh ada di
schema, tetapi bukan fokus workflow forecasting saat ini.

## Contoh Compact Payload

```json
{
  "gw": "raspi_gateway_01",
  "n": "esp32c6_node_01",
  "r": "lab_01",
  "ts": "2026-06-18T09:30:00+07:00",
  "seq": 42,
  "st": "ok",
  "q": "valid",
  "f": [],
  "s": {
    "tc": 28.4,
    "h": 62.1,
    "p": 1008.6,
    "bme": 1840,
    "co": 0.012
  },
  "lora": {
    "rssi": -87,
    "snr": 8.5
  }
}
```

Parser existing menerima alias compact di atas dan mengubahnya menjadi
`SensorReading` internal.

## Timestamp Policy

- `timestamp` adalah waktu sampling atau waktu payload dibuat di ESP32-C6.
- Receiver live boleh menambahkan `receive_timestamp` sebagai metadata wrapper.
- Jika jam ESP32-C6 belum sinkron, receiver harus tetap menyimpan
  `receive_timestamp` agar urutan data bisa diaudit.
- Semua timestamp harus bisa dikonversi ke timezone-aware datetime.

## Sequence Policy

- `sequence` naik satu per payload per node.
- Sequence gap tidak selalu membuat payload invalid keras, tetapi harus
  ditandai sebagai issue agar missing packet dapat dihitung.
- Sequence reset boleh terjadi saat node restart dan harus dicatat melalui
  status/flag jika firmware mendukung.

## Quality Policy

- `status=ok` dan `quality=valid` berarti payload dapat dipakai normal.
- `status=sensor_error`, `quality=invalid`, atau flag `sensor_error` harus
  diperlakukan sebagai invalid keras.
- Missing optional pressure/BME/VOC boleh menjadi warning, bukan otomatis gagal
  total, selama masih ada temperature, humidity, dan minimal satu gas signal.

## Output Setelah Canonical

Setelah canonical schema, data boleh diproses menjadi:

- validation result;
- resampled point;
- feature vector;
- LSTM-ready window `[timesteps, features]`;
- forecast dataset `X/y`;
- forecast payload v1;
- forecast decision payload v1.

Canonical schema bukan model dataset final. Normalisasi, feature extraction, dan
windowing tetap dilakukan setelah canonical stage.
