# ESP32-C6 Sensor Node Firmware

Firmware ini menghasilkan **hardware observation**, bukan nilai yang sudah diproses untuk model.

```text
SensorReader
→ protocol/checksum/warm-up/vendor compensation
→ HardwareIntegrityGate
→ HardwareObservation
→ compact_sensor.v3
→ Ebyte E32
```

Semantic moving average, resampling, normalization, feature engineering, model, dan final decision berjalan di Raspberry Pi gateway.

## Target RAB

| Perangkat | Interface | Field v3 | Status software |
|---|---|---|---|
| BME688 | I²C `0x77` | `tc,h,p,bme` | Bosch forced-mode adapter; compile-validated, hardware open |
| SEN0466 CO | I²C `0x74` | `co` ppm | checksum/type/scaling/range gate; hardware open |
| SEN0574 NO₂ | ADC GPIO0 | `n2mv,n2r` | ADC mV; `n2r` null sampai baseline; bukan ppm |
| SEN0321 O₃ | I²C `0x73` | `o3` ppm | automatic read; hardware open |
| MH-Z19B/C | SC16IS752 A | `co2` ppm | Winsen checksum/timeout/180 s warm-up; hardware open |
| PMS7003T | SC16IS752 B | `pm1,pm25,pm10` | passive frame/checksum/30 s warm-up; hardware open |
| INA226 | I²C `0x40` | `bv,bi,bp` | register/calibration path; shunt calibration open |
| Ebyte E32 | UART1 + AUX | transport | bounded AUX/timeout/error handling; RF open |

Tidak ada ToF, distance, VL53, atau SEN0377 pada firmware aktif.

## Boundary Hardware Integrity

Node tetap melakukan operasi yang wajib sebelum data dapat disebut observasi valid:

- protocol/frame/checksum validation;
- sensor not-ready/warm-up state;
- vendor compensation;
- ADC/register ke engineering unit;
- non-finite rejection;
- broad physical-impossibility gate;
- payload overflow/failure;
- boot ID, sequence, uptime/time basis;
- firmware, board config, dan calibration version.

Node **tidak** melakukan:

- semantic moving average/EMA/median;
- project threshold/quality scoring;
- interpolation/resampling;
- normalization/features/windows;
- anomaly/forecast/decision.

File historis `preprocessing.cpp` tetap dipakai build untuk meminimalkan churn, tetapi class aktif adalah `HardwareIntegrityGate`; tidak ada `MovingAverage`, `PreprocessedSample`, atau `Preprocessor` runtime.

## Build Profiles

```bash
cd firmware/esp32-c6-sensor-node
/home/ubuntu/.venvs/platformio/bin/pio run -e mock
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware-bme68x
```

- `mock`: CI/host, 8 MB profile;
- `hardware`: 16 MB target, BME688 disabled bila dependency tidak enabled;
- `hardware-bme68x`: fail-fast bila official Bosch `bme68x.h` tidak tersedia.

Build tidak membuktikan flash chip, pin, PCB, rail, level logic, sensor response, E32, calibration, atau power fisik.

## Bus/Pin Profile Kode

```text
I2C0: SDA GPIO6, SCL GPIO7, 100 kHz
BME688       0x77
SEN0466      0x74
SEN0321      0x73
INA226       0x40
SC16IS752    0x48, crystal assumption 1.8432 MHz

SEN0574 ADC: GPIO0
E32 UART1: TX16, RX17, M0=18, M1=19, AUX=20
SC16IS752 A: MH-Z19B/C, 9600 8N1
SC16IS752 B: PMS7003T, 9600 8N1
```

Semua pin/address/crystal/rail harus dicocokkan dengan PCB aktual. Rail-enable masih dapat bernilai `-1`, yang berarti selalu tersedia dari hardware eksternal.

## Warm-up dan Hardware State

- MH-Z19: 180 s; `co2=null`, state `warming`, flag `co2_warmup`;
- PMS7003T: 30 s; PM `null`, state `warming`, flag `pm_warmup`;
- SEN0574: `n2r=null` sampai baseline; flag `no2_ratio_unavailable`;
- checksum/protocol/range invalid: nilai grup menjadi `null`, hardware state error;
- `hs`: `ok`, `warming`, `partial`, atau `error`.

Current payload encoder membedakan `ok`, `warming`, dan generic `error`; detail timeout/checksum/protocol masih tersedia terutama melalui driver/log/flags dan perlu diperkaya setelah hardware bring-up bila backend membutuhkan reason code lebih granular.

## Payload v3

```json
{
  "v": 3,
  "gw": "raspi_gateway_01",
  "n": "esp32c6_node_01",
  "r": "room_A",
  "ts": 42,
  "tb": "uptime_s",
  "seq": 7,
  "bid": "boot-identity",
  "pp": "hardware_only",
  "fw": "sensor-fw-3.0.0-dev",
  "cfg": "esp32c6-board-profile-unverified",
  "cal": "factory-or-placeholder-unverified",
  "hs": "partial",
  "f": "co2_warmup",
  "ok": {
    "bme688": "ok",
    "sen0466": "ok",
    "sen0574": "ok",
    "sen0321": "ok",
    "mhz19": "warming",
    "pms7003t": "ok",
    "ina226": "ok"
  },
  "s": {
    "tc": 28.0,
    "h": 62.0,
    "p": 1008.0,
    "bme": 18000.0,
    "co": 2.0,
    "n2mv": 420.0,
    "n2r": null,
    "o3": 0.03,
    "co2": null,
    "pm1": 8.0,
    "pm25": 12.0,
    "pm10": 18.0,
    "bv": 4.05,
    "bi": 82.0,
    "bp": 332.1
  }
}
```

`ts` mengikuti `tb`; uptime tidak boleh dianggap epoch. Missing selalu `null`. Label `unverified` sengaja mencegah config/calibration placeholder dibaca sebagai hardware certification.

## Migration

Gateway membaca:

- v3 sebagai hardware observation dan menjalankan semantic preprocessing;
- v2 sebagai legacy node-preprocessed observation dan tidak memfilter ulang secara default;
- unknown version/profile ditolak.

Rollback firmware ke v2 tetap mungkin selama parser compatibility aktif. Jangan menghapus v2 parser pada hari cutover board terakhir.

## Hardware Acceptance yang Masih Open

- real payload v3 dari board;
- seluruh sensor state/warm-up/error;
- I²C scan/ADC protection/SC16IS752;
- E32 packet loss/range/recovery;
- payload size dan airtime;
- rail 5 V/current/thermal;
- INA226/shunt dan gas calibration;
- restart/recovery;
- 24–72 jam soak;
- reference instrument comparison.
