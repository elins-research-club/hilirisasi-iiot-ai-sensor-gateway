# Analisis Total Node Sensor + Data Bacaan Sensor Hardprog

- **Tanggal:** 2026-08-28
- **Topik:** IIoT — Sensor
- **Sumber data:** Google Drive tim hardprog — folder `DATASET` (8 file CSV, diunduh 2026-08-28)
- **Lokasi kerja:** `/home/ubuntu/projects/iiot-project/iiot-ai-sensor-gateway/data/real_offline/hardprog_csv_2026-08-28/`
- **Status:** ANALISIS & EKSPERIMEN (bukan deployment/produksi)

> **Catatan supersession hasil model — diverifikasi ulang 15 September 2026.** Profil dataset, provenance, dan temuan kualitas data pada laporan ini tetap berlaku. Namun tabel performa model di §5.2 merekam state eksperimen sebelum evaluator/gate dibangun ulang pada sesi lanjutan. Untuk angka performa forecasting terkini, sumber kanonik adalah `forecast/*/eval_results/metrics.json`, `forecast/edge_rebuild_summary.json`, dan paket Monev `Project Context/TKT7/evidence/final-ai-developer/TAB-AI-10_forecasting_model_bakeoff.csv`. Contoh perubahan penting: `data_co2/fits` pada artefak final memiliki RMSE skill **+0,54316** dan readiness `PROMISING`; jangan menggunakan angka skill lama di §5.2 sebagai hasil final.

---

## 1. Ringkasan Eksekutif

Dataset pertama **data bacaan sensor nyata** dari tim hardware-programmer berhasil diunduh, diprofilkan, diadaptasi ke kontrak canonical `compact_sensor.v3`, diproses melalui pipeline resmi, dan dipakai untuk **eksperimen forecasting (FITS / FITS-official / DLinear) + deteksi anomali streaming (RobustZScore+PageHinkley)**.

**Temuan terpenting:**

1. **Ini data nyata pertama proyek** — bukan simulasi/proxy. Nilai historisnya tinggi sebagai *provenance empiris* dan baseline kalibrasi.
2. **Data belum kalibrasi & masih mentah** — sesuai ekspektasi user; banyak artefak (CO₂ saturasi 5000 ppm, CO konstan 0, power konstan, timestamp uptime).
3. **Sesi capture per-sensor terpisah** — tiap file adalah boot/sesi berbeda dengan uptime mulai 0; **tidak bisa digabung jadi satu timeseries multi-sensor** tanpa kolokasi waktu nyata.
4. **Model eksperimental TIDAK mengalahkan baseline sederhana secara konsisten** — sesuai kebijakan proyek (EXPERIMENTAL, belum deploy). DLinear menang tipis di beberapa lane, FITS di lane lain; tidak ada pemenang konsisten.
5. **Deteksi anomali streaming berhasil menemukan sinyal menarik** — drift di INA226 & BME, anomali di CO₂/PMS/BME.

---

## 2. Keadaan Node Sensor Saat Ini (State Assessment)

### 2.1 Hardware & firmware

- **Board:** ESP32-C6 (firmware PlatformIO/C++, environment `mock` default, `hardware` terpisah).
- **Sensor RAB final:** BME688 (T/H/P/gas) · CO SEN0466 (I²C calibrated) · NO₂ SEN0574 (analog qualitative) · O₃ SEN0321 · MH-Z19B/C CO₂ · PMS7003T PM · INA226 daya.
- **Kontrak:** `compact_sensor.v3` (`pp=hardware_only`), parser/validator fail-closed, event_id deterministik, boot_id, sequence.
- **Catatan penting dari audit sebelumnya:**
  - Firmware hardware path **compile-validated tapi belum hardware-verified end-to-end**.
  - NO₂ **belum ppm** (kualitatif mV/ratio) — butuh kalibrasi referensi.
  - INA226 di firmware produksi masih placeholder; **kalibrasi empiris `y=1.2469x+0.0012` dari repo hardprog belum dimigrasi** (lihat `PLAN_GABUNGAN_HARDPROG_AND_V1V2_2026-08-27.md` A4).
  - ToF VL53 **bukan** node sensor — hybrid camera only.
  - Waktu node = uptime (bukan wall-clock); gateway wajib tambah receive-timestamp.

### 2.2 Software gateway

- Pipeline: raw → L1 hardware obs → L2 canonical → L3 resampled → L4 windows → model.
- 88 unit test host lulus (audit 22 Agu); receiver append-only; normalisasi train-only; baseline gate wajib.
- **Semua model forecasting = EXPERIMENTAL**; belum ada yang lolos deployment gate.

### 2.3 Gap yang tersisa

- Hardware baseline (Pi5, Zero2W, node sensor fisik) belum dijalankan ulang — perangkat offline.
- Kalibrasi fisik sensor belum tuntas (CO₂ offset, NO₂ referensi, INA226 migrasi).
- MQTT produksi/TLS/ACL belum certified.
- **Data real pertama ini adalah langkah pertama menutup gap "belum ada dataset lapangan RAB".**

---

## 3. Profil Dataset (8 file CSV, ~340 KB, 10.648 baris total)

| File | Kolom | Baris | Durasi | Cadence | Catatan kualitas |
|---|---|---|---|---|---|
| `data_bme.csv` | tc, humidity_rh, pressure_hpa, gas_ohm | 1.000 | 868 s | ~0,87 s | T 25,8–26,2 °C; RH 41,9–48,3%; gas 126–144 kΩ |
| `data_mentah_bme688.csv` | tc, humidity_%, pressure_hPa, gas_ohm | 5.130 | 3.385 s | ~0,65 s | T 26,5–27,6 °C; RH 46–56,5%; gas 63–138 kΩ (paling bervariasi) |
| `data_co.csv` | co_ppm, board_temp_c | 1.000 | 525 s | ~0,52 s | **CO konstan 0,00 ppm** (sinyal tidak bergerak) |
| `data_co2.csv` | co2_ppm | 1.058 | 553 s | ~0,55 s | **Saturasi 5000 ppm (20 titik), ada -1 (4 titik)**, mean 3.292 ppm |
| `data_no2.csv` | raw_adc, voltage_V | 322 | 644 s | ~2,0 s | ADC 2016–2897; voltage 1,62–2,33 V |
| `data_INA226.csv` | bus_voltage_v, current_mA, power_mW | 1.138 | 502 s | ~0,50 s | V ~4,99 V; **I ~1,24 mA; power konstan 6,25 mW**; 138 timestamp duplikat |
| `data_pms_1.csv` | pm1_0, pm2_5, pm10 | 1.000 | 870 s | ~0,90 s | PM1 20–25; PM2.5 32–41; PM10 34–48 µg/m³ |
| `data_tof_1.csv` | range_cm | 1.046 | 1.118 s | ~1,05 s | **Bukan node sensor** (hybrid camera); range 24–26 cm, ada -1 |

**Temuan kualitas data:**
- **Timestamp = uptime ms** (mulai dari nilai kecil, bukan epoch) — konsisten dgn kontrak node (Pi harus tambah receive-time). Kami sudah menambahkannya di adapter.
- **Missing value:** tidak ada missing ekstrem per kolom; beberapa baris `-1` (CO₂, ToF) = indikasi error/saturasi.
- **Konstanta:** CO = 0, power = 6,25 mW → fitur ini tidak informatif untuk model (dikecualikan dari training).
- **Duplikat timestamp:** INA226 138, CO₂ 2 → indikasi cadence tidak sempurna.
- **Dua delimiter** (`,` dan `;`) → adapter menangani otomatis.
- **Sesi terpisah antar-sensor** → tidak ada momen semua sensor membaca bersamaan; **tidak boleh digabung jadi satu vektor multi-sensor** untuk training.

---

## 4. Adaptasi ke Kontrak Canonical

- Script: `scripts/adapt_hardprog_csv.py` → 10.648 payload `compact_sensor.v3` ke `canonical/hardprog_canonical.jsonl`.
- Field v3 lengkap (`tb`, `pp`, `fw`, `cfg`, `cal`, `hs`) agar lolos parser resmi.
- Per-sensor payload hanya mengisi field sensor miliknya; sisanya `null` (missing) sesuai `shared-canonical-schema.md`.
- NO₂ disimpan sebagai referensi (`raw_adc`, `voltage_V`) — **tidak diklaim sebagai mV kalibrasi**.
- ToF disimpan di referensi lane (hybrid camera), tidak masuk model sensor.
- Pipeline resmi `run` sukses → `canonical_observations`, `hardware_observations`, `processed_timeseries`, `windows`.

---

## 5. Eksperimen Model Forecasting

### 5.1 Setup

- Builder: `scripts/build_hardprog_forecast_npz.py` → NPZ per lane (sliding window 16, horizon 5, split temporal train/val/test, normalisasi train-only, format `_load_dataset`).
- Model: `fits` (FITS-inspired), `fits_official` (official-style), `dlinear` — via `train-edge-forecast` (epochs 40, seed 42, CPU).
- Evaluasi: `evaluate-edge-forecast` → MAE/RMSE/MASE + baseline (last_value, window_mean, drift, seasonal_naive) + gate.

### 5.2 Hasil (test split, normalized; denorm = satuan asli)

| Lane | Model | MAE | RMSE | MASE | MAE denorm | Best baseline | Skill vs best BL | Baseline gate |
|---|---|---|---|---|---|---|---|---|
| data_bme | **fits** | 0.0484 | 0.0630 | 2.80 | 307.5 (gas ohm) | last_value | **+3.8%** | PASS |
| data_bme | fits_official | 0.0550 | 0.0710 | 3.36 | 325.9 | last_value | -9.2% | FAIL |
| data_bme | dlinear | 0.0538 | 0.0690 | 3.36 | 307.4 | last_value | -6.8% | FAIL |
| data_mentah_bme688 | **fits** | 0.0140 | 0.0215 | 2.68 | 81.6 (gas ohm) | last_value | **+3.8%** | PASS |
| data_mentah_bme688 | dlinear | 0.0141 | 0.0223 | 2.78 | 80.7 | last_value | +2.7% | FAIL |
| data_mentah_bme688 | fits_official | 0.0156 | 0.0239 | 3.25 | 76.0 | last_value | -7.0% | FAIL |
| data_co2 | **fits** | 0.0030 | 0.0045 | 5.58 | 14.8 ppm | drift | **-38%** (kalah) | PASS* |
| data_co2 | fits_official | 0.0051 | 0.0074 | 9.69 | 25.7 ppm | drift | -140% | PASS* |
| data_co2 | dlinear | 0.0066 | 0.0072 | 12.5 | 33.2 ppm | drift | -209% | PASS* |
| data_no2 | **dlinear** | 0.0117 | 0.0142 | 1.36 | 4.12 (adc) | last_value | **+11.7%** | PASS |
| data_no2 | fits | 0.0141 | 0.0172 | 1.64 | 5.05 | last_value | -6.4% | PASS |
| data_no2 | fits_official | 0.0148 | 0.0185 | 1.72 | 5.28 | last_value | -11.5% | PASS |
| data_pms_1 | **fits** | 0.1100 | 0.1560 | 2.75 | 0.85 µg/m³ | last_value | **+1.1%** | PASS |
| data_pms_1 | dlinear | 0.1108 | 0.1522 | 2.77 | 0.86 | last_value | +3.5% | PASS |
| data_pms_1 | fits_official | 0.1179 | 0.1586 | 2.94 | 0.92 | last_value | -11.1% | FAIL |
| data_INA226 | fits_official | 0.1631 | 0.2050 | 0.80 | 0.01 (mA) | window_mean | +0.4% | FAIL |
| data_INA226 | dlinear | 0.1654 | 0.2081 | 0.82 | 0.01 | window_mean | -1.0% | FAIL |
| data_INA226 | fits | 0.1676 | 0.2152 | 0.83 | 0.01 | window_mean | -2.4% | FAIL |

\* data_co2: `baseline_passed=True` tapi `data_quality_passed=False` → **gate keseluruhan tetap FAIL**.

**Interpretasi jujur:**
- **Tidak ada model yang konsisten mengalahkan baseline** di semua lane. FITS menang tipis di BME/PMS, DLinear di NO₂, tapi kalah telak di CO₂ (data saturasi + non-stasioner).
- **Data terlalu pendek & belum kalibrasi** → MASE tinggi (2,7–12,5) = prediksi jauh lebih buruk dari naive musiman.
- **Status `FAIL_OR_EXPERIMENTAL`** = sesuai kebijakan: model eksperimental TIDAK boleh deploy sampai mengalahkan baseline di data real yang layak.
- **Kesimpulan model:** gunakan hasil ini sebagai **baseline pengukuran pertama pada data nyata**, bukan kandidat produksi.

---

## 6. Deteksi Anomali Streaming (RobustZScore + PageHinkley)

Input ternormalisasi [0,1] per lane; warmup 32; threshold 0.7; z-scale 3.0.

| Lane | Diproses | Anomali | Drift | Catatan |
|---|---|---|---|---|
| data_mentah_bme688 | 5.130 | 98 | 66 | Sinyal paling dinamis; banyak drift gas/humidity |
| data_bme | 1.000 | 9 | 60 | Drift halus T/RH/gas |
| data_co2 | 1.058 | 6 | 4 | Anomali di sekitar saturasi 5000 & -1 |
| data_pms_1 | 1.000 | 2 | 78 | Drift bertahap PM (lingkungan berubah) |
| data_INA226 | 1.138 | 0 | 177 | **Drift sangat tinggi** — indikasi noise/quantisasi tegangan |
| data_no2 | 322 | 0 | 0 | Data pendek & relatif stabil |

**Interpretasi:** drift PageHinkley banyak mendeteksi perubahan level halus (INA226 tegangan ~4,99 V dengan noise kuantisasi, BME humidity). Ini sinyal bahwa data mentah mengandung **drift lingkungan nyata** — berguna untuk kalibrasi & thresholding, tapi anomali "keras" jarang (data lab relatif tenang).

---

## 7. Artefak yang Dihasilkan

```
data/real_offline/hardprog_csv_2026-08-28/
├── *.csv                          # 8 file CSV asli (immutable, tidak diubah)
├── canonical/hardprog_canonical.jsonl          # 10.648 payload v3
├── canonical/per_file/*.jsonl                   # per-sensor canonical
├── processed/                    # hasil pipeline run (gabungan)
├── processed_per_file/*/         # hasil pipeline per sensor
├── forecast/*_dataset.npz        # dataset training per lane
├── forecast/{lane}_{model}/      # model + training.json
├── forecast/{lane}_{model}/eval_results/metrics.json
├── forecast/comparison_table.json
├── stream/*_det.jsonl + .meta.json  # hasil deteksi anomali
└── plots/*_timeseries.png        # 6 visualisasi timeseries
scripts/adapt_hardprog_csv.py     # adapter CSV → canonical v3
scripts/build_hardprog_forecast_npz.py  # builder NPZ per lane
reports/hardprog_sensor_analysis_2026-08-28/
└── profile_summary.json          # profil statistik lengkap
```

---

## 8. Kesimpulan & Rekomendasi

### Kesimpulan
1. **Data nyata pertama berhasil diintegrasikan** ke pipeline resmi end-to-end (CSV → canonical v3 → windows → model → evaluasi → deteksi anomali). Ini milestone penting.
2. **Kualitas data mentah sesuai ekspektasi "belum kalibrasi"**: artefak saturasi/konstanta/uptime, tapi tetap berguna.
3. **Model belum siap produksi** — konsisten dengan kebijakan proyek. Baseline sederhana masih kompetitif.

### Rekomendasi (prioritas)
1. **Kalibrasi fisik sensor dulu** sebelum model: CO₂ offset (saturasi 5000), NO₂ referensi ppm, INA226 migrasi kalibrasi `y=1.2469x+0.0012` dari repo hardprog.
2. **Capture simultan multi-sensor** (semua sensor dalam satu boot/sesi, wall-clock) — ini syarat untuk training multi-target yang sah.
3. **Perpanjang durasi capture** (minimal 1–2 jam per skenario) dan variasikan kondisi (ventilasi, orang masuk, dsb.) agar ada sinyal non-stasioner nyata.
4. **Gunakan hasil ini sebagai baseline pengukuran**, bukan untuk klaim akurasi.
5. Lanjutkan ke **prospective epoch** (freeze schema → forward observation → evaluasi) setelah kalibrasi & capture lebih baik, sesuai roadmap Minggu 4 `IIOT_TOTAL_AUDIT_AND_ROADMAP_2026-08-22.md`.

---

## Update 2026-08-28 (sesi lanjutan): LSTM residual + gate kualitas + rencana Pi 5

### LSTM residual di semua 6 lane real
- Train + eval LSTM residual (`forecast-strategy=residual`, hidden 32, 1 layer, 40 epoch, seed 42) untuk **semua 6 lane** hardprog.
- Hasil test MAE (normalized): data_bme 0.0528, mentah_bme688 0.0152, co2 0.0082, no2 0.0130, pms 0.1071, INA226 0.1582.
- LSTM menang skill vs last_value di **data_no2 (+0.02)** dan **data_INA226 (+0.27)**; kalah tipis di bme/mentah/pms; kalah telak di CO₂ (−2.07, baseline drift menang karena sinyal hampir konstan).

### Perbaikan gate kualitas (temuan penting)
- Builder NPZ kita awalnya menulis `data_quality_json` tanpa `status`/`effective_target_names`, sehingga **semua edge model diblokir gate kualitas** (`quality_passed=false`) meski beberapa menang baseline.
- Diperbaiki: `scripts/build_hardprog_forecast_npz.py` kini memakai `array_quality_report()` kanonik + menulis metadata lengkap.
- Setelah rebuild: hanya **data_co2 yang PASS penuh** (semua model). Lane lain WARN/FAIL karena boundary saturation di test (data mentah belum kalibrasi) — jujur EXPERIMENTAL, bukan PROMISING.

### Bug `cadence_seconds` KeyError (source)
- `forecasting.py` line 756 & 947: fallback `cadence_seconds` mengevaluasi `data["resample_interval_sec"][0]` sebelum guard → KeyError saat NPZ tanpa key itu. Diperbaiki dengan fallback aman. Test suite 88 OK.

### Hook resource LSTM (parity dengan edge)
- Trainer LSTM kini mencatat `resource_measurement` (`training_wall_ms`, `process_max_rss_kib`, `param_count`) seperti trainer edge — siap untuk benchmark Pi 5.

### Pi 5 fisik online (Tailscale)
- Host benchmark Pi 5 internal (Bookworm arm64, Python 3.11.2, torch 2.13 CPU, 4 core, 15 GB RAM) **diakses SSH dari VPS**; alamat jaringan dan lokasi kredensial sengaja tidak disimpan di repo.
- **SELESAI DIEKSEKUSI 2026-08-28**: 39 model compliant scope sensor (24 hardprog + 15 bakeoff) dilatih/dievaluasi/di-benchmark di Pi 5 fisik; stream-detect native 6 lane sensor dijalankan. Hasil lama yang memasukkan ToF tidak dihitung sebagai evidence forecasting node sensor. `hardware_label=raspberry_pi_5` tetap hanya berlaku untuk artifact yang benar-benar dibuat di Pi 5. Hasil lengkap: `docs/benchmark-raspi5-forecast-2026-08-28.md`. Node kamera (`yolo-snapshot-receiver`) tidak terganggu (venv & direktori terpisah).

---

## GRAPH_GATE — Audit implementasi dan artefak (15 September 2026)

```
GRAPH_GATE: PASS
Graphify: graph_stats(8610 nodes/14212 edges/563 communities) + query kontrak compact_sensor.v3/uptime/receive_timestamp + query HardProg six-lane/purge-gap/ToF + query runner artifact-isolation + god_nodes/neighbors.
CRG: list_graph_stats(root)=6720 nodes/42169 edges/776 files; detect_changes/impact/affected_flows dipanggil untuk tree ini. Indeks CRG khusus subrepo kosong, sehingga risk 0.00 dan 0 flow bukan bukti blast radius nol.
Verified: source/tests/Git menjadi SoT; compileall, 97 unit tests, bash -n, git diff --check, 10.648 payload schema-v3, six-lane NPZ build, stream build, dan normalized-range check lulus. ToF tetap dikecualikan dari model sensor.
```
