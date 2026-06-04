# Preprocessing ESP32-C6

Dokumen ini menjelaskan preprocessing ringan di node ESP32-C6. Ini berbeda dari preprocessing AI/pre-model di Raspberry Pi.

## Peran ESP32-C6

ESP32-C6 bertugas:

- membaca BME688/BME668;
- membaca SEN0377;
- melakukan validasi awal;
- melakukan smoothing ringan jika diperlukan;
- membuat payload compact;
- mengirim payload melalui LoRa/Ebyte E32.

ESP32-C6 tidak menjalankan LSTM, anomaly detection, forecasting, MQTT broker, backend, dashboard, atau OpenClaw.

## Sensor Target

- BME688/BME668: temperature, humidity, pressure, gas/VOC-like raw value.
- SEN0377: CO/gas tambahan.

## Aturan Preprocessing Ringan

- Range check suhu, humidity, pressure, BME gas raw, dan CO raw.
- Missing check untuk field wajib: node_id, room_id, timestamp/uptime, sequence, temperature, humidity, pressure, BME gas, dan CO.
- Sensor error flag jika pembacaan gagal atau nilai tidak masuk akal.
- Moving average ringan 3 sampai 5 sampel jika noise terlalu besar.
- Pembulatan nilai agar payload LoRa kecil.
- Sequence monoton per node agar Raspberry Pi bisa mendeteksi packet loss.
- Status dan quality dikirim bersama payload.

## Payload Compact ESP32-C6

Contoh payload:

```json
{"v":1,"n":"node_01","r":"room_A","ts":42,"seq":7,"st":"ok","q":"valid","f":"","s":{"tc":29.20,"h":65.40,"p":1008.30,"bme":18125.00,"co":0.01200}}
```

Alias field:

- `v`: schema version.
- `n`: node_id.
- `r`: room_id.
- `ts`: timestamp atau uptime seconds jika RTC belum tersedia.
- `seq`: sequence number.
- `st`: status, contoh `ok` atau `sensor_error`.
- `q`: quality, contoh `valid` atau `invalid`.
- `f`: flags, dipisahkan `|` jika lebih dari satu.
- `s.tc`: temperature_c.
- `s.h`: humidity_pct.
- `s.p`: pressure_hpa.
- `s.bme`: bme_gas_raw.
- `s.co`: co_raw.

## Batasan

Preprocessing ESP32 hanya menjaga kualitas payload awal. Raspberry Pi tetap wajib melakukan validasi lanjutan, logging, resampling, feature extraction, normalisasi, windowing, dan AI inference.

## Simulator Gary ESP32-Like

Untuk menguji jalur dari awal tanpa hardware, repo menyediakan simulator Python yang meniru preprocessing ringan ESP32-C6 pada dataset Gary:

- membaca baris CSV sebagai pembacaan sensor;
- mapping `temp`, `humidity`, `co`, `lpg`, dan `smoke`;
- membuat gas proxy `bme_gas_raw` dari rata-rata LPG/smoke;
- melakukan range check dan missing check;
- menerapkan moving average 3 sampel per device;
- membuat sequence number per device;
- mengirim compact payload dengan flag `pressure_unavailable`, `voc_not_native`, dan `gas_proxy_from_lpg_smoke`.

Command:

```powershell
py -3.13 run_gateway.py simulate-gary-esp32 --input-csv iot_telemetry_data.csv --output data/simulated/gary_esp32_lora_payloads.jsonl
```

Simulator ini hanya untuk laptop/testing. Implementasi hardware tetap berada di firmware C++ folder `firmware/esp32-c6-sensor-node/`.
