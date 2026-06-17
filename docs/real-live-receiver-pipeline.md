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

Repo sekarang menyediakan receiver live skeleton v0. Receiver ini bisa membaca
line-delimited JSON dari replay file atau serial UART/LoRa transparent mode,
memvalidasi payload dengan parser/validator existing, lalu menulis accepted dan
rejected JSONL.

Receiver ini belum menjadi receiver hardware final. Konfigurasi khusus Ebyte
E32/E22, pin M0/M1/AUX, channel, reconnect kompleks, dan tuning deployment tetap
menjadi handoff hard-prog.

Repo saat ini menyediakan:

- firmware skeleton ESP32-C6;
- compact payload contract;
- parser compact payload;
- validator dan preprocessing core;
- receiver skeleton `receive-real-live`;
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

## Command

Replay fixture tanpa hardware:

```powershell
py -3.13 run_gateway.py receive-real-live --replay-file tests/fixtures/real_payload_samples.jsonl --output-dir data/real_live_logs --max-messages 10
```

Serial UART/LoRa transparent mode:

```powershell
py -3.13 -m pip install -e ".[serial]"
py -3.13 run_gateway.py receive-real-live --port /dev/serial0 --baudrate 9600 --timeout 1.0 --output-dir data/real_live_logs
```

Di Windows, ganti port dengan `COMx`, misalnya `COM5`.

## Output Receiver v0

Receiver v0 menulis:

```text
data/real_live_logs/accepted_payloads.jsonl
data/real_live_logs/rejected_payloads.jsonl
data/real_live_logs/receiver_events.jsonl
```

Accepted payload berisi metadata receive, source, raw line, payload compact yang
sudah di-unwrap, node, room, sequence, dan validation issues soft. Rejected
payload berisi metadata receive, raw line, kategori error, pesan error, dan
validation issues jika parse berhasil tetapi validasi gagal.

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

- Receiver v0 belum mengatur konfigurasi Ebyte E32/E22 secara hardware-specific.
- Belum ada MQTT publish.
- Belum ada backend/dashboard/OpenClaw runtime.
- Threshold decision layer v1 belum final sampai dikalibrasi dengan sensor real.
