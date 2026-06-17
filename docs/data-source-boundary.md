# Data Source Boundary

Dokumen ini menetapkan batas antara data simulasi, data real offline, dan data
real live untuk repo `IIOT-AI-Sensor-Gateway`.

## Prinsip Utama

Repo ini mendukung tiga jalur data yang berbeda:

```text
simulation/reference data
-> simulation adapter
-> canonical schema
-> shared preprocessing/model/decision logic

raw real offline file
-> real file adapter
-> canonical schema
-> shared preprocessing/model/decision logic

real live receiver stream
-> real stream adapter
-> canonical schema
-> shared preprocessing/model/decision logic
```

Sumber data boleh berbeda, tetapi semua jalur harus bertemu di canonical schema
sebelum masuk preprocessing, forecasting, evaluator, model selector, atau
decision layer.

## Simulation/Reference Pipeline

Simulation/reference pipeline dipakai untuk eksperimen awal dan pembuktian alur.
Dataset publik atau dummy tidak boleh dianggap representasi final sensor asli.

Untuk workflow Gary derived saat ini:

- Gary dipakai sebagai proof of concept karena memiliki temperature, humidity,
  CO, LPG, dan smoke.
- `pressure_hpa` pada Gary derived adalah synthetic dan bukan pressure asli.
- `bme_gas_raw` pada Gary derived adalah proxy dari LPG/smoke, bukan pembacaan
  BME688/BME668 asli.
- Model LSTM dan threshold decision layer dari data simulasi belum boleh
  dianggap final untuk sensor real.

## Real Offline File Pipeline

Real offline file pipeline adalah jalur utama untuk training dari data ESP32-C6
sendiri sebelum live gateway matang.

Data raw disimpan dulu sebagai file, misalnya JSONL atau CSV dari Raspberry Pi
receiver/logging. File raw ini belum boleh dinormalisasi, diresampling,
di-windowing, atau diubah menjadi feature model.

Contoh prinsip raw real dataset:

```text
ESP32-C6 + sensor
-> LoRa/Ebyte E32 atau serial debug
-> Raspberry Pi/laptop capture file
-> data/real_raw/*.jsonl atau data/real_raw/*.csv
```

Training real tidak harus memakai live receiver. Data real boleh dikumpulkan
dulu sebagai file agar pipeline dapat diproses ulang, dibandingkan, dan diaudit.

## Real Live Pipeline

Real live pipeline dipakai setelah hardware dan receiver stabil. Jalur ini
bertujuan untuk inference dan decision lokal di Raspberry Pi, bukan untuk
training utama.

Receiver live penuh belum diimplementasikan di repo ini. Receiver live menjadi
handoff untuk integrasi hardware/LoRa/serial, sedangkan repo ini menyediakan
kontrak payload dan shared logic yang akan dipakai setelah payload berubah
menjadi canonical schema.

## Artifact Boundary

Artifact simulasi, data real, dan model harus tetap dipisahkan secara folder:

```text
data/canonical/      canonical dari dataset publik atau simulasi lama
data/derived/        dataset turunan simulasi project-like
data/processed/      processed/window output eksperimen lokal
data/modeling/       dataset training lokal, ignored
data/real_raw/       raw capture dari ESP32-C6/Raspberry Pi, ignored
data/real_canonical/ canonical hasil adapter real offline, ignored
models/              model dan eksperimen lokal, ignored
```

`data/`, `models/`, `*.pt`, dan `*.npz` tidak boleh dipaksa masuk Git. Hasil
penting dari eksperimen harus diringkas di `docs/`.

## Keputusan Saat Ini

- Simulation/reference pipeline tetap dipertahankan untuk riset dan demonstrasi.
- Real offline file pipeline menjadi jalur training real yang paling aman.
- Real live receiver pipeline disiapkan secara kontrak, tetapi receiver penuh
  belum dikerjakan.
- Semua jalur wajib masuk canonical schema sebelum memakai shared core logic.
- Threshold decision layer v1 harus dikalibrasi ulang dengan data sensor real.
