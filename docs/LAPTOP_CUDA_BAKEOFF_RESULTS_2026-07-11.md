# Hasil Laptop CUDA Multi-Lane Bake-off — 11 Juli 2026

> **Status setelah methodology hardening:** seluruh angka di bawah adalah historical pre-hardening evidence. Bug cadence/horizon, baseline duplicate/test selection, target degeneracy, clipping visibility, sparse schema, repeated-seed orchestration, dan simulator saturation sudah ditangani di source/test. Namun full CUDA bake-off baru **belum dijalankan**, sehingga ranking lama tidak boleh dipromosikan atau dianggap current winner. Gunakan `scripts/laptop_bakeoff_runner.py` untuk rerun.

## Ringkasan

Bake-off selesai pada laptop NVIDIA GeForce RTX 4050. Seluruh training artifact mencatat `device: cuda`. Empat lane selesai:

- Gary: 34.425 sampel forecast; split 24.162/5.145/5.118.
- UCI Air Quality: 8.941 sampel; split 6.281/1.334/1.326.
- Zenodo Fidas PM: 224.762 sampel; split 157.355/33.708/33.699.
- Simulator mixed: 14.856 sampel; split 10.464/2.208/2.184.

Semua lane menggunakan window 12, horizon 5 langkah, purge gap 5, dan temporal split. **Horizon adalah langkah, bukan otomatis menit**: berdasarkan timestamp, UCI berinterval sekitar 1 jam (horizon sekitar 5 jam), Fidas berinterval sekitar 2 menit (horizon sekitar 10 menit), sedangkan metadata lama masih hardcoded `resample_interval_sec=60`. Nilai skill adalah `1 - RMSE_model/RMSE_baseline`; semakin besar semakin baik. RMSE normalized tidak boleh dibandingkan langsung antar-lane.

## Hasil per lane

### Gary — regression/compatibility, bukan RAB asli

Target: temperatur, kelembapan, tekanan. Tekanan Gary bersifat sintetik.

1. LSTM residual: skill **+0,169**, menang 3/3, PROMISING pada lane ini.
2. FITS-inspired: **+0,158**, menang 3/3, PROMISING.
3. DLinear residual: **+0,095**, menang 2/3, PROMISING.
4. FITS official-style: **+0,090**, menang 2/3, PROMISING; kalah buruk pada target tekanan meski overall gate lulus.

Interpretasi: LSTM unggul tipis atas FITS. Selisih kecil dan hanya satu seed, sehingga belum cukup untuk mengubah default edge.

### UCI Air Quality — proxy meteo

Target yang benar-benar dipakai: temperatur dan kelembapan. CO UCI dalam mg/m³ tidak dipetakan ke `co_ppm`.

1. LSTM residual: skill **+0,309**, menang 2/2, PROMISING.
2. DLinear residual: **+0,219**, menang 2/2, PROMISING.
3. FITS-inspired: **+0,175**, menang 2/2, PROMISING.
4. FITS official-style: **+0,143**, menang 2/2, PROMISING.

Interpretasi: LSTM menang jelas pada lane UCI, tetapi ini hanya T/H outdoor proxy dan bukan bukti model terbaik untuk full RAB.

### Fidas — PM reference proxy

Target: PM2.5, PM1, PM10. Ini reference-grade proxy, bukan PMS7003T chip-identical.

1. FITS-inspired: skill **+0,277**, menang 3/3, PROMISING.
2. FITS official-style: **+0,251**, menang 3/3, PROMISING.
3. LSTM residual: **+0,181**, menang 2/3, status **MIXED/EXPERIMENTAL** karena kalah pada PM1.
4. DLinear residual: **+0,176**, menang 3/3, PROMISING.

Interpretasi: FITS-inspired adalah pemenang Fidas dan lebih konsisten daripada LSTM untuk ketiga target PM.

### Simulator mixed — bukti wiring, bukan ranking full-RAB yang valid

Hasil tercatat:

1. FITS-inspired: skill **+0,256**, menang 4/7.
2. FITS official-style: **+0,194**, menang 3/7, MIXED.
3. DLinear residual: **+0,113**, menang 4/7.
4. LSTM residual: **+0,098**, menang 1/7, MIXED.

Namun test split simulator memiliki **CO, CO₂, dan PM2.5 konstan**. Penyebabnya adalah skenario `mixed` menaikkan polutan terus-menerus sampai melewati rentang normalisasi, lalu nilai test tersaturasi. LastValue mendapat RMSE nol pada tiga target itu. Karena itu:

- status PASS 4/7 untuk FITS/DLinear tidak boleh dibaca sebagai validasi tujuh target;
- lane sim hanya membuktikan pipeline/train/eval berjalan;
- generator simulator atau evaluasi target-degenerate perlu diperbaiki sebelum sim diulang.

## Ranking historical setelah multi-lane lama

### Default edge candidate

1. **FITS-inspired residual** — paling konsisten: menang edge-model pada Gary, Fidas, dan simulator yang non-degenerate; ringan dan tetap cocok sebagai primary edge candidate.
2. **DLinear residual** — baseline neural wajib; menang edge-model pada UCI dan murah.
3. **FITS official-style** — research comparator valid, sangat kecil, tetapi tidak konsisten pada Gary pressure dan gagal majority gate simulator.
4. **LSTM residual** — challenger nonlinear: terbaik pada Gary/UCI, tetapi MIXED pada Fidas/simulator dan lebih besar. Jangan jadikan default global hanya karena menang dua lane.

### Keputusan arsitektur

Tetap gunakan:

```text
L0 quality/rules/abstain
L1 LastValue/SeasonalNaive
L2 FITS-inspired primary + DLinear baseline
L3 LSTM residual lane-specific challenger
L4 FITS official-style research comparator
```

Tidak ada model yang dipromosikan ke production. Label PROMISING hanya berlaku pada lane terkait.

## Keterbatasan wajib

- Hanya satu seed per model/lane; belum ada confidence interval.
- Gary/UCI/Fidas adalah proxy, bukan data real node RAB.
- Temporal purge diterapkan dan tidak ditemukan overlap langsung, tetapi node/device yang sama berada di train/val/test; ini belum leave-device/site-out.
- `SeasonalNaive` belum independen: bake-off memakai `seasonal_period=0`, sehingga prediksinya identik dengan LastValue. Gate efektif baru melawan persistence baseline.
- Schema input dipaksakan 53 fitur walau lane hanya punya sedikit variabel: sekitar 40 fitur Gary, 43 UCI, dan 45 Fidas konstan. Missingness/sparsity dapat memengaruhi ranking.
- Fidas PM10 test mencapai batas normalisasi 1,0 sementara train jauh lebih rendah; clipping outlier dapat mengecilkan error aktual.
- Metadata interval/horizon untuk UCI dan Fidas tidak mengikuti cadence timestamp aktual.
- Belum ada benchmark Raspberry Pi: latency p95, RSS, CPU, dan power.
- Belum ada false-alert/day dan event-level anomaly metrics.
- Simulator memiliki target-degenerate pada test split.
- Dataset/model blobs tetap local dan tidak boleh di-commit.

## Artifact dan verifikasi

- Summary: `models/bakeoff/FULL_BAKEOFF_SUMMARY.json`.
- Metrics: `models/bakeoff/{gary,uci,fidas,sim}/{model}/metrics.json`.
- Dataset metadata: `data/bakeoff/{lane}/forecast-meta.json`.
- Semua model memiliki `model.pt`, `training.json`, dan `metrics.json`.
- Summary generator telah diperbaiki agar membaca schema LSTM dan edge dengan benar.

## Status Remediation 11 Juli 2026

Sudah source-implemented + regression-tested:

1. constant/near-constant dan boundary-saturation gate;
2. clipping report per feature/split;
3. cadence inference/declared validation + horizon duration;
4. SeasonalNaive applicability dan duplicate baseline exclusion;
5. baseline selection pada validation split lalu locked test;
6. simulator v3 bounded rise/recovery;
7. train-only active feature schema + SHA-256;
8. atomic resume-safe runner + exact download filenames/checksum;
9. per-seed output dan repeated-seed aggregate;
10. event-level anomaly benchmark harness.

Masih harus dijalankan/dibuktikan:

1. full CUDA rerun dengan 3+ seeds;
2. review blocked targets/clipping per lane;
3. leave-device/site-out bila metadata mendukung;
4. real RAB raw capture dan label/commissioning;
5. Raspberry Pi latency p50/p95, RSS, CPU, artifact, power;
6. MQTT/backend E2E dan field soak.
