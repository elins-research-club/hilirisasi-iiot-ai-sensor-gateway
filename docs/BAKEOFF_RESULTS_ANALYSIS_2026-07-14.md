# Analisis Final Bake-off CUDA — 14 Juli 2026

> **Evidence level: EXPERIMENTAL / proxy lanes only.** Hasil ini bukan validasi sensor RAB nyata, bukan benchmark Raspberry Pi, dan tidak cukup untuk promotion produksi.

## Lingkup dan protokol

Host aktual: Windows laptop `GREY`, NVIDIA GeForce RTX 4050 Laptop GPU, Python 3.13.12, PyTorch 2.11.0+cu128.

Konfigurasi baseline:

```text
device=cuda
edge epochs=80
LSTM epochs=40
patience=10
batch=256
eval batch=1024
seeds=42,43,44
horizon=5 steps
purge gap=5
```

Aturan evaluasi:

- temporal split dan purge gap;
- baseline dipilih per target pada validation lalu dikunci untuk test;
- repeated-seed mean/std lebih utama daripada legacy single-run;
- legacy root metrics tetap terlihat untuk audit tetapi dikeluarkan dari aggregate bila `seed_*` tersedia;
- status `PROMISING` hanya berlaku pada lane proxy terkait;
- tidak ada klaim latency/RSS Raspberry Pi.

## Dataset/lane final

- **Gary:** cadence 60 detik, window 48, pressure sintetis; compatibility/regression proxy.
- **UCI:** cadence sekitar 1 jam, window 48; CO sumber tidak dipalsukan menjadi `co_ppm` proyek.
- **Fidas:** cadence 120 detik, window 12, horizon sekitar 10 menit; capped 50.000 window records dan 20.000 train samples untuk keselamatan RAM.
- **Sim v3:** cadence 60 detik, window **12**, full schema RAB sintetis bounded; bukan bukti hardware.

## Ranking repeated-seed final

Skill = `1 - RMSE_model / RMSE_validation-selected-baseline`; semakin tinggi semakin baik.

### Gary

- `lstm_residual`: mean **+0,13896**, std 0,05856; 3/3 promotion pass. Variance tinggi dan pressure sintetis dapat menginflasi hasil.
- `fits`: mean **+0,13135**, std 0,00142; 3/3 pass. Jauh lebih stabil.
- `fits_official`: mean +0,11314, std 0,00399; 3/3 pass.
- `dlinear`: mean +0,07259, std 0,00281; 3/3 pass.

Keputusan engineering: FITS tetap kandidat edge praktis; jangan memilih LSTM hanya dari mean Gary karena pressure bukan pengukuran nyata.

### UCI

- `lstm_residual`: mean **+0,37891**, std 0,00021; 3/3 pass.
- `fits`: mean **+0,37428**, std 0,00290; 3/3 pass.
- `dlinear`: mean +0,35442, std 0,00051; 3/3 pass.
- `fits_official`: mean +0,28886, std 0,00030; 3/3 pass.

Keputusan engineering: LSTM terbaik pada lane UCI, tetapi keunggulan atas FITS kecil dan UCI tetap proxy outdoor/meteo, bukan hardware RAB.

### Fidas

- `fits`: mean **+0,05722**, std 0,00122; **3/3 pass dan 3/3 target wins pada semua seed**.
- `fits_official`: mean +0,01303, std 0,00114; 3/3 pass.
- `lstm_residual`: mean +0,00618, std **0,05452**; hanya 2/3 pass; seed 44 −0,07091.
- `dlinear`: mean −0,04082, std 0,06281; hanya 1/3 pass.

Keputusan engineering: FITS adalah pemenang proxy Fidas yang jelas dan paling stabil. Single-run legacy sekitar +0,277 tidak boleh dibandingkan langsung karena dataset/evaluator lama berbeda.

### Simulator v3

- `fits`: mean **+0,36261**, std 0,00024; 3/3 pass dan 7/7 target wins.
- `dlinear`: mean +0,32072, std 0,00261; 3/3 pass.
- `lstm_residual`: mean +0,28685, std 0,00153; 3/3 pass.
- `fits_official`: mean +0,23416, std 0,00005; 3/3 pass.

Keputusan engineering: FITS unggul dan stabil pada simulator v3. Angka tinggi tidak boleh diterjemahkan menjadi akurasi lapangan karena simulator dibuat dari fungsi periodik/event/noise yang diketahui.

## Parameter refinement yang dijalankan

Tahap 1 train-only seed 42, dipilih memakai `best_val_loss` tanpa melihat test:

- FITS `frequency_bins`: 4 dan 6 vs default 7;
- FITS LR: `5e-4`, `1e-3` default, `2e-3`;
- `fits_official --individual` vs shared.

Hasil validation:

- bins 4/6 lebih buruk pada Fidas dan Sim;
- `fits_official --individual` lebih buruk;
- LR `5e-4` tidak membantu;
- LR `2e-3` menang validation 3/3 seed pada kedua lane, tetapi margin kecil: Fidas sekitar 0,80%, Sim sekitar 0,10%.

Setelah seleksi validation selesai, enam checkpoint LR `2e-3` dievaluasi satu kali pada test:

- **Fidas:** default skill mean +0,05722 → tuned +0,05393; variance tuned membesar. Tuning ditolak.
- **Sim:** default +0,36261 → tuned +0,36284; kenaikan hanya 0,00023 absolute skill dan tidak menang 3/3 seed. Tidak cukup untuk mengganti default.

**Keputusan final parameter:** pertahankan FITS `frequency_bins=7`, `learning_rate=1e-3`, batch 256, patience 10. Grid dihentikan untuk mencegah overfit proxy/test.

## Bug dan hardening yang ditemukan selama run

1. `enqueue_job.py` mengirim path Linux sebagai Windows `workdir`; diperbaiki agar empty workdir di-resolve agent ke repo Windows.
2. Agent failure cleanup dapat meninggalkan job di `jobs/running`; diperkeras dan diberi regression test.
3. Runner Sim salah menetapkan window 120 sementara metodologi/artifact terkunci di 12; dikoreksi dan diberi test.
4. Fingerprint model sebelumnya ikut berubah ketika script transport remote berubah; `scripts/remote/` dikeluarkan dari source fingerprint model.
5. Summarizer mencampur legacy root single-run dengan repeated seeds; legacy sekarang tetap ditampilkan tetapi tidak masuk aggregate bila `seed_*` tersedia.
6. Fidas preprocess menghasilkan window JSONL besar; capped dataset preparation dan chunked finite check berhasil mencegah OOM. Full campaign selesai exit 0.

## Verifikasi eksekusi

- CUDA smoke terisolasi: 2 lane × 4 model = **8/8 selesai**, tanpa menimpa artifact baseline.
- Full Fidas+Sim campaign: **55/55 orchestration steps completed**, exit code 0.
- Parameter refinement: 10 train stage-1 + 4 train confirmation + 6 eval selesai, semua exit 0.
- Data quality gate Fidas/Sim: PASS; tidak ada NaN/Inf pada dataset/model evaluation.
- Machine summary: `models/bakeoff/FULL_BAKEOFF_SUMMARY.json`.
- Campaign state: `models/bakeoff/state/CAMPAIGN_20260714.json`.

## Ranking praktis untuk tahap berikutnya

1. Rules + data-quality/abstain gate.
2. Validation-selected LastValue/SeasonalNaive/window-mean/drift baseline.
3. **FITS-inspired residual** sebagai default edge candidate paling konsisten lintas lane.
4. LSTM residual sebagai challenger khusus lane; bukan default edge.
5. DLinear sebagai neural sanity baseline.
6. `fits_official` sebagai research comparator.

**Production winner tetap belum ada.** Tahap bernilai berikutnya bukan menambah grid proxy, melainkan real raw capture dari node RAB, repeated evaluation real-device, lalu benchmark latency/RSS Raspberry Pi.

## Sumber metode utama

- FITS, ICLR 2024: https://proceedings.iclr.cc/paper_files/paper/2024/hash/701251e1db4a2e4dd2ef23f5265d5936-Abstract-Conference.html
- FITS repository: https://github.com/VEWOXIC/FITS
- DLinear, AAAI 2023: https://ojs.aaai.org/index.php/AAAI/article/view/26317/26089

Implementasi repo tetap disebut **FITS-inspired** dan **fits_official-style**, bukan reproduksi bit-for-bit paper.
