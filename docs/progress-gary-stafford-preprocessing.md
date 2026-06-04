# Progress Gary Stafford Preprocessing

## Tujuan

Laporan ini mencatat progres uji awal pipeline AI sensor pre-model memakai dataset publik Gary Stafford. Targetnya bukan melatih model LSTM, tetapi memastikan data publik dapat dikonversi ke canonical JSONL, diproses, dinormalisasi, dan di-window menjadi input LSTM-ready.

Catatan terbaru: untuk simulasi yang lebih dekat ke hardware, gunakan laporan `docs/progress-gary-esp32-to-raspi-simulation.md`. Jalur baru tersebut memproses Gary sebagai bacaan sensor simulatif di ESP32-C6-like layer, lalu mengirim compact payload ke pipeline Raspberry Pi.

## Konteks Sensor Project

Schema lingkungan ideal project memakai:

- BME688/BME668 untuk temperature, humidity, pressure, dan gas/VOC.
- SEN0377 untuk CO/gas tambahan.

Gary dipakai sebagai uji awal karena memiliki temperature, humidity, CO, LPG, dan smoke. Gary tidak merepresentasikan sensor final 1:1 karena tidak punya pressure_hpa dan tidak punya VOC/BME688 gas asli. Namun, LPG dan smoke adalah gas-like feature yang bisa dipakai sebagai proxy awal untuk menguji pipeline gas/VOC.

## Mapping Dataset

- ts menjadi timestamp.
- device menjadi node_id.
- temp menjadi temperature_c.
- humidity menjadi humidity_pct.
- co menjadi co_raw atau SEN0377-like CO feature.
- lpg dan smoke menjadi gas proxy untuk bme_gas_raw/gas_raw atau pendekatan voc_proxy.
- pressure_hpa unavailable karena Gary tidak punya pressure.
- voc_raw unavailable sebagai VOC/BME688 gas asli.
- light dan motion diabaikan dari fitur utama default.

## Alur Preprocessing

Alur di dokumen ini adalah jalur direct canonical untuk debugging pipeline:

1. CSV Gary dikonversi ke data/canonical/gary_stafford_canonical.jsonl.
2. Payload canonical diparse dan divalidasi.
3. Data digroup per device/node.
4. Data diresampling per 60 detik.
5. Feature extraction membuat raw feature, delta feature, rolling gas mean/std, missing count, valid ratio, dan sequence gap count.
6. Feature dinormalisasi dengan min-max config.
7. Window dibuat dengan 12 timestep per sample.
8. Output ditulis ke data/processed/lstm_windows.jsonl.

Alur simulasi utama yang direkomendasikan sekarang:

```text
Gary CSV -> ESP32-C6-like preprocessing -> compact LoRa payload -> Raspberry Pi pipeline -> LSTM-ready window
```

## Hasil Evaluasi

- pipeline_status: PASS.
- dataset_coverage_status: PARTIAL.
- lstm_readiness: READY_WITH_LIMITATIONS.
- Total data: 405184 baris.
- Jumlah node/device: 3.
- Node/device: 00:0f:00:70:91:0a, 1c:bf:ce:15:ec:4d, b8:27:eb:bf:9d:51.
- Rentang waktu: 2020-07-12T00:01:34.385975+00:00 sampai 2020-07-20T00:03:37.264313+00:00.
- Valid data: 405184.
- Invalid data: 0.
- Missing rate temperature_c: 0.0.
- Missing rate humidity_pct: 0.0.
- Missing rate co_raw: 0.0.
- Missing rate bme_gas_raw: 0.0.
- Missing rate gas_raw: 0.0.
- Missing rate pressure_hpa: 1.0.
- Missing rate voc_raw: 1.0.
- Kolom dipakai: temp, humidity, co, lpg, smoke.
- Kolom proxy: lpg, smoke.
- Kolom diabaikan: light, motion.
- Jumlah window: 34536.
- Shape akhir: [34536, 12, 16].
- NaN count: 0.
- Inf count: 0.

## Interpretasi

Pipeline pre-model berhasil memproses dataset Gary menjadi window LSTM-ready tanpa NaN/Inf dan tanpa invalid row. Karena preprocessing berhasil dan window terbentuk, pipeline_status adalah PASS. Coverage dataset tetap PARTIAL karena pressure_hpa tidak tersedia dan gas BME688/VOC asli hanya diwakili oleh proxy LPG/smoke. Dengan kondisi tersebut, data siap untuk uji awal LSTM sebagai READY_WITH_LIMITATIONS.

## Keterbatasan Gary

- Tidak ada pressure_hpa.
- Tidak ada VOC/BME688 gas asli.
- LPG dan smoke hanya gas proxy awal, bukan pengganti penuh BME688 gas/VOC.
- CO tersedia dan dipakai sebagai SEN0377-like feature, tetapi skala dan karakteristiknya tetap perlu divalidasi terhadap sensor real.
- Dataset publik ini hanya uji awal pipeline, bukan representasi final sensor project.

## Rencana Lanjut

- Gunakan Bristol sebagai dataset utama berikutnya karena lebih mirip BME688-like multi-device indoor sensor dengan temperature, humidity, pressure, gas, dan RSSI.
- Gunakan GAMS/AQUAIR sebagai referensi lanjutan untuk VOC/IAQ.
- Setelah data real ESP32-C6 tersedia, bandingkan missing rate, range, noise, dan stabilitas window terhadap hasil Gary dan Bristol.
- Setelah dataset final cukup, lanjutkan baseline anomaly/forecasting sebelum LSTM penuh.
