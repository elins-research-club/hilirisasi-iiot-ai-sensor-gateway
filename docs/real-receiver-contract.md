# Real Receiver Contract

Dokumen ini menjelaskan kontrak minimum antara ESP32-C6/LoRa receiver dan AI
Sensor Gateway pipeline.

## Peran Receiver

Receiver bertugas menerima payload dari ESP32-C6, mencatat metadata receive, dan
menyediakan data untuk parser/canonical schema. Receiver bukan model AI, bukan
MQTT broker, dan bukan backend.

## Compact ESP32 Payload

Contoh payload compact dari ESP32-C6:

```json
{
  "v": 1,
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
  }
}
```

## Receiver Wrapped Payload

Receiver boleh membungkus payload dengan metadata receive:

```json
{
  "receive_timestamp": "2026-06-18T09:30:02+07:00",
  "receiver_id": "raspi_gateway_01_lora_rx",
  "raw_payload": "{...compact json...}",
  "payload": {
    "v": 1,
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
    }
  },
  "radio": {
    "rssi": -87,
    "snr": 8.5
  }
}
```

Untuk parser existing, object di field `payload` dapat diberikan langsung ke
`PayloadParser.parse(...)`. Metadata wrapper disimpan untuk audit raw capture.

Receiver skeleton v0 melakukan unwrap ini otomatis untuk replay file dan serial
line source. Jika line memiliki field `payload`, field tersebut yang diparse;
metadata wrapper tetap disimpan di raw log.

## Field Wajib

Payload yang diterima harus memiliki:

- `node_id` atau `n`;
- `timestamp` atau `ts`;
- `sequence` atau `seq`;
- `sensor` atau `s`;
- `temperature_c` atau `tc`;
- `humidity_pct` atau `h`;
- minimal satu sinyal gas: `bme_gas_raw`/`bme`, `co_raw`/`co`,
  `voc_raw`/`v`, atau `gas_raw`/`g`.

`pressure_hpa`/`p` direkomendasikan untuk BME688/BME668 real, tetapi boleh
hilang pada fase awal. Missing pressure harus dicatat sebagai issue/warning.

## Timestamp Policy

- `ts` berasal dari ESP32-C6 jika jam node tersedia.
- `receive_timestamp` berasal dari Raspberry Pi receiver.
- Jika `ts` tidak bisa dipercaya, receiver tetap menyimpan `receive_timestamp`
  dan menandai flag seperti `node_time_unsynced`.
- Timestamp harus memakai ISO-8601 atau epoch seconds/milliseconds.

## Sequence Number Policy

- `seq` naik satu untuk setiap payload per node.
- Gap sequence harus dicatat sebagai `sequence_gap`.
- Sequence reset setelah reboot node boleh terjadi, tetapi harus ditandai
  dengan status/flag jika firmware mendukung.

## Accepted Payloads

Payload dapat diterima untuk preprocessing jika:

- JSON valid atau CSV row bisa diparse;
- field wajib tersedia;
- timestamp valid;
- status/quality tidak menunjukkan error keras;
- nilai sensor wajib berada dalam range validasi dasar;
- minimal ada satu sinyal gas.

Payload dengan missing optional pressure/BME/VOC masih boleh diterima sebagai
warning jika field wajib lain valid.

## Rejected Payloads

Payload harus ditolak atau ditandai invalid keras jika:

- JSON corrupt dan tidak bisa diparse;
- `node_id` kosong;
- `timestamp` hilang atau tidak valid;
- temperature/humidity hilang;
- semua sinyal gas hilang;
- `status=sensor_error` atau flag `sensor_error` muncul;
- nilai sensor keluar dari range validasi keras.

## Output Folder

Receiver live skeleton v0 menulis log ke:

```text
data/real_live_logs/accepted_payloads.jsonl
data/real_live_logs/rejected_payloads.jsonl
data/real_live_logs/receiver_events.jsonl
```

Untuk capture raw jangka panjang, receiver real juga disarankan menulis raw
capture ke:

```text
data/real_raw/<date>_<receiver_id>.jsonl
```

Adapter real offline dapat menulis canonical output ke:

```text
data/real_canonical/<capture_name>_canonical.jsonl
```

Kedua folder berada di bawah `data/`, sehingga ignored dari Git.

## Handoff Ke Hard-Prog

Receiver serial/LoRa skeleton sudah tersedia, tetapi konfigurasi hardware penuh
belum dikerjakan. Hard-prog perlu memastikan payload yang dikirim ESP32-C6
mengikuti kontrak field, timestamp, sequence, status, quality, flags, dan sensor
object di dokumen ini. Hard-prog juga perlu menyesuaikan port, baudrate, mode
Ebyte, wiring, dan service runtime Raspberry Pi.
