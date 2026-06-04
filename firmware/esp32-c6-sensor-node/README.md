# ESP32-C6 Sensor Node Firmware

Firmware skeleton ini adalah sisi ESP32-C6 untuk Industrial Environment Monitoring. Tugasnya hanya akuisisi sensor, preprocessing ringan, membuat payload compact, lalu mengirim payload melalui LoRa/Ebyte E32 ke Raspberry Pi gateway.

Firmware ini tidak menjalankan AI, LSTM, MQTT, backend, dashboard, atau computer vision.

## Target Sensor

- BME688/BME668: temperature, humidity, pressure, gas/VOC-like raw value.
- SEN0377: CO/gas tambahan melalui analog input.
- Ebyte E32: transport LoRa UART ke Raspberry Pi gateway.

## Pembagian Preprocessing

ESP32-C6 melakukan preprocessing ringan:

- sensor read;
- range check kasar;
- sensor error flag;
- missing check;
- moving average kecil 3 sampel;
- pembulatan nilai di payload;
- sequence number;
- status, quality, dan flags;
- compact JSON payload.

Raspberry Pi tetap melakukan preprocessing AI/pre-model:

- parse payload;
- validasi lanjutan;
- logging;
- grouping/buffer per node;
- resampling;
- feature extraction;
- normalisasi;
- windowing;
- AI inference atau dataset LSTM-ready.

## Build Awal

Skeleton default memakai mock sensor agar struktur firmware bisa diuji tanpa hardware sensor. PlatformIO project ini memakai framework ESP-IDF karena board ESP32-C6 DevKitC-1 pada PlatformIO package saat ini tidak mendukung Arduino framework.

    cd firmware/esp32-c6-sensor-node
    pio run
    pio run -t upload
    pio device monitor

Jika PlatformIO belum terpasang, install PlatformIO Core atau gunakan ESP-IDF langsung dengan porting file `src/`.

## Aktivasi Hardware Real

Di `platformio.ini`, ubah:

    -D IIOT_USE_MOCK_SENSORS=0

Lalu lengkapi `src/sensors.cpp` dengan library BME688/BME668 pilihan dan kalibrasi SEN0377.

## Payload Compact

Contoh payload dari ESP32-C6:

```json
{"v":1,"n":"node_01","r":"room_A","ts":42,"seq":7,"st":"ok","q":"valid","f":"","s":{"tc":29.20,"h":65.40,"p":1008.30,"bme":18125.00,"co":0.01200}}
```

Alias sensor:

- `tc`: temperature_c.
- `h`: humidity_pct.
- `p`: pressure_hpa.
- `bme`: bme_gas_raw.
- `co`: co_raw.

Raspberry Pi parser sudah menerima alias tersebut melalui pipeline Python.

## Catatan Hardware

Pin di `include/config.h` masih default awal dan harus disesuaikan dengan wiring real. Jangan anggap pin final sebelum diagram wiring dikunci.

