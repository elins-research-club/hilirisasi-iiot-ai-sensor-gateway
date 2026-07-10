# Dataset Catalog dan Adapter

## Prinsip

Dataset publik dipakai untuk menguji pipeline, baseline, dan metodologi. Dataset publik tidak otomatis mewakili chip, kalibrasi, lokasi, atau pola daya node proyek.

Setiap lane wajib memiliki:

- dataset ID dan provenance;
- unit asli;
- license;
- quality/missing marker;
- adapter sendiri;
- field canonical yang benar-benar tersedia;
- split time-ordered dan leave-site/device-out bila memungkinkan.

Missing tetap `null`. Adapter tidak boleh membuat O₃, CO₂, PM, pressure, atau power yang tidak ada.

## Catalog

Catalog machine-readable: `datasets/catalog.json`.

### UCI Air Quality

Kegunaan:

- regression/reference gas sensor;
- temperatur dan kelembapan;
- drift/cross-sensitivity experiment.

Mapping:

- `T` → `temperature_c`;
- `RH` → `humidity_pct`;
- `CO(GT)` tetap `reference.co_mg_m3`;
- `NO2(GT)` tetap `reference.no2_ug_m3`;
- `NOx(GT)` tetap `reference.nox_ppb`;
- missing `-200` → `null`.

CO tidak dikonversi diam-diam ke ppm karena konversi membutuhkan asumsi temperatur/tekanan/molar yang harus eksplisit. Respons sensor yang secara nominal menargetkan O₃ juga tidak dianggap O₃ ground truth.

Adapter:

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY run_gateway.py adapt-uci-air-quality \
  --input-csv data/external/AirQualityUCI.csv \
  --output data/canonical/uci_air_quality.jsonl
```

### Bristol BME680 Smart Building

Kegunaan:

- variasi indoor multi-device;
- temperatur, kelembapan, dan tekanan;
- leave-device-out validation;
- drift/perangkat heterogen.

Mapping:

- `Temperature` → `temperature_c`;
- `Humidity` → `humidity_pct`;
- `Pressure` → `pressure_hpa`;
- `Gas` tetap `reference.bme680_iaq_index`.

Nilai Gas sumber adalah IAQ index, bukan resistance dalam ohm dan bukan CO₂. BME680 juga bukan chip-identical terhadap BME688 proyek.

Adapter:

```bash
$PY run_gateway.py adapt-bristol-bme680 \
  --input-csv data/external/bristol/device.csv \
  --output data/canonical/bristol_bme680.jsonl
```

Dataset lengkap besar dan tidak diunduh otomatis.

### Zenodo 7198378 Fidas 200S PM Reference

Kegunaan:

- reference-grade PM1/PM2.5/PM10 lane;
- metodologi calibration/co-location;
- evaluation dengan paired low-cost data bila nanti tersedia.

Adapter sengaja menyimpan nilai pada `reference.*`. Field project PMS7003T pada `sensor.*` tetap `null`; Fidas tidak dipalsukan sebagai output chip proyek.

```bash
$PY run_gateway.py adapt-zenodo-pm-reference \
  --input-csv data/external/df_pm_2min.csv \
  --output data/canonical/zenodo_fidas_pm_reference.jsonl
```

### SensEURCity 17858205

Kegunaan potensial: multi-city/leave-site-out air-quality research. Archive besar dan low-cost systems-nya bukan chip-identical terhadap RAB. Entry tetap `manual_only` sampai schema sampling, storage, dan provenance per city/device disetujui.

### Gary Stafford

Status: regression/compatibility only.

Gary berguna untuk menguji parser/preprocessing lama, tetapi:

- gas source bukan sensor RAB;
- pressure turunan adalah sintetis;
- tidak boleh menjadi main training lane atau klaim performa hardware;
- output lama harus dilabeli sebagai historical/migration.

### Project Real Capture

Ini lane utama untuk validasi deployment:

```text
raw serial/radio envelope
-> append-only capture
-> canonical adapter
-> quality review
-> time/device/site split
-> baseline/model evaluation
```

Hanya data ini yang dapat membuktikan kalibrasi dan perilaku sensor/board aktual.

### Power Proxy

Belum ada dataset publik yang dipromosikan sebagai pengganti langsung INA226/node battery traces. Chemistry, regulator, beban, duty cycle, dan sample rate berbeda. Battery/node health tetap rules-first sampai capture proyek tersedia.

## Downloader Aman

Script: `scripts/download_dataset.py`.

Fitur:

- hanya entry catalog berstatus `script_allowed`;
- HTTPS only;
- maximum bytes;
- catalog-pinned SHA-256 untuk artifact yang sudah diverifikasi, dengan optional CLI override;
- temporary file + atomic rename;
- sidecar provenance;
- tidak mengekstrak archive otomatis.

Lihat daftar atau metadata tanpa download:

```bash
$PY scripts/download_dataset.py --list
$PY scripts/download_dataset.py uci_air_quality_360 --describe-only
```

Download:

```bash
$PY scripts/download_dataset.py uci_air_quality_360 \
  --output-dir data/external/downloads \
  --max-bytes 52428800
```

Checksum dapat dikunci setelah artifact resmi pertama diverifikasi:

```bash
$PY scripts/download_dataset.py uci_air_quality_360 \
  --output-dir data/external/downloads \
  --sha256 <EXPECTED_SHA256>
```

## Canonical Dataset Record

Adapter baru menghasilkan:

```json
{
  "schema_version": "iiot.dataset_record.v1",
  "dataset_id": "...",
  "lane": "...",
  "record_id": "...",
  "timestamp": "...",
  "device_id": "...",
  "site_id": "...",
  "sensor": {"temperature_c": 22.5},
  "reference": {},
  "quality": {"valid": true, "notes": []},
  "provenance": {"source_file": "...", "source_row": 2}
}
```

Field sensor canonical yang tidak tersedia tetap `null`.

## Split dan Evaluasi

Urutan default:

1. sort timestamp;
2. train paling awal;
3. validation setelah train;
4. test paling akhir;
5. leave-device/site-out bila metadata mendukung;
6. normalizer fit hanya pada train;
7. report missing per field dan coverage per lane.

Jangan random split pada deret waktu yang berpotensi leakage.

## Status Verifikasi

Sudah:

- catalog tervalidasi oleh unit test;
- adapter UCI, Bristol, dan Zenodo memiliki fixture test;
- missing/unit semantics diuji;
- downloader `--list`, describe-only, size/checksum gate, dan atomic path tersedia;
- UCI Air Quality resmi diunduh ke `/tmp`, SHA-256 dikunci, dan 9.357 row diadaptasi tanpa reject;
- Zenodo 7198378 resmi diunduh ke `/tmp`, SHA-256 dikunci, dan 224.810 row diadaptasi tanpa reject.

Belum:

- Bristol/SensEURCity full archive diuji karena manual-only/besar;
- real project capture tersedia;
- benchmark leave-site/device-out dijalankan.
