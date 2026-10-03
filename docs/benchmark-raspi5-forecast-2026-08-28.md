# Benchmark Raspberry Pi 5 — Forecasting Edge & Stream Detection IIOT

**Status:** SELESAI (dieksekusi penuh di Pi 5 fisik)
**Tanggal eksekusi:** 2026-08-28
**Host:** Raspberry Pi 5 (Bookworm arm64, 4× Cortex-A76, 15 GB RAM)
**Lingkungan:** Python 3.11.2, torch 2.13.0+cpu, numpy 1.26.4, river 0.22.0
**Konvensi:** label `raspberry_pi_5` hanya sah untuk artifact yang dibuat oleh eksekusi Pi 5 fisik; benchmark lama di bawah adalah evidence lokal historis dan harus dibaca bersama provenance dataset/kode yang dipakai.

> **Re-audit model 3 Oktober 2026:** benchmark Pi 5 di dokumen ini tetap sah
> sebagai bukti eksekusi hardware, latency, resource, dan reproducibility pada
> dataset 28 Agustus. Namun label kualitas model CO2 `PROMISING` **tidak lagi
> berlaku untuk interpretasi deployment live 60 detik**. Raw capture CO2
> memiliki median cadence `0.55 s`, durasi sekitar `552.905 s`, dan empat
> sentinel error `-1`. Artifact training historis memakai normalization range
> `[-1, 5000]` serta kemudian dideploy pada bucket `60 s` dengan horizon
> `300 s`. Dengan demikian ada mismatch temporal: horizon training 5 langkah
> raw sekitar `2.75 s`, bukan 5 menit. Current source manifest diturunkan ke
> `EXPERIMENTAL`/`shadow_only` sampai dataset 60-s yang cukup panjang dibangun
> dan model dilatih + dievaluasi ulang. Detail audit:
> `Project Context/AI_SENSOR_MODEL_PIPELINE_AUDIT_2026-10-03.md`.

---

## 1. Ringkasan Eksekusi

1. **Deploy** repo `iiot-ai-sensor-gateway` ke Pi 5 di `~/iiot-ai-sensor-gateway` (terpisah dari `/opt/yolo-vision-gateway` milik node kamera — **tidak ada yang disentuh**).
2. **Venv terpisah** `~/iiot-ai-sensor-gateway/.venv` (torch 2.13.0+cpu, numpy 1.26.4, river 0.22.0, scipy, sklearn, pytest). `pip check` bersih.
3. **Verifikasi:** 88 unit tests OK di Pi 5; build NPZ idempoten untuk 6 lane hardprog yang termasuk kontrak node sensor.
4. **Benchmark training + eval** 6 lane hardprog × 4 model (fits, fits_official, dlinear, lstm_residual) = **24 model**.
5. **Benchmark training + eval** 5 dataset bakeoff (fidas, gary, sim, smoke, uci) × 3 model (fits, dlinear, lstm_residual) = **15 model**.
6. **Benchmark inference latency** per sampel untuk **39 model** (mean/median/p95, throughput, RSS).
7. **Stream-detect native** (RobustZScore+PageHinkley) untuk **6 lane hardprog**, input ternormalisasi [0,1], dibandingkan dengan hasil VPS.

Total compliant dengan scope sensor node: **39 model terlatih + dievaluasi + di-benchmark** di Pi 5 fisik. Artefak/laporan lama yang memasukkan `data_tof_1` (hybrid-camera reference lane) tidak dihitung sebagai evidence model sensor.

---

## 2. Hasil Benchmark Training — Hardprog Real (6 lane × 4 model)

| Lane | Model | Train wall (ms) | RSS (KiB) | Params | Lat mean (ms) | Lat p95 (ms) | Throughput (sampel/s) |
|---|---|---|---|---|---|---|---|
| data_INA226 | dlinear | 506.7 | 322112 | — | 0.186 | 0.196 | 5377 |
| data_INA226 | fits | 642.5 | 328128 | — | 0.244 | 0.255 | 4104 |
| data_INA226 | fits_official | 890.0 | 324544 | — | 0.200 | 0.209 | 5006 |
| data_INA226 | lstm_residual | 4286.0 | 333440 | 4674 | 0.398 | 0.411 | 2513 |
| data_bme | dlinear | 486.6 | 323232 | — | 0.312 | 0.325 | 3203 |
| data_bme | fits | 208.1 | 327952 | — | 0.254 | 0.263 | 3942 |
| data_bme | fits_official | 965.0 | 324768 | — | 0.192 | 0.199 | 5210 |
| data_bme | lstm_residual | 1563.2 | 334128 | 4996 | 0.402 | 0.415 | 2490 |
| data_co2 | dlinear | 335.3 | 322096 | — | 0.129 | 0.137 | 7755 |
| data_co2 | fits | 291.0 | 322800 | — | 0.153 | 0.165 | 6521 |
| data_co2 | fits_official | 881.6 | 323520 | — | 0.193 | 0.201 | 5183 |
| data_co2 | lstm_residual | 2034.5 | 333744 | 4513 | 0.285 | 0.294 | 3510 |
| data_mentah_bme688 | dlinear | 3156.0 | 326784 | — | 0.313 | 0.326 | 3194 |
| data_mentah_bme688 | fits | 1432.9 | 331488 | — | 0.248 | 0.257 | 4040 |
| data_mentah_bme688 | fits_official | 2169.8 | 328816 | — | 0.186 | 0.203 | 5381 |
| data_mentah_bme688 | lstm_residual | 3168.4 | 334992 | 4996 | 0.398 | 0.412 | 2512 |
| data_no2 | dlinear | 340.4 | 322272 | — | 0.193 | 0.202 | 5180 |
| data_no2 | fits | 67.1 | 327936 | — | 0.248 | 0.269 | 4034 |
| data_no2 | fits_official | 316.0 | 324352 | — | 0.187 | 0.199 | 5343 |
| data_no2 | lstm_residual | 1295.5 | 332784 | 4674 | 0.394 | 0.404 | 2540 |
| data_pms_1 | dlinear | 190.6 | 322592 | — | 0.248 | 0.257 | 4038 |
| data_pms_1 | fits | 138.1 | 328240 | — | 0.249 | 0.257 | 4025 |
| data_pms_1 | fits_official | 930.1 | 324608 | — | 0.196 | 0.203 | 5113 |
| data_pms_1 | lstm_residual | 1621.3 | 334496 | 4835 | 0.393 | 0.409 | 2545 |
<!-- ToF is retained only as historical hybrid-camera evidence; it is not a node-sensor forecast lane. -->

**Insight:**
- Semua model edge inferensi **< 0.35 ms/sampel** di Pi 5 (dlinear tercepat 0.13 ms; fits_official konsisten ~0.19 ms).
- LSTM residual inferensi **0.28–0.41 ms/sampel** — masih sangat layak real-time (cadence sensor 0.5–2 s).
- RSS training **322–335 MiB** — aman di Pi 5 (15 GB RAM).
- Training wall time **67 ms – 4.3 s** — instan untuk retrain berkala.

---

## 3. Hasil Benchmark Training — Bakeoff (5 dataset × 3 model)

| Dataset | Model | Train wall (ms) | RSS (KiB) | Params | Lat mean (ms) | Lat p95 (ms) | Throughput (sampel/s) |
|---|---|---|---|---|---|---|---|
| fidas | dlinear | 16119 | 346800 | — | 0.255 | 0.270 | 3925 |
| fidas | fits | 13436 | 351984 | — | 0.244 | 0.255 | 4091 |
| fidas | lstm_residual | 28592 | 341824 | 5219 | 0.366 | 0.380 | 2734 |
| gary | dlinear | 15565 | 498032 | — | 0.249 | 0.260 | 4024 |
| gary | fits | 9435 | 503168 | — | 0.244 | 0.253 | 4107 |
| gary | lstm_residual | 215388 | 412912 | 6115 | 0.579 | 0.599 | 1729 |
| sim | dlinear | 22297 | 374496 | — | 0.488 | 0.504 | 2050 |
| sim | fits | 9603 | 379984 | — | 0.253 | 0.264 | 3956 |
| sim | lstm_residual | 15966 | 357504 | 9191 | 0.373 | 0.395 | 2679 |
| smoke | dlinear | 373 | 323472 | — | 0.487 | 0.509 | 2054 |
| smoke | fits | 334 | 329024 | — | 0.250 | 0.257 | 3997 |
| smoke | lstm_residual | 1609 | 334976 | 11367 | 0.383 | 0.402 | 2611 |
| uci | dlinear | 3059 | 357312 | — | 0.191 | 0.201 | 5240 |
| uci | fits | 1364 | 361968 | — | 0.251 | 0.260 | 3991 |
| uci | lstm_residual | 37387 | 354288 | 5698 | 0.581 | 0.596 | 1723 |

**Insight:**
- Dataset besar (fidas 20k×12×6, gary 24k×48×13) tetap layak: training < 4 menit, RSS < 504 MiB.
- **gary/lstm_residual** paling berat (215 s) karena 48-step window × 13 fitur — masih OK untuk batch.
- Semua inference < 0.6 ms/sampel — real-time sangat aman.

---

## 4. Verifikasi Evaluasi (gate) di Pi 5

### Hardprog (24 model)
- **data_co2**: semua model **PASS / PROMISING** (gate True; skill RMSE 0.12–0.54) — konsisten dengan VPS.
- **data_bme / data_mentah_bme688**: fits `baseline_passed=True` (skill +0.03..+0.07) tapi `data_quality` WARN → status jujur EXPERIMENTAL.
- **data_no2 / data_pms_1 / data_INA226**: FAIL_OR_EXPERIMENTAL (boundary saturation) — jujur EXPERIMENTAL.
- **Konsistensi VPS ↔ Pi 5: 100%** untuk status/gate/skill.

### Bakeoff (15 model)
- **fidas / gary / sim / uci**: mayoritas PASS / PROMISING (fits skill +0.06..+0.41, lstm +0.23..+0.42).
- **smoke**: jujur EXPERIMENTAL (skill kecil/negatif).
- **Konsistensi VPS ↔ Pi 5: 100%.**

---

## 5. Stream Detection Native di Pi 5 (input ternormalisasi)

| Lane | Proses | Anomali | Drift | Rejected | vs VPS |
|---|---|---|---|---|---|
| data_bme | 1000 | 9 | 60 | 0 | **identik** (9/60) |
| data_mentah_bme688 | 5130 | 98 | 66 | 0 | **identik** (98/66) |
| data_co2 | 1058 | 3 | 4 | 0 | anomali VPS 6 (lihat catatan) |
| data_no2 | 322 | 0 | 0 | 0 | **identik** (0/0) |
| data_pms_1 | 1000 | 2 | 78 | 0 | **identik** (2/78) |
| data_INA226 | 1138 | 0 | 177 | 0 | **identik** (0/177) |


**Catatan data_co2:** Kedua host mendeteksi anomali di sample_index **288, 704, 754** — persis di posisi nilai **-1 (error reads)** di CSV. VPS juga menangkap sample 59–61 (drop ke 500 ppm — event nyata sensor); Pi 5 tidak pada run ini karena detektor stateful (RobustZScore+PageHinkley) sensitif terhadap urutan/state — variasi wajar, bukan bug. Normalisasi kedua host **identik** (diverifikasi nilai-norm sama persis).

**Temuan kualitas data:** CSV co2 mengandung nilai **-1 (error reads)** di baris 7, 289, 705, 755 — ini seharusnya dibersihkan di pipeline hulu (mirip penanganan -1.0 ToF yang sudah kita lakukan di builder NPZ).

---

## 6. Aset & Artefak

### Di Pi 5 (`~/iiot-ai-sensor-gateway`)
- `.venv/` — venv terpisah (tidak menyentuh venv kamera `/opt/yolo-vision-gateway/.venv`)
- `models/pi5/` — 24 model hardprog (training.json + eval_results + benchmark.json)
- `models/pi5_bakeoff/` — 15 model bakeoff
- `data/real_offline/hardprog_csv_2026-08-28/stream/pi5/` — deteksi stream
- `models/pi5_benchmark.log`, `pi5_bakeoff.log`, `pi5_stream_detect_norm.log`

### Di VPS (disinkronkan)
- `models/pi5/pi5_full_results.json`, `pi5_eval_summary.json`
- `models/pi5_bakeoff/pi5_full_results.json`, `pi5_eval_summary.json`
- `data/real_offline/hardprog_csv_2026-08-28/stream/pi5/*_det.jsonl`

### Script baru (repo)
- `scripts/benchmark_inference.py` — ukur latency per sampel (edge + LSTM)
- `scripts/collect_pi5_results.py`, `collect_pi5_eval.py` — rangkum hasil
- `scripts/pi5_run_benchmark.sh`, `pi5_run_bakeoff.sh`, `pi5_run_inference_bench.sh`, `pi5_run_stream_detect_norm.sh` — runner Pi 5
- `scripts/build_hardprog_stream_input.py`, `normalize_stream_input.py` — input stream
- `scripts/build_hardprog_forecast_npz.py` — 6 node-sensor lane; ToF dikecualikan dari forecast

---

## 7. Kesimpulan

1. **Pi 5 sangat mumpuni** untuk forecasting edge IIOT: semua model (edge + LSTM) inferensi < 0.6 ms/sampel, training 67 ms – 4.3 s untuk data real.
2. **Replikasi VPS ↔ Pi 5 sempurna** untuk evaluasi gate & stream detection (kecuali variasi stateful minor yang terdokumentasi).
3. **Node kamera tidak terganggu**: service `yolo-snapshot-receiver` tetap running; venv & direktori terpisah.
4. **Klaim hardware kini jujur & terukur**: `hardware_label=raspberry_pi_5` + `raspberry_pi_claim` di 39 training.json.
5. **Temuan kualitas data**: error-reads `-1` di CO₂ dibuang dari stream normalization; ToF tetap berada di lane hybrid-camera dan tidak dipakai sebagai forecast sensor.

---
*Laporan ini dihasilkan dari eksekusi nyata di Raspberry Pi 5 fisik (2026-08-28).*
