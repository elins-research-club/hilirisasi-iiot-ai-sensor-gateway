# Riset Dataset Environmental IoT

Tanggal keputusan: 10 Juli 2026.

## Prinsip Keputusan

Tidak ada satu dataset publik yang sekaligus cocok dengan seluruh sensor RAB, unit, kalibrasi, lokasi, dan pola daya node proyek. Strategi yang dipilih adalah **adapter per lane**, bukan menggabungkan kolom berbeda menjadi satu dataset seolah-olah berasal dari perangkat yang sama.

## Prioritas Dataset

### 1. Project Real Sensor Capture — prioritas tertinggi

Tujuan:

- kalibrasi sensor aktual;
- model deployment;
- node/battery health;
- sequence/reboot/transport behavior;
- field-condition evaluation.

Ini satu-satunya lane yang dapat membuktikan performa hardware proyek.

### 2. UCI Air Quality — reference gas/drift

Kelebihan:

- time series panjang;
- CO/NOx/NO₂ reference;
- temperatur/RH;
- sensor drift/cross-sensitivity.

Batasan:

- bukan sensor RAB;
- CO reference memakai mg/m³, bukan ppm;
- NO₂ reference bukan sinyal analog SEN0574;
- missing marker `-200`;
- tidak memiliki O₃ reference, CO₂, PM, pressure, atau telemetry daya yang sesuai proyek.

Keputusan: adapter tersedia; unit asli dipertahankan sebagai `reference.*`.

### 3. Bristol BME680 Smart Building — indoor multi-device

Kelebihan:

- beberapa device;
- periode panjang;
- temperatur, kelembapan, tekanan;
- cocok untuk leave-device-out/drift study.

Batasan:

- BME680 bukan chip-identical BME688;
- source Gas adalah IAQ index, bukan gas resistance;
- tidak memiliki gas RAB lengkap atau telemetry daya;
- archive besar dan tidak diunduh otomatis.

Keputusan: adapter tersedia; gas disimpan sebagai reference IAQ index.

### 4. Multi-site reference networks

OpenAQ/EPA-class data tetap relevan untuk:

- seasonal/weather/site diversity;
- CO/O₃/NO₂/PM reference;
- leave-site-out evaluation.

Batasan:

- data stasiun reference bukan low-cost sensor node;
- indoor CO₂/BME/INA tidak tersedia;
- API/license/volume berbeda antar sumber.

Keputusan: belum dibuat adapter pada wave ini. Tambahkan hanya setelah pollutant/site/time range dipilih jelas.

### 5. Gary Stafford — regression only

Kelebihan:

- sudah tersedia pada workflow lama;
- berguna untuk parser/preprocessing regression.

Batasan:

- pressure turunan sintetis;
- gas source bukan sensor RAB;
- tidak layak menjadi main training/evaluation lane.

Keputusan: tetap historical/reference, tidak dipromosikan.

### 6. Power/Battery External Dataset

Dataset battery-aging publik umumnya berbeda chemistry, load, regulator, dan duty cycle. Dataset household power juga tidak mewakili INA226 node.

Keputusan: belum ada dataset eksternal yang dipromosikan. Gunakan rules dan project power traces terlebih dahulu.

## Canonical Coverage Matrix

| Lane | T/H/P | CO | NO₂ | O₃ | CO₂ | PM | BME gas | V/I/P |
|---|---|---|---|---|---|---|---|---|
| UCI Air Quality | T/H | reference unit asli | reference unit asli | tidak ada | tidak ada | tidak ada | sensor-array response saja | tidak ada |
| Bristol BME680 | T/H/P | tidak ada | tidak ada | tidak ada | tidak ada | tidak ada | IAQ index reference | tidak ada |
| Gary | T/H | proxy legacy | tidak ada | tidak ada | tidak ada | tidak ada | proxy legacy | tidak ada |
| Project real | sesuai sensor valid | ya | mV/ratio | ya | ya | ya | ohm | ya |

## Data Quality Rules

- missing menjadi `null`;
- unit sumber tidak diganti diam-diam;
- provenance per row;
- timezone assumption dicatat;
- raw source tidak ditimpa;
- adapter output terpisah dari raw;
- split time-ordered;
- normalizer fit pada train saja;
- leave-device/site-out bila metadata memungkinkan;
- synthetic/reference tidak boleh masuk laporan field performance.

## Implementation

- catalog: `datasets/catalog.json`;
- safe downloader: `scripts/download_dataset.py`;
- UCI adapter: `src/iiot_ai_sensor_gateway/adapters/uci_air_quality.py`;
- Bristol adapter: `src/iiot_ai_sensor_gateway/adapters/bristol_bme680.py`;
- docs command: `docs/dataset-catalog-and-adapters.md`.

## Status

Sudah:

- riset dan catalog;
- adapter unit-tested;
- missing/provenance policy;
- safe download helper.

Belum:

- download dataset penuh;
- checksum artifact dikunci;
- adapter full archive validation;
- multi-site adapter;
- real sensor capture;
- baseline/model benchmark penuh.
