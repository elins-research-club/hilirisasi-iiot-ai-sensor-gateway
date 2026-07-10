# Riset Sensor Node ESP32-C6 — RAB MBKM IIoT

Dokumen ini hanya membahas sensor environment node. Sensor presence untuk subsistem kamera berada di luar scope repo ini.

## Ringkasan RAB dan Kontrak

| Sensor | Interface target | Field canonical | Semantik |
|---|---|---|---|
| BME688 | I²C `0x77` | `temperature_c`, `humidity_pct`, `pressure_hpa`, `bme_gas_ohm` | gas resistance mentah/terkalibrasi driver, bukan CO₂ |
| SEN0466 | I²C `0x74` | `co_ppm` | CO digital ppm |
| SEN0574 | ADC | `no2_raw_mv`, `no2_ratio` | sinyal NO₂ kualitatif |
| SEN0321 | I²C `0x73` | `o3_ppm` | O₃ |
| MH-Z19B/C | UART 9600 | `co2_ppm` | CO₂ NDIR |
| PMS7003T | UART 9600 | `pm1_ug_m3`, `pm25_ug_m3`, `pm10_ug_m3` | particulate matter |
| INA226 | I²C `0x40` | `battery_voltage`, `current_ma`, `power_mw` | telemetry daya node |

Nama modul CO analog pada desain awal adalah mismatch legacy dan tidak digunakan pada path aktif.

## 1. BME688

- Mengukur temperatur, kelembapan, tekanan, dan gas resistance.
- Alamat umum `0x76`/`0x77`; profile proyek memakai `0x77`.
- Driver produksi harus menjaga unit pressure hPa dan gas resistance ohm.
- IAQ/eCO₂ dari algoritme BSEC adalah output olahan, bukan pengganti CO₂ NDIR.
- Heater profile dan BSEC state harus disimpan/ditandai bila digunakan.
- Adapter resmi Bosch BME68x SensorAPI forced-mode sudah tersedia dan profile `hardware-bme68x` compile-validated dengan source resmi. Dependency tetap tidak dipasang otomatis; profile fail-fast bila header tidak tersedia, dan pembacaan hardware nyata masih open.

Acceptance hardware:

- chip ID dan alamat benar;
- finite values;
- pressure dan humidity masuk sanity range;
- gas heater/status valid;
- reboot tidak menghilangkan state kalibrasi bila BSEC dipakai.

## 2. SEN0466 CO

- Target digital terkalibrasi, default I²C `0x74`.
- Output contract adalah `co_ppm`.
- Jangan memakai analog proxy lama sebagai CO ppm.
- Bila dua unit berada pada bus yang sama, alamat harus dibuat unik dan diuji dengan scan assembled board.
- Jalur I²C digital dengan request/response, checksum, gas-type check, decimal scaling, dan range gate sudah diimplementasikan serta compile-tested. Hardware fisik belum diverifikasi.

Acceptance hardware:

- pembacaan ID/status device;
- concentration finite dalam range;
- warm-up dan error code diteruskan sebagai quality/status;
- cross-check terhadap reference/co-location.

## 3. SEN0574 NO₂

- Interface analog 3.3–5 V.
- Contract hanya `no2_raw_mv` dan optional `no2_ratio`.
- Tidak boleh menghasilkan NO₂ ppm tanpa calibration model terhadap referensi.
- Firmware memakai GPIO0 dan ADC calibration; GPIO ini berbeda dari SDA GPIO6.
- `no2_ratio` tetap `null` sampai baseline commissioning tersedia.

Acceptance hardware:

- ADC calibrated mV;
- baseline/reference disimpan per node/sensor;
- temperature/RH compensation dievaluasi;
- qualitative band ditentukan dari data co-location, bukan angka katalog saja.

## 4. SEN0321 O₃

- I²C, alamat selectable `0x70`–`0x73`; profile memakai `0x73`.
- Output `o3_ppm` hanya setelah driver/protocol dan warm-up terpenuhi.
- Bila dua unit dipakai, alamat harus unik.
- Jalur automatic mode/read, konversi ppb ke ppm, dan range gate sudah diimplementasikan serta compile-tested. Hardware fisik belum diverifikasi.

Acceptance hardware:

- preheat sesuai vendor;
- address/protocol read valid;
- sensor error menghasilkan null + status, bukan nol;
- co-location/reference check.

## 5. MH-Z19B/C CO₂

- UART TTL 9600 8N1, rail 5 V.
- Peak supply harus ditangani oleh rail yang sesuai.
- Preheat/stabilization membuat power-cycle singkat tidak valid.
- Output `co2_ppm` dan quality/warm-up status.
- Strategi serial proyek: channel A pada bridge dual-UART eksternal.
- Driver SC16IS752 channel A, request/response Winsen, checksum, timeout, range gate, dan warm-up 180 s sudah diimplementasikan serta compile-tested. Hardware belum diverifikasi.

Acceptance hardware:

- frame checksum valid;
- warm-up state eksplisit;
- ABC/zero calibration ditentukan berdasarkan lokasi;
- supply drop tidak merusak frame;
- reconnect/recovery diuji.

## 6. PMS7003T

- UART TTL 9600, rail 5 V untuk fan/laser.
- Output utama PM1/PM2.5/PM10 atmospheric.
- Setelah wake, data belum langsung stabil; quality harus menandai post-sleep warm-up.
- Strategi serial proyek: channel B pada bridge dual-UART eksternal.
- Driver SC16IS752 channel B, wake/passive mode, passive read, header/length/checksum frame, dan warm-up 30 s sudah diimplementasikan serta compile-tested. Hardware belum diverifikasi.

Acceptance hardware:

- header/length/checksum frame valid;
- CF=1 dan atmospheric field tidak tertukar;
- warm-up setelah sleep diuji;
- fan/laser error dan stale data dideteksi;
- co-location dengan reference PM.

## 7. INA226

- I²C `0x40`, bus voltage/current/power.
- Firmware sudah menulis calibration register dan membaca bus/current/power register.
- Current LSB yang ada masih commissioning default dan harus dihitung ulang dari shunt resistor serta arus maksimum aktual.
- Nilai sign current harus dipertahankan bila sistem mendukung charge/discharge direction.

Acceptance hardware:

- shunt value dan tolerance diketahui;
- calibration register dihitung ulang;
- zero offset dibandingkan multimeter;
- bus voltage/current/power dibandingkan instrumen referensi;
- alert/threshold baru dipakai setelah acceptance.

## 8. Bus Mapping

### I²C

| Address | Perangkat |
|---|---|
| `0x77` | BME688 |
| `0x74` | SEN0466 |
| `0x73` | SEN0321 |
| `0x40` | INA226 |
| `0x48` | bridge dual-UART eksternal |

Risiko:

- lebih dari satu SEN0466/SEN0321 memerlukan alamat unik;
- address scan assembled board wajib;
- bus pull-up/cable length/noise harus diuji.

### Serial

```text
UART1 -> Ebyte E32
bridge channel A -> MH-Z19B/C
bridge channel B -> PMS7003T
```

Bridge dipilih agar tiga perangkat serial tidak dipaksakan ke resource UART internal. Kode memakai address `0x48`, crystal 1.8432 MHz, FIFO level, dan timeout bounded. Pilihan ini masih blocker PCB/hardware, bukan hasil uji fisik.

## 9. Rail dan Power

- MH-Z19 dan PMS7003T memerlukan 5 V.
- Enable pin rail belum dikunci (`-1` di config).
- INA226 memonitor voltage/current/power, tetapi tidak menggantikan pengukuran rail eksternal saat commissioning.
- Battery life tidak boleh dihitung hanya dari current katalog; ukur idle, warm-up, sample, LoRa TX, dan sleep pada node aktual.

## 10. Status Implementasi

| Bagian | Status host | Status hardware |
|---|---|---|
| full compact v2 mock | build + parser test lulus | belum diuji |
| SEN0574 ADC | compile lulus | belum diuji |
| INA226 register path | compile lulus | belum diuji/kalibrasi |
| BME688 | adapter SensorAPI + official source compile lulus; default profile tetap lane-disabled | hardware read/soak belum dilakukan |
| SEN0466 | protocol/checksum path compile | sensor fisik belum diuji |
| SEN0321 | automatic-read path compile | sensor fisik belum diuji |
| SC16IS752 + MH-Z19/PMS | bridge, checksum/frame, timeout, warm-up compile lulus | bridge/sensor/rail belum diuji |
| E32 AUX/error handling | compile lulus | link RF belum diuji |
| flash profile 8/16 MB | build lulus | chip fisik belum diverifikasi |

## 11. Dataset dan Model

Dataset publik membantu metodologi, tetapi tidak menggantikan capture hardware:

- UCI Air Quality: reference gas/drift; unit asli dipertahankan;
- Bristol BME680: indoor multi-device; gas adalah IAQ index;
- Gary: regression-only;
- real project capture: lane utama untuk kalibrasi dan model deployment.

Lihat:

- `docs/dataset-catalog-and-adapters.md`
- `datasets/catalog.json`
- `docs/modeling-fits-river-decision.md`
