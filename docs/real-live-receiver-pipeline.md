# Real Live Receiver Pipeline

Real live receiver pipeline adalah workflow inference lokal di Raspberry Pi saat
ESP32-C6 dan LoRa/Ebyte E32 sudah siap dipakai terus-menerus.

## Tujuan

Pipeline ini digunakan untuk membaca data real secara live, membangun window
terbaru, menjalankan model terlatih, dan menghasilkan decision output lokal.

```text
ESP32-C6 + sensor
-> LoRa/Ebyte E32
-> Raspberry Pi receiver
-> compact payload / wrapped payload
-> canonical sensor reading
-> shared preprocessing
-> latest window
-> load trained model
-> predict forecast
-> local decision payload
```

## Status Implementasi

Receiver live penuh belum diimplementasikan di repo ini. Bagian ini menjadi
handoff untuk integrasi hardware/LoRa/serial. Repo ini saat ini menyediakan:

- firmware skeleton ESP32-C6;
- compact payload contract;
- parser compact payload;
- validator dan preprocessing core;
- forecasting/evaluator/model selector;
- forecast payload dan decision payload v1;
- contoh fixture real-like untuk test kontrak.

## Tanggung Jawab Receiver Live

Receiver live harus menangani:

- koneksi serial/LoRa ke Raspberry Pi;
- baca payload per baris atau per paket;
- decode payload;
- timestamp receive dari Raspberry Pi;
- accepted/rejected payload log;
- sequence gap dan corrupt packet;
- reconnect/retry jika perangkat putus;
- output raw capture ke `data/real_raw/` untuk audit.

Receiver live bukan model AI dan bukan backend. Receiver hanya membawa data real
ke canonical schema atau raw capture yang bisa diproses ulang.

## Hubungan Dengan Training

Training model tidak harus berjalan dari stream live. Alur yang lebih aman:

```text
live receiver capture
-> raw real file
-> offline preprocessing/training/evaluation
-> selected model
-> deploy model ke Raspberry Pi
-> live inference
```

Dengan cara ini model dapat diuji ulang dan dibandingkan terhadap baseline
sebelum dipakai untuk decision lokal.

## Output Live Inference

Output lokal yang disarankan:

```text
models/lstm_forecast/latest/forecast_payloads.jsonl
models/lstm_forecast/latest/decision_payloads.jsonl
logs/gateway/*.log
```

Saat integrasi backend/MQTT dilakukan nanti, output ini dapat menjadi dasar
payload MQTT. Tahap tersebut berada di luar scope task ini.

## Batasan

- Belum ada receiver serial/LoRa penuh.
- Belum ada MQTT publish.
- Belum ada backend/dashboard/OpenClaw runtime.
- Threshold decision layer v1 belum final sampai dikalibrasi dengan sensor real.
