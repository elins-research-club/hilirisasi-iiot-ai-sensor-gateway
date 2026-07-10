# Preprocessing ESP32-C6

## Tujuan

ESP32-C6 mengurangi noise dan membawa status sensor tanpa menghilangkan data penting. Preprocessing device tetap ringan dan deterministik.

## Langkah

1. `SensorReader` membaca setiap lane.
2. Nilai tidak tersedia tetap NaN internal.
3. `Preprocessor` memeriksa sanity range per sensor.
4. Nilai valid masuk moving average lima sampel.
5. Status per sensor disimpan.
6. Global status menjadi `ok`, `degraded`, atau `sensor_error`.
7. Encoder mengubah NaN menjadi JSON `null`.
8. Payload truncation membatalkan transmit.

## Field dan Range Host-Contract

| Field | Sanity range |
|---|---|
| temperatur | -10–80 °C |
| kelembapan | 0–100 % |
| tekanan | 800–1200 hPa |
| BME gas | 100–10,000,000 ohm |
| CO | 0–1000 ppm |
| NO₂ mV | 0–3300 mV |
| NO₂ ratio | 0–20 bila tersedia |
| O₃ | 0–10 ppm |
| CO₂ | 0–10,000 ppm |
| PM | 0–5000 µg/m³ |
| voltage | 0–60 V |
| current | -20,000–20,000 mA |
| power | -1,000,000–1,000,000 mW |

Range ini adalah sanity gate, bukan regulatory alert threshold.

## Partial Validity

Satu sensor gagal tidak memaksa semua field menjadi invalid. Contoh:

```text
BME688 valid
CO valid
O3 unavailable
-> st=degraded
-> q=partial
-> o3=null
-> flag o3_invalid
```

## NO₂

Firmware menghasilkan ADC mV. Rasio hanya tersedia setelah baseline commissioning. Tidak ada conversion ke ppm pada firmware foundation.

## INA226

Firmware memiliki register path, tetapi current LSB masih commissioning default. Nilai produksi menunggu shunt resistor aktual dan cross-check alat referensi.

## Mock vs Hardware

Mock:

- seluruh field valid;
- deterministic variation;
- dipakai CI/end-to-end contract.

Hardware:

- SEN0466, SEN0574, SEN0321, INA226, SC16IS752, MH-Z19, PMS7003T, dan E32 memiliki read/transport path yang fail-closed;
- MH-Z19/PMS menandai warm-up secara eksplisit dan tetap `null` sampai siap;
- BME688 memiliki adapter Bosch SensorAPI; profile resmi compile-validated, sedangkan default tetap disabled bila dependency lokal tidak tersedia;
- seluruh jalur baru compile-tested, belum hardware-verified;
- tidak ada placeholder yang dipromosikan sebagai pembacaan real.

## Batasan

Moving average tidak menggantikan:

- kalibrasi;
- warm-up state;
- compensation model;
- outlier handling gateway;
- resampling/time alignment;
- model AI.
