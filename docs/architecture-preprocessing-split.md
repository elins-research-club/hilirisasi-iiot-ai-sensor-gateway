# Pembagian Preprocessing ESP32-C6 dan Raspberry Pi

## Keputusan Arsitektur

Preprocessing dibagi menjadi dua layer:

```text
ESP32-C6 sensor node
-> preprocessing ringan firmware
-> compact payload LoRa/Ebyte E32
-> Raspberry Pi gateway
-> preprocessing AI/pre-model
-> window LSTM-ready atau AI inference
```

## ESP32-C6: Preprocessing Ringan

Bahasa: C/C++ dengan Arduino framework atau ESP-IDF.

Tugas:

- membaca BME688/BME668 dan SEN0377;
- range check kasar;
- missing check;
- sensor error flag;
- moving average kecil;
- pembulatan nilai;
- sequence number;
- compact payload;
- kirim LoRa.

Output ESP32 adalah payload yang sudah cukup bersih untuk diterima gateway, tetapi belum siap langsung untuk LSTM.

## Raspberry Pi: Preprocessing AI/Pre-Model

Bahasa: Python.

Tugas:

- parse payload dari ESP32 atau dataset publik;
- validasi lanjutan;
- deteksi sequence gap, duplicate, out-of-order, dan node silent;
- logging/audit;
- grouping atau buffer per node;
- resampling ke interval tetap;
- feature extraction;
- normalisasi;
- windowing `[samples, timesteps, features]`;
- AI inference atau export dataset LSTM-ready.

## Kenapa Python Tetap Ada

Python tidak di-upload ke ESP32. Python dipakai di Raspberry Pi/laptop karena pipeline AI, dataset publik, evaluasi, dan windowing time-series lebih cocok dijalankan di perangkat gateway atau mesin training.

## Simulasi End-to-End di Laptop

Karena hardware belum selalu tersedia, repo menyediakan simulator ESP32-like untuk dataset Gary:

```text
Gary CSV
-> simulasi sensor read
-> ESP32-like range/missing check, moving average, sequence, flags
-> compact payload LoRa JSONL
-> parser dan pipeline Raspberry Pi
-> window LSTM-ready
```

Command utama:

```powershell
py -3.13 run_gateway.py simulate-gary-esp32 --input-csv iot_telemetry_data.csv --output data/simulated/gary_esp32_lora_payloads.jsonl
py -3.13 run_gateway.py run --input-file data/simulated/gary_esp32_lora_payloads.jsonl --output-dir data/processed
```

Simulator ini bukan firmware final. Fungsinya menjaga kontrak data agar pipeline Python di laptop/Raspberry Pi sudah siap menerima payload compact dari ESP32-C6 real.

## Folder Terkait

- `firmware/esp32-c6-sensor-node/`: skeleton firmware ESP32-C6.
- `src/iiot_ai_sensor_gateway/`: pipeline Python Raspberry Pi/dataset.
- `docs/esp32-preprocessing.md`: detail preprocessing ringan firmware.
- `docs/raspberry-pi-pipeline.md`: detail preprocessing AI/pre-model.
