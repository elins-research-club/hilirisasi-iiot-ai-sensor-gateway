# Perbandingan Model AI Sensor

Tanggal keputusan: 10 Juli 2026.

Semua kandidat tetap **EXPERIMENTAL** sampai diuji pada data real proyek, time/device/site split, baseline gate, dan Raspberry Pi target.

## Ranking Praktis

| Rank | Model | Peran | Status implementasi | Alasan |
|---|---|---|---|---|
| 1 | Rules + quality gate | keselamatan, node/battery health | tersedia | paling deterministik dan dapat abstain |
| 2 | LastValue / SeasonalNaive | forecast baseline | tersedia | wajib sebagai pembanding minimum |
| 3 | DLinear | lightweight neural baseline | train/eval/predict tersedia | sederhana dan kuat sebagai gate trend/seasonal |
| 4 | Native RobustZScore + PageHinkley | streaming anomaly + drift | E2E tersedia tanpa dependency tambahan | runnable di CI/edge, score-before-learn, tetap wajib warm-up dan evaluasi false alert |
| 5 | River Half-Space Trees + ADWIN | streaming anomaly + drift challenger | orchestration + smoke aktual tersedia; dependency tidak dipasang permanen | online dan incremental, tetapi benchmark data nyata belum dijalankan |
| 6 | FITS-inspired | forecast ringan berbasis frekuensi | train/eval/predict tersedia | kecil dan relevan untuk sinyal periodik, tetapi bukan otomatis terbaik |
| 7 | LSTM | forecast nonlinear existing | tersedia dan safe-load | lebih kompleks; wajib mengalahkan baseline |
| 8 | Isolation Forest window | anomaly batch/window | workflow baseline/research | mudah diuji, tetapi bukan streaming-native |
| 9 | N-HiTS / boosting lag features | kandidat server/laptop | belum diimplementasikan | layak benchmark setelah dataset real cukup |
| 10 | Autoencoder/USAD/transformer besar | anomaly/forecast lanjut | belum diimplementasikan | biaya, tuning, dan risiko overfit lebih tinggi |

## Baseline Gate

Forecast candidate tidak dipromosikan bila:

- overall test RMSE tidak mengalahkan baseline terbaik;
- mayoritas target tidak menang;
- hanya bagus pada synthetic/reference lane;
- split berpotensi leakage;
- performa lintas device/site buruk;
- resource target belum diukur.

Status:

- `EXPERIMENTAL`: baru smoke/limited evaluation;
- `PROMISING`: lulus baseline gate pada satu dataset yang layak;
- `VALIDATED`: lulus repeated real-data evaluation dan target-device benchmark;
- `PRODUCTION`: hanya setelah deployment acceptance/rollback/monitoring.

## Task Split

### Forecast

Target:

```text
temperature_c
humidity_pct
pressure_hpa
co_ppm
o3_ppm
co2_ppm
pm25_ug_m3
```

NO₂ tidak menjadi ppm forecast target. Ia dapat dipakai sebagai ordinal ratio/band setelah baseline sensor tersedia.

### Anomaly

Urutan:

1. quality/range/rules;
2. residual terhadap forecast baseline;
3. Isolation Forest/Half-Space Trees;
4. model kompleks hanya jika memberi gain nyata.

### Drift

Page-Hinkley native atau ADWIN River per feature digunakan sebagai marker distribusi berubah. Drift bukan otomatis kondisi lingkungan berbahaya; ia dapat berasal dari sensor aging, calibration shift, site change, atau concept change.

### Battery dan Node Health

Rules-first berdasarkan:

- voltage/current/power;
- stale/silent node;
- sensor error ratio;
- sequence gap/reboot;
- rail/warm-up state.

Model battery berbasis dataset eksternal belum dipromosikan karena chemistry dan load profile tidak sama dengan node proyek.

## FITS-inspired vs Paper FITS

Implementasi repo mengambil ide low-frequency representation dan linear residual head, tetapi tidak mengklaim reproduksi bit-for-bit. Karena itu namanya selalu `FITS-inspired`.

Ekspektasi:

- dapat efisien pada pola periodik;
- dapat kalah pada trend/non-periodik;
- DLinear dan naive baseline wajib dijalankan bersamaan.

## Native RobustZScore + PageHinkley

Backend native sudah berjalan end-to-end tanpa dependency tambahan. Ia memakai Welford statistics per feature, score-before-learn, warm-up, dan Page-Hinkley-style mean-shift detection. Ini challenger praktis, bukan algoritme HST yang diganti nama. Status tetap experimental sampai precision/recall/false-alert/day tersedia.

## River HST + ADWIN

Syarat:

- input finite dan normalized 0–1;
- score sebelum learn;
- warm-up;
- threshold configurable;
- event-level evaluation;
- false-alert/day dilaporkan;
- benchmark River pada data nyata harus dijalankan sebelum klaim performa.

## LSTM Existing

Perbaikan:

- target canonical RAB;
- safe checkpoint `weights_only=True`;
- strict state-dict load;
- baseline comparison;
- status experimental bila kalah.

## Decision Layer

```text
quality/source validity
-> rules
-> baseline/anomaly/drift
-> forecast candidate
-> env_status/main_factor/battery_status/node_health/confidence/abstain
```

Data invalid atau stale wajib abstain. Model tidak boleh mengubah missing/invalid menjadi normal.

## Metrics

Forecast:

- MAE;
- RMSE;
- MASE;
- skill score;
- per-target win;
- repeated seeds bila training stochastic.

Anomaly/drift:

- precision/recall/F1;
- event-level detection;
- false-alert/day;
- detection delay;
- warm-up false positives.

Resource:

- parameter count;
- latency;
- RSS;
- CPU;
- artifact size;
- power jika alat tersedia.

Label hardware wajib. Angka host tidak boleh ditulis sebagai angka Raspberry Pi.

## Kesimpulan

Pilihan terbaik saat ini bukan satu model tunggal. Stack yang paling aman:

```text
L0 rules + quality + abstain
L1 LastValue/SeasonalNaive/DLinear + streaming anomaly/drift
L2 FITS-inspired atau LSTM bila lulus baseline gate
```

Promosi final menunggu real sensor data dan pengukuran Raspberry Pi.
