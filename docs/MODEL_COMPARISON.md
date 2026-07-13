# Perbandingan Model AI Sensor

> **Analysis update 13 Juli 2026:** multi-seed CUDA evidence (Gary/UCI) + legacy single-run Fidas/Sim dianalisis di `BAKEOFF_RESULTS_ANALYSIS_2026-07-13.md`. LSTM gate diselaraskan ke majority-win; Fidas OOM diatasi dengan window/sample cap. Ranking production **tetap BELUM**.

> **Methodology update 11 Juli 2026:** ranking CUDA lama di dokumen ini adalah historical artifact evidence dan **tidak boleh lagi dipakai untuk promotion**. Dataset/evaluator sekarang menginfer cadence, menyimpan horizon duration, memilih active feature schema dari train-only, memblokir target constant/saturated, mencatat clipping, memilih baseline per target pada validation split, menolak duplicate/inapplicable SeasonalNaive, dan membutuhkan repeated seeds. Semua artifact lama harus di-bake-off ulang dengan `scripts/laptop_bakeoff_runner.py` sebelum ranking diperbarui.

Tanggal keputusan: 10 Juli 2026.
Re-audit + laptop CUDA multi-lane bake-off: **11 Juli 2026**.

Status jujur:

- **Production promotion: BELUM.** Data real RAB + Pi latency/RSS/false-alert/day masih open.
- Empat lane selesai: Gary, UCI, Fidas, dan simulator; semua training artifact mencatat `device: cuda`.
- Ranking FITS/LSTM/DLinear lama tetap berguna sebagai diagnosis, tetapi cadence/baseline/schema/degenerate-target gate lama belum cukup ketat.
- Simulator lama memiliki CO/CO₂/PM2.5 konstan pada test split; simulator v3 telah dibatasi rise/recovery, tetapi full CUDA bake-off baru belum dijalankan.
- Laporan lengkap: `LAPTOP_CUDA_BAKEOFF_RESULTS_2026-07-11.md`.
- Streaming native + River: wiring E2E OK, **EXPERIMENTAL** (bukan quality bake-off).

## Ranking Praktis Historical (sebelum methodology hardening)

1. **Rules + quality gate** — safety / abstain (wajib L0)
2. **LastValue / SeasonalNaive** — forecast baseline gate (wajib)
3. **FITS-inspired residual** — kandidat edge paling konsisten; menang edge-model di Gary dan Fidas
4. **DLinear residual** — neural baseline ringan; menang edge-model di UCI
5. **LSTM residual** — challenger nonlinear; terbaik di Gary/UCI, MIXED pada Fidas/simulator
6. **FITS official-style (`fits_official`)** — research comparator kecil; belum konsisten lintas target
7. **Native RobustZScore + PageHinkley** — streaming anomaly/drift tanpa dep ekstra
8. **River HST + ADWIN** — streaming challenger optional
9. Isolation Forest window — batch AD research
10. N-HiTS / boosting lags / USAD — belum atau cost lebih tinggi

Status PROMISING selalu **per lane**, bukan global dan bukan production.

## Optimasi yang diterapkan (kode)

File utama: `edge_forecasting.py`, `forecasting.py`, `cli.py`.

- **DLinear bugfix:** prediksi absolut diganti **residual di atas last value** + zero-init (smoke lama skill ≈ −118 karena 1 epoch + absolute head).
- **FITS:** zero-init residual head (start ≈ LastValue).
- **Training:** AdamW, SmoothL1, grad clip, ReduceLROnPlateau, weight decay.
- **LSTM:** default strategy **`residual`** (CLI + API), dropout 0.1, residual-friendly head init.
- Model version edge: `*_edge_v2`.

## Bake-off evidence (simulator)

Lokasi artifact (tidak di-commit): `/tmp/iiot-model-opt-bakeoff/`.

Dataset:

- 7200 payload compact v2 (`simulate mixed`, 3 node)
- 7167 windows → 7056 forecast samples
- split temporal **5004 / 1038 / 1014**, window 12, horizon 5, purge 5

Forecast test (normalized overall RMSE vs best baseline LastValue 0.003680):

- **FITS v2:** RMSE **0.002491**, skill **+0.323**, wins **6/7**, gate **PASS**, readiness **PROMISING**, params 693, best_epoch 3 / 15 ran
- **DLinear v2:** RMSE **0.002816**, skill **+0.235**, wins **6/7**, gate **PASS**, readiness **PROMISING**, params 182, best_epoch 79 / 80 ran
- **LSTM residual:** RMSE **0.003567**, skill **+0.031**, wins **2/7**, gate **FAIL**, readiness **EXPERIMENTAL**, status MIXED

Perbandingan smoke lama (240 payload, 1 epoch, before fix):

- FITS skill **−0.43** (gagal)
- DLinear skill **−118** (ambruk; bukan “DLinear jelek selamanya”)

Streaming (400 sample feature stream, EXPERIMENTAL):

- native: processed 400, rejected 0, anomalies 0, drift_events **3**
- River HST+ADWIN (isolated venv): processed 400, rejected 0, anomalies 0, drift_events 0

Host inference (bukan Pi claim): FITS ~0.0024 ms/sample, DLinear ~0.0015 ms/sample on current host.

## Baseline dan Data-Quality Gate Current

Baseline candidates: LastValue, window mean, drift, dan SeasonalNaive bila applicable. Kandidat identik tidak dihitung dua kali. Baseline dipilih per target pada validation split dan baru dievaluasi terkunci pada test.

Forecast candidate tidak dipromosikan bila:

- test model tidak mengalahkan validation-selected baseline;
- effective target coverage tidak cukup atau mayoritas effective target tidak menang;
- target train/test constant atau boundary-saturated;
- cadence/horizon metadata tidak valid;
- clipping/out-of-domain rate melampaui gate;
- checkpoint ordered feature schema/hash tidak cocok;
- hanya bagus pada synthetic/reference lane;
- split berpotensi leakage;
- performa lintas device/site buruk;
- resource target belum diukur.

Status:

- `EXPERIMENTAL`: baru smoke/limited evaluation;
- `PROMISING`: lulus baseline gate pada satu dataset yang layak (**sim bake-off FITS/DLinear = PROMISING sim only**);
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
- default train strategy **residual** (bukan absolute);
- dropout / AdamW / grad clip / LR schedule;
- status experimental bila kalah baseline atau kalah FITS/DLinear.

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

Pilihan terbaik saat ini **bukan** satu model tunggal. Stack yang paling aman setelah multi-lane bake-off:

```text
L0 rules + quality + abstain
L1 LastValue/SeasonalNaive + native streaming anomaly/drift
L2 FITS-inspired primary edge + DLinear neural baseline
L3 LSTM residual sebagai challenger nonlinear per lane
L4 FITS official-style + River sebagai research challengers
```

**Historical candidate signal:** FITS-inspired dan LSTM menunjukkan hasil menarik pada sebagian lane, tetapi belum boleh diranking ulang sebelum bake-off repeated-seed dengan evaluator baru selesai.

**Current engineering priority:** validitas dataset/evaluator dan real RAB capture lebih penting daripada menambah model baru.

**Bukan pemenang production:** seluruh hasil masih proxy/simulation. Promosi final menunggu bake-off ulang, data sensor RAB, repeated seeds, leave-device/site evaluation, real-label false-alert/day/delay, dan pengukuran Raspberry Pi. Artifact lama tidak dihapus, tetapi readiness-nya tetap `EXPERIMENTAL`.
