# Hardware Conditioning ESP32-C6

> Dokumen lama bernama preprocessing, tetapi firmware aktif tidak lagi melakukan semantic preprocessing. Keputusan current-state: `docs/adr/ADR-001-gateway-centric-sensor-preprocessing.md`.

## Tujuan

ESP32-C6 menghasilkan observasi yang valid secara hardware/protokol dan terdokumentasi unitnya. Noise filtering, project validation, resampling, normalization, features, dan model berada di Raspberry Pi.

## Langkah Aktif

1. `SensorReader` membaca lane I²C/UART/ADC.
2. Driver memeriksa frame/header/length/checksum/status.
3. Warm-up/not-ready ditandai eksplisit.
4. Vendor compensation dan register/ADC conversion menghasilkan engineering units.
5. `HardwareIntegrityGate` menolak non-finite atau nilai yang mustahil secara hardware.
6. Per-sensor hardware state dan flags dipertahankan.
7. `HardwareObservation` membawa nilai langsung per sample tanpa moving average.
8. Encoder membentuk `compact_sensor.v3`; NaN internal menjadi JSON `null`.
9. Payload truncation/transport failure membatalkan transmit.

Tidak ada class runtime `MovingAverage`, `PreprocessedSample`, atau `Preprocessor`.

## Hardware-Impossibility Gate vs Project Range

Node memakai range lebar untuk mendeteksi corruption/konversi mustahil. Gateway memakai project ranges yang dapat berubah berdasarkan commissioning.

Contoh broad node gate:

| Field | Broad hardware gate |
|---|---|
| temperatur | -40–85 °C |
| kelembapan | 0–100 % |
| tekanan | 300–1250 hPa |
| BME gas | 1–1,000,000,000 ohm |
| CO | 0–10,000 ppm |
| NO₂ mV | 0–3300 mV |
| NO₂ ratio | 0–100 bila tersedia |
| O₃ | 0–100 ppm |
| CO₂ | 0–50,000 ppm |
| PM | 0–10,000 µg/m³ |
| voltage | 0–100 V |
| current | -100,000–100,000 mA |
| power | -10,000,000–10,000,000 mW |

Range ini bukan regulatory threshold, bukan normal operating range, dan bukan calibration evidence.

## Partial Hardware Validity

Satu grup sensor gagal tidak menghapus grup lain:

```text
BME688 ok
SEN0466 ok
SEN0321 protocol error
→ hs=partial
→ o3=null
→ ok.sen0321=error
→ flag sen0321_hardware_invalid
```

Gateway kemudian menentukan semantic quality/abstain.

## Warm-up

- MH-Z19: `co2=null`, state `warming`, flag `co2_warmup` selama warm-up;
- PMS7003T: PM null, state `warming`, flag `pm_warmup`;
- BME688: data/heater/gas-valid gate dari official SensorAPI;
- warm-up bukan pengukuran nol.

## NO₂

Firmware menghasilkan `n2mv`. `n2r` hanya tersedia setelah baseline commissioning. Tidak ada konversi NO₂ ppm tanpa reference calibration.

## INA226

Register/calibration path tersedia, tetapi shunt/current-LSB final dan reference-meter comparison masih hardware open. `cal` payload harus tetap berlabel unverified sampai commissioning selesai.

## Mock vs Hardware

Mock:

- menghasilkan v3 hardware observation;
- bounded deterministic variation;
- warm-up/error scenarios;
- dipakai untuk CI/contract/pipeline, bukan accuracy.

Hardware:

- driver paths compile-tested;
- BME profile fail-fast tanpa official dependency;
- seluruh pin/address/rail/bridge/radio/calibration masih perlu board evidence.

## Yang Dipindahkan ke Gateway

- project sanity range;
- EMA/median/moving-average compatibility;
- outlier/cross-sensor quality;
- event-time/order policy;
- resampling/missing policy;
- normalization/clipping report;
- features/windows/model/decision.

Default gateway filter `none`; pemilihan filter menunggu real sensor noise/reference study.
