# Modeling: Baseline, FITS-inspired, Native Streaming, River, dan Decision Layer

## Status

Semua model pada tahap ini masih **EXPERIMENTAL**. Tidak ada model yang dipromosikan hanya karena berhasil train atau inference.

## Task

- Forecast: temperatur, kelembapan, tekanan, CO, O₃, CO₂, dan PM2.5.
- Anomaly: residual forecast, native robust z-score, Isolation Forest/Half-Space Trees, dan rules.
- Drift: native Page-Hinkley atau ADWIN per feature.
- Battery/node health: rules terlebih dahulu.
- NO₂: ordinal/ratio band, bukan ppm.

## Baseline Gate

Setiap model forecast dibandingkan dengan:

- LastValue;
- SeasonalNaive;
- DLinear.

Gate edge forecast lulus hanya bila:

- test RMSE lebih rendah daripada baseline terbaik; dan
- menang pada minimal separuh target.

Status `PROMISING` bukan production-ready. Data real, site/device split, repeated run, dan runtime target tetap wajib.

## FITS-inspired Edge Forecaster

Implementasi: `src/iiot_ai_sensor_gateway/edge_forecasting.py`.

Model ini menggunakan komponen frekuensi rendah dari history target dan linear head kecil untuk memprediksi residual dari nilai terakhir. Implementasi proyek adalah **FITS-inspired**, bukan reproduksi bit-for-bit repository/paper.

Kelebihan:

- parameter sedikit;
- cocok diuji pada sinyal periodik;
- safe checkpoint `state_dict`;
- train/eval/predict CLI lengkap.

Risiko:

- tidak otomatis unggul pada trend/non-periodik;
- performance sangat bergantung horizon, sampling, dan seasonality;
- wajib dibandingkan LastValue/SeasonalNaive/DLinear.

Command:

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY run_gateway.py train-edge-forecast \
  --dataset data/modeling/lstm_forecast_dataset.npz \
  --output-dir models/edge_forecast/fits \
  --model-type fits

$PY run_gateway.py evaluate-edge-forecast \
  --dataset data/modeling/lstm_forecast_dataset.npz \
  --model models/edge_forecast/fits/model.pt \
  --seasonal-period 24

$PY run_gateway.py predict-edge-forecast \
  --dataset data/modeling/lstm_forecast_dataset.npz \
  --model models/edge_forecast/fits/model.pt \
  --output models/edge_forecast/fits/predictions.jsonl
```

## DLinear

DLinear dipakai sebagai lightweight neural baseline formal. Ia memisahkan trend dan seasonal component dengan moving average, lalu memakai linear head per target.

```bash
$PY run_gateway.py train-edge-forecast \
  --dataset data/modeling/lstm_forecast_dataset.npz \
  --output-dir models/edge_forecast/dlinear \
  --model-type dlinear
```

## LSTM Existing

LSTM multi-target tetap tersedia. Perubahan keselamatan:

- checkpoint load memakai `weights_only=True`;
- checkpoint wajib mapping dengan key lengkap;
- state dict dimuat strict;
- target lama berbasis proxy diganti field canonical RAB;
- output tetap experimental bila kalah baseline.

LSTM tidak boleh menjadi default production hanya karena model lebih kompleks.

## Native RobustZScore + PageHinkley

Implementasi dependency-free berada di `src/iiot_ai_sensor_gateway/streaming_detection.py`. Model menghitung skor sebelum mempelajari sampel saat ini, memakai statistik Welford per fitur, dan memetakan z-score terbesar ke rentang 0–1. Page-Hinkley-style detector dipakai per fitur untuk mean shift.

Model ini adalah challenger praktis, bukan pengganti berlabel palsu untuk River HST. Input tetap harus finite dan dinormalisasi 0–1. Warm-up wajib sebelum anomaly alert.

```bash
$PY run_gateway.py stream-detect \
  --backend native \
  --input data/modeling/normalized_features.jsonl \
  --output data/modeling/native_streaming_detection.jsonl \
  --feature-names temperature_c,humidity_pct,co2_ppm,pm25_ug_m3
```

Native backend telah dijalankan end-to-end pada smoke dataset host. Status tetap `EXPERIMENTAL`; false-alert/day dan detection delay belum tersedia tanpa label event real.

## River Half-Space Trees + ADWIN

Implementasi: `src/iiot_ai_sensor_gateway/streaming_detection.py`.

Aturan:

- dependency opsional, tidak dipasang otomatis;
- feature wajib finite dan sudah dinormalisasi 0–1;
- score dilakukan sebelum learn;
- warm-up wajib sebelum alert;
- ADWIN per feature mengeluarkan drift marker;
- invalid record ditolak dan dicatat.

Dependency command:

```bash
python -m pip install -e '.[streaming]'
```

Run:

```bash
$PY run_gateway.py stream-detect \
  --backend river \
  --input data/modeling/normalized_features.jsonl \
  --output data/modeling/streaming_detection.jsonl \
  --feature-names temperature_c,humidity_pct,co2_ppm,pm25_ug_m3
```

River tidak dipasang permanen pada environment project. Smoke aktual dijalankan di venv temporer terisolasi: 20 record diproses, 0 ditolak, backend `river.HalfSpaceTrees+ADWIN`. Ini hanya membuktikan wiring runtime; warm-up, normalization gate, dependency error, dan orchestration juga tetap diuji.

## Decision Layer

Implementasi: `src/iiot_ai_sensor_gateway/decision.py`.

Urutan:

```text
L0 quality/source validity
-> L0 commissioning rules
-> L1 anomaly/drift/baseline
-> L2 forecast candidate
-> status + factor + confidence + abstain
```

Output:

```text
schema_version
env_status
main_factor
battery_status
node_health
confidence
abstain
reason
forecast_status
model_readiness
no2_semantics
```

Data invalid/stale/offline menghasilkan:

```text
env_status=unknown
abstain=true
```

Threshold default adalah commissioning defaults proyek, bukan batas regulasi. Produksi harus memindahkan threshold ke config deployment dan melalui review domain.

## Safe Checkpoint

LSTM dan edge model hanya memuat checkpoint dengan `torch.load(..., weights_only=True)` dan memvalidasi shape/key sebelum strict load. File checkpoint yang tidak sesuai ditolak.

## Metrics

Forecast:

- MAE;
- RMSE;
- MASE;
- per-target metrics;
- skill score terhadap baseline.

Anomaly:

- precision/recall/F1;
- event-level matching;
- false-alert/day;
- warm-up behavior.

Drift:

- detection delay;
- false drift;
- recovery/adaptation period.

Resource hooks:

- parameter count;
- wall time;
- max RSS host;
- mean inference time per sample.

Hook hanya diberi label hardware tempat ukur. Implementasi menyimpan `raspberry_pi_claim: null` sampai benchmark benar-benar dijalankan pada Pi.

## Status Verifikasi

Sudah:

- FITS-inspired train/eval/predict smoke test;
- DLinear train/eval/predict smoke test;
- safe checkpoint tests;
- baseline gate;
- native RobustZScore + PageHinkley end-to-end;
- River orchestration tests + smoke dependency nyata di venv temporer;
- decision abstain dan NO₂ ordinal semantics.

Belum:

- long training;
- benchmark dataset publik penuh;
- real field dataset;
- benchmark River HST+ADWIN pada data real;
- false-alert/day;
- leave-site/device-out comparison;
- Raspberry Pi latency/RSS/power;
- model promotion.
