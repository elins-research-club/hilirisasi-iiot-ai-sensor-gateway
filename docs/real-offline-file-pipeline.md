# Real Offline File Pipeline

Real offline file pipeline adalah workflow untuk memakai data mentah yang
dikumpulkan sendiri dari ESP32-C6/Raspberry Pi sebagai bahan preprocessing,
training, evaluation, dan model selection.

## Tujuan

Workflow ini menjembatani hardware real dan eksperimen model tanpa harus
menunggu live receiver produksi selesai.

```text
ESP32-C6 + BME688/BME668 + SEN0377
-> LoRa/Ebyte E32 atau serial debug
-> Raspberry Pi/laptop capture file
-> raw real JSONL/CSV
-> real file adapter
-> canonical schema
-> shared preprocessing
-> real processed windows
-> real forecast dataset
-> train/evaluate real model
-> model selection report
-> decision threshold validation
```

## Raw Real Dataset

Raw real dataset harus disimpan apa adanya atau seminimal mungkin berubah.
Raw berarti belum:

- dinormalisasi;
- diresampling;
- dibuat delta/rolling feature;
- di-windowing;
- diisi missing value secara permanen;
- dipakai sebagai `X/y` model.

Isi raw yang direkomendasikan:

- `receive_timestamp` dari Raspberry Pi/laptop;
- `raw_payload` asli jika payload diterima sebagai string;
- parsed compact payload jika receiver sudah bisa parse JSON;
- `gateway_id`, `node_id`, `room_id` jika tersedia;
- `sequence`;
- `status`, `quality`, `flags`;
- nilai sensor asli;
- metadata radio seperti RSSI/SNR jika tersedia.

Folder target:

```text
data/real_raw/
```

Folder ini di-ignore dari Git karena berisi data eksperimen/hardware lokal.

## Real File Adapter

Real file adapter bertugas membaca raw JSONL/CSV dan menulis canonical JSONL.
Adapter boleh membersihkan format field, tetapi tidak boleh mengubah data mentah
menjadi feature model.

Output yang disarankan:

```text
data/real_canonical/<capture_name>_canonical.jsonl
```

## Training Dari Data Real Offline

Setelah canonical JSONL terbentuk, workflow memakai shared core yang sama dengan
simulation/reference pipeline:

```text
canonical real JSONL
-> run gateway preprocessing
-> LSTM-ready windows
-> prepare forecast dataset
-> train/evaluate model
-> select best model
```

Training real boleh dijalankan di laptop/server. Raspberry Pi sebaiknya fokus
untuk capture, preprocessing ringan/gateway, dan inference setelah model cukup
stabil.

## Validasi Decision Layer

Decision layer v1 yang ada sekarang masih rule-based dan harus dikalibrasi ulang
dengan data real. Validasi minimal dari real offline file:

- range normal temperature, humidity, pressure, BME gas raw, dan CO;
- missing rate;
- sequence gap rate;
- respons gas/CO terhadap kondisi ruangan;
- false warning/critical rate pada kondisi normal;
- target mana yang forecast-nya lebih buruk dari baseline.

## Batasan

- Workflow ini tidak membutuhkan receiver live penuh.
- Workflow ini belum mengirim MQTT dan belum masuk backend/dashboard.
- Data real offline adalah bahan training/evaluation, bukan live decision final.
- Model hasil training real tetap harus dievaluasi terhadap baseline dan
  didokumentasikan sebelum dipakai inference.
