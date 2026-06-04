# Pipeline Raspberry Pi dan Dataset Publik

Pipeline Raspberry Pi adalah preprocessing AI/pre-model. Ini bukan firmware ESP32 dan bukan preprocessing ringan yang berjalan di node.

## Input

Raspberry Pi menerima:

- compact payload dari ESP32-C6 via LoRa/Ebyte E32;
- canonical JSONL dari dataset publik untuk eksperimen;
- data synthetic dari simulator.

## Tahapan

1. Terima compact payload ESP32-C6 atau hasil simulator ESP32-like.
2. Parse payload.
3. Validasi lanjutan temperature, humidity, pressure, gas signal, CO, status, quality, dan sequence gap.
4. Group atau buffer per node/device.
5. Resampling per interval tetap, default 60 detik.
6. Feature extraction: raw value, delta, rolling gas mean/std, missing count, valid ratio, sequence gap count.
7. Normalisasi min-max dari `config/default.toml`.
8. Windowing per node dengan default 12 timestep.
9. Tulis `data/processed/lstm_windows.jsonl`.

## Workflow Gary yang Direkomendasikan

Untuk simulasi dari bacaan sensor sampai window LSTM-ready, gunakan jalur compact payload:

```powershell
py -3.13 run_gateway.py simulate-gary-esp32 --input-csv iot_telemetry_data.csv --output data/simulated/gary_esp32_lora_payloads.jsonl
py -3.13 run_gateway.py run --input-file data/simulated/gary_esp32_lora_payloads.jsonl --output-dir data/processed
py -3.13 run_gateway.py evaluate --canonical data/simulated/gary_esp32_lora_payloads.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_esp32_to_raspi_eval.json --input-source gary_esp32_simulated_lora_payload --simulation-layer esp32_light_preprocessing --gateway-layer raspberry_pi_pre_model_pipeline
```

`convert-gary` tetap tersedia sebagai jalur direct canonical untuk debugging parser dan pipeline, tetapi bukan jalur simulasi hardware utama.

## Beda Dengan ESP32 Preprocessing

ESP32-C6 hanya melakukan preprocessing ringan agar payload stabil untuk LoRa. Raspberry Pi melakukan preprocessing yang dibutuhkan model AI/time-series, termasuk resampling, feature extraction, normalisasi, dan windowing.

## Dataset Roadmap

- Gary Stafford: uji awal pipeline karena tersedia temperature, humidity, CO, LPG, dan smoke. CO dipakai sebagai SEN0377-like feature; LPG/smoke dipakai sebagai gas proxy awal untuk pendekatan BME688 gas/VOC.
- Bristol: dataset utama berikutnya karena lebih mirip BME688-like multi-device indoor sensor dengan temperature, humidity, pressure, gas, dan RSSI.
- GAMS/AQUAIR: referensi lanjutan untuk VOC dan IAQ.

## Batasan Saat Ini

- Input hardware serial/LoRa real belum diikat langsung ke reader Python.
- MQTT publish belum diaktifkan di repo ini.
- Model LSTM belum diimplementasikan.
- Gary tidak memiliki pressure_hpa dan tidak memiliki VOC/BME688 gas asli; gas hanya direpresentasikan oleh proxy LPG/smoke.
