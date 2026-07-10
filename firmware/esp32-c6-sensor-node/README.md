# ESP32-C6 Sensor Node Firmware

Firmware ini membaca sensor lingkungan, melakukan validasi dan moving average ringan, membentuk `compact_sensor.v2`, lalu mengirimkannya melalui Ebyte E32 ke Raspberry Pi gateway. Model AI, MQTT, backend, dashboard, dan computer vision tidak berjalan di ESP32-C6.

## Target RAB dan Status Kode

| Perangkat | Interface | Field v2 | Status kode |
|---|---|---|---|
| BME688 | I²C `0x77` | `tc,h,p,bme` | adapter Bosch BME68x SensorAPI forced-mode tersedia dan fail-closed; profile `hardware-bme68x` compile-validated dengan source resmi, tetapi belum diuji pada sensor fisik |
| SEN0466 CO | I²C `0x74` | `co` ppm | request/response digital, checksum, gas type, skala desimal, dan range validation tersedia |
| SEN0574 NO₂ | ADC GPIO0 | `n2mv,n2r` | ADC oneshot + curve-fitting mV tersedia; rasio tetap `null` sampai baseline commissioning diukur |
| SEN0321 O₃ | I²C `0x73` | `o3` ppm | automatic mode/read tersedia; nilai ppb dikonversi ke ppm |
| MH-Z19B/C | SC16IS752 channel A | `co2` ppm | command/response Winsen, checksum, timeout, range check, dan warm-up 180 s tersedia |
| PMS7003T | SC16IS752 channel B | `pm1,pm25,pm10` | wake + passive mode, passive read, frame/header/length/checksum validation, dan warm-up 30 s tersedia |
| INA226 | I²C `0x40` | `bv,bi,bp` | calibration-register dan register read tersedia; `current LSB` final menunggu nilai shunt aktual |
| Ebyte E32 | UART1 + AUX | transport | AUX wait, timeout, dan error handling tersedia |

Semua jalur hardware di atas baru **compile-tested**, belum membuktikan pembacaan sensor, wiring, rail, bridge, radio, atau kalibrasi fisik. Sensor yang tidak siap tetap menghasilkan `null` dan status parsial; firmware tidak membuat nilai pengganti.

Modul CO analog legacy bukan target hardware. Sensor presence milik kamera juga tidak berada di node ini.

## Profil Build

Mock adalah default CI dan tidak memerlukan hardware:

```bash
cd firmware/esp32-c6-sensor-node
/home/ubuntu/.venvs/platformio/bin/pio run -e mock
```

Profile hardware mengompilasi SEN0466, SEN0574, SEN0321, INA226, SC16IS752, MH-Z19, PMS7003T, dan E32. BME688 sengaja tetap disabled sampai source resmi tersedia:

```bash
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware
```

### Mengaktifkan BME688

Dependency tidak dipasang otomatis. Perintah yang perlu dijalankan user:

```bash
cd firmware/esp32-c6-sensor-node
mkdir -p lib
git clone --depth 1 https://github.com/boschsensortec/BME68x_SensorAPI.git lib/BME68x_SensorAPI
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware-bme68x
```

`hardware-bme68x` menetapkan `IIOT_ENABLE_BOSCH_BME68X=1`. Build akan berhenti dengan error bila header resmi `bme68x.h` belum tersedia; jadi BME688 tidak dapat aktif diam-diam tanpa dependency.

Profile `mock` dikunci 8 MB dan `hardware` 16 MB sesuai target modul RAB. Build bukan bukti ukuran flash fisik. Verifikasi chip aktual tetap wajib sebelum upload.

## Bus, Pin, dan UART

```text
I2C0: SDA GPIO6, SCL GPIO7, 100 kHz
BME688       0x77
SEN0466      0x74
SEN0321      0x73
INA226       0x40
SC16IS752    0x48, crystal 1.8432 MHz

SEN0574 ADC: GPIO0
E32 UART1: TX16, RX17, M0=18, M1=19, AUX=20
SC16IS752 A: MH-Z19B/C, 9600 8N1
SC16IS752 B: PMS7003T, 9600 8N1
```

Keputusan tiga kanal serial:

- E32 memakai UART1 langsung;
- MH-Z19 dan PMS7003T memakai dua channel SC16IS752;
- firmware tidak mengasumsikan tiga UART HP bebas pada ESP32-C6;
- bridge memakai level-register TX/RX, FIFO, dan timeout bounded, bukan polling tanpa batas.

Pin, alamat, crystal, dan rail tersebut adalah profile kode. Semuanya harus dicocokkan dengan PCB final. `GAS_SENSOR_5V_ENABLE_PIN` dan `PARTICLE_SENSOR_5V_ENABLE_PIN` masih `-1`, artinya rail 5 V diasumsikan selalu tersedia dari hardware eksternal. MH-Z19 membutuhkan supply 5 V dengan kemampuan peak yang memadai; PMS juga memakai rail 5 V. Jangan menyalakan profile lapangan sebelum power budget dan level logic diukur.

## Warm-up dan Partial Validity

- MH-Z19: `MHZ19_WARMUP_MS = 180000`; selama itu `co2` tetap `null` dan flag `co2_warmup` dikirim.
- PMS7003T: `PMS7003T_WARMUP_MS = 30000`; selama itu PM tetap `null` dan flag `pm_warmup` dikirim.
- SEN0574: `n2mv` valid setelah ADC calibration; `n2r` tetap `null` dengan flag `no2_ratio_unavailable` sampai baseline reference tersedia.
- BME688: lane invalid bila official SensorAPI tidak aktif atau data/heater status tidak valid.
- Sensor lain yang gagal tetap `null`; status payload menjadi `degraded/partial` bila setidaknya satu grup sensor masih valid.

## Payload Compact v2

```json
{
  "v": 2,
  "gw": "raspi_gateway_01",
  "n": "esp32c6_node_01",
  "r": "room_A",
  "ts": 42,
  "seq": 7,
  "bid": "boot-identity",
  "st": "ok",
  "q": "valid",
  "f": "",
  "ok": {
    "bme688": true,
    "sen0466": true,
    "sen0574": true,
    "sen0321": true,
    "mhz19": true,
    "pms7003t": true,
    "ina226": true
  },
  "s": {
    "tc": 28.0,
    "h": 62.0,
    "p": 1008.0,
    "bme": 18000.0,
    "co": 2.0,
    "n2mv": 420.0,
    "n2r": 1.0,
    "o3": 0.03,
    "co2": 650.0,
    "pm1": 8.0,
    "pm25": 12.0,
    "pm10": 18.0,
    "bv": 4.05,
    "bi": 82.0,
    "bp": 332.1
  }
}
```

`ts` adalah uptime seconds, bukan Unix epoch. Gateway menambahkan `receive_timestamp` dan `time_quality`. Missing selalu `null`, bukan nol.

## Batas Verifikasi

Sudah diverifikasi pada host:

- `mock` build SUCCESS;
- `hardware` build SUCCESS;
- bridge/MH-Z19/PMS source path compile;
- payload v2, parser, validator, partial validity, dan warm-up safeguards melalui regression test.

Belum diverifikasi:

- pembacaan BME688 nyata; profile `hardware-bme68x` dengan official Bosch source sudah compile SUCCESS;
- response nyata SEN0466/SEN0321/MH-Z19/PMS;
- baseline NO₂ dan input protection ADC;
- shunt/current-LSB INA226;
- SC16IS752 crystal/address/level logic;
- flash 16 MB fisik, E32 packet loss, 5 V rail, thermal, current profile, dan soak test.
