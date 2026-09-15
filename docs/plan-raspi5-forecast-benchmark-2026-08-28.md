# Rencana Benchmark & Simulasi Raspberry Pi 5 — Forecasting Edge IIOT

**Status:** SELESAI DIEKSEKUSI 2026-08-28 — rencana historis dan acceptance record; lihat `docs/benchmark-raspi5-forecast-2026-08-28.md` untuk hasil yang sudah dikoreksi.
**Tanggal:** 2026-08-28
**Konvensi:** Bahasa Indonesia untuk `docs/*.md`; klaim hardware Pi 5 TIDAK boleh diisi sampai benchmark dijalankan di Pi 5 fisik.

---

## 1. Konteks & Temuan Terkini (2026-08-28)

### 1.1 Data real offline hardprog (capture 2026-08-28)

6 lane per-sensor CSV node-sensor (~300–5100 titik, cadence 0.5–2 s, window 16, horizon 5) dibangun menjadi NPZ kanonik (`scripts/build_hardprog_forecast_npz.py`) dan dievaluasi. ToF tetap hybrid-camera reference lane dan tidak masuk forecast sensor:

| Lane | fitur/target | n | train/val/test | data_quality | Model terbaik (test MAE norm) |
|---|---|---|---|---|---|
| data_bme | T/H/P/gas | 1000 | 686/147/147 | WARN | fits 0.04844 |
| data_mentah_bme688 | T/H/P/gas | 5130 | 3577/766/767 | WARN | fits 0.01399 |
| data_co2 | co2_ppm | 1058 | 726/155/157 | **PASS** | fits 0.00296 |
| data_no2 | raw_adc, voltage_V | 322 | 211/45/46 | FAIL | dlinear 0.01169 |
| data_pms_1 | pm1/pm2_5/pm10 | 1000 | 686/147/147 | FAIL | lstm 0.10710 |
| data_INA226 | bus_voltage, current | 1138 | 782/167/169 | WARN | lstm 0.15818 |

### 1.2 Status gate yang jujur

- Hanya **data_co2** yang lolos penuh gate (`baseline_passed AND quality_passed`) untuk ketiga edge model.
- Lane lain diblokir oleh `data_quality.status != PASS` (boundary saturation di split test, target near-constant). Ini **jujur EXPERIMENTAL**, bukan PROMISING.
- **LSTM residual** (comparator non-linear, bukan production default): menang skill vs last_value di data_no2 (+0.02) dan data_INA226 (+0.27), tapi tidak lolos quality gate (kecuali data_co2 yang malah kalah telak vs drift −2.07).
- Sesuai keputusan proyek: **LSTM = comparator saja**, FITS-inspired `fits` tetap kandidat default edge.

### 1.3 Bug yang ditemukan & diperbaiki

1. **`forecasting.py` line 756** — `cadence_seconds` fallback mengevaluasi `data["resample_interval_sec"][0]` sebelum guard → `KeyError` saat key tidak ada. Diperbaiki (fallback aman). (baris 947 juga)
2. **`scripts/build_hardprog_forecast_npz.py`** — `data_quality_json` ditulis sebagai `{"missing_handling": ...}` tanpa `status`/`effective_target_names` sehingga evaluator selalu `quality_passed=False`. Diperbaiki dengan memakai `array_quality_report()` kanonik + menulis metadata lengkap (`horizon_steps`, `resample_interval_sec`, `cadence_seconds`, `window_size`, `purge_gap_steps`).
3. **`forecasting.py` trainer LSTM** — tidak mencatat resource (wall time, RSS, param count) seperti edge trainer. Ditambahkan `resource_measurement` + `param_count` (parity dengan edge).

### 1.4 Status Pi 5 fisik

- `iiot-pi5` menjalankan benchmark forecasting pada 2026-08-28 dengan venv terpisah dari lane kamera.
- Evidence yang boleh dihitung untuk sensor node: 6 lane × 4 model = 24 model hardprog, ditambah 15 model bakeoff proxy = 39 model; ToF tidak dihitung sebagai forecast sensor.
- Host label `raspberry_pi_5` hanya sah pada artifact yang benar-benar dibuat di Pi 5. VPS/ARM64 tidak boleh dipakai sebagai pengganti evidence Pi 5.

---

## 2. Tujuan

1. Mendapatkan **ukuran resource sungguhan di Pi 5** untuk model edge (`fits`, `fits_official`, `dlinear`) dan comparator LSTM residual:
   - `training_wall_ms`, `process_max_rss_kib`, `param_count` (hook sudah ada di kedua trainer).
   - **Inference latency per sampel** (tidak ada hook otomatis — perlu skrip benchmark kecil).
2. Mengisi `resource_measurement.hardware_label = "raspberry_pi_5"` + `raspberry_pi_claim` hanya **setelah** pengukuran nyata di Pi 5 (aturan proyek: jangan klaim tanpa bukti).
3. Menentukan model edge yang layak jalan di Pi 5 dengan data real (batas: RSS, latency, wall time training).

## 3. Prasyarat / Blocker

- [x] Repo gateway dan data capture tersedia di Pi 5 pada execution record.
- [x] Venv terpisah dan dependency lane diverifikasi pada execution record.
- [x] Data CSV hardprog dibangun menjadi dataset/stream artifact lokal.

## 4. Langkah Eksekusi (historis; telah dijalankan)

### Fase A — Deployment kode ke Pi 5

```bash
# Di VPS; ganti `<pi5-host>` dan `<pi5-user>` dengan target yang sudah
# diverifikasi di lingkungan eksekusi, tanpa menaruh alamat jaringan/kunci di repo.
cd /home/ubuntu/projects/iiot-project/iiot-ai-sensor-gateway
git clone https://github.com/greyghstt/IIOT-AI-Sensor-Gateway.git /tmp/gateway  # atau rsync
rsync -az --exclude data/ --exclude models/ ./ <pi5-user>@<pi5-host>:~/iiot-ai-sensor-gateway/
```

### Fase B — Install deps di Pi 5

```bash
ssh <pi5-user>@<pi5-host>
cd ~/iiot-ai-sensor-gateway
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt  # sesuaikan isi repo
# verifikasi
.venv/bin/python -c "import torch, numpy; print(torch.__version__, numpy.__version__)"
```

### Fase C — Benchmark training + eval di Pi 5

Untuk setiap lane (6 lane) dan model (`fits`, `fits_official`, `dlinear`, `lstm_residual`):

```bash
.venv/bin/python run_gateway.py train-edge-forecast --dataset data/.../{lane}_dataset.npz \
  --output-dir models/pi5/{lane}/{model} --model-type {model} --epochs 40 --seed 42 --device cpu
.venv/bin/python run_gateway.py evaluate-edge-forecast --dataset ... --model models/pi5/{lane}/{model}/model.pt --output-dir models/pi5/{lane}/{model}/eval
```

Setelah itu `training.json` berisi `resource_measurement` (wall_ms + RSS + param_count) dengan `hardware_label: current_host`; ganti menjadi `raspberry_pi_5` + isi claim.

### Fase D — Benchmark inference latency per sampel

Tidak ada hook otomatis → skrip kecil (`scripts/benchmark_inference.py`) yang:
- load model (edge + LSTM) dari checkpoint,
- jalankan N iterasi (mis. 200) pada satu window (16×fitur) dengan `torch.no_grad()`,
- ukur `mean/median/p95 inference ms per sampel`,
- ukur RSS via `resource.getrusage`,
- tulis `benchmark.json` per model.

### Fase E — Rekap & laporan

- Tabel per lane/model: train wall ms, RSS, param count, inference ms, test MAE/RMSE.
- Tentukan `raspberry_pi_claim` + `hardware_label` yang jujur.
- Update `docs/` (Bahasa Indonesia) + `Project Context/AI_SENSOR.md` bila relevan.

## 5. Kriteria Penerimaan

- [x] `resource_measurement.hardware_label == "raspberry_pi_5"` dan `raspberry_pi_claim` terisi pada artifact Pi 5 yang dilaporkan.
- [x] Semua 4 model (3 edge + LSTM) tereksekusi untuk 6 lane hardprog sensor pada execution record.
- [x] `benchmark.json` inference latency per sampel tersedia (mean/p95).
- [x] Klaim latency/RSS dibatasi pada artifact Pi 5; VPS hanya proxy/non-Pi evidence.
- [x] Test suite host gateway hijau pada verifikasi ini (97 test).

## 6. Risiko & Catatan

- **Host VPS aarch64 (Neoverse-N1, 4 core, 23 GB)** bukan Pi 5; jangan jadikan hasil VPS sebagai klaim Pi 5. Hanya proxy arsitektur ARM64.
- `torch 2.13.0+cu130` di Pi 5 adalah build CPU (CUDA tak ada) — pastikan `device=cpu` eksplisit.
- Dataset real masih pendek & satu node; hasil hanya EXPERIMENTAL, bukan produksi.
- Data CO₂ hampir konstan → baseline drift menang; gate PASS tapi model tidak berguna secara praktis — catat sebagai "model tidak menambah nilai" (jangan promo).
- Jika SSH Pi 5 berubah (key/user), verifikasi dulu `ssh <pi5-user>@<pi5-host>` sebelum eksekusi.
