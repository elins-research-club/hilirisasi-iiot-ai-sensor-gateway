# Laptop CUDA Full Model Bake-off

Tanggal methodology pack: 11 Juli 2026.

Tujuan: menjalankan forecasting bake-off yang reproducible pada laptop CUDA tanpa menjadikan hasil laptop sebagai Raspberry Pi atau production evidence.

## Status

Model yang tersedia:

| Model | Peran |
|---|---|
| LastValue | baseline wajib |
| Window mean | baseline statistik |
| Drift | baseline trend sederhana |
| SeasonalNaive | baseline hanya bila period/window/horizon applicable |
| `fits` | low-frequency residual edge challenger |
| `fits_official` | official-style frequency comparator |
| `dlinear` | lightweight neural baseline/challenger |
| LSTM residual | nonlinear challenger |

Semua model tetap `EXPERIMENTAL` sampai real RAB + target Pi evidence.

## Methodology Guard

Runner baru wajib memakai:

- cadence inference/validation dari timestamp;
- horizon steps + duration aktual;
- temporal split + purge/no-overlap;
- train-only active feature schema/hash;
- constant/near-constant target detection;
- boundary saturation/clipping report;
- baseline per target dipilih pada validation split;
- duplicate/inapplicable baseline exclusion;
- repeated seeds;
- per-lane, per-target metrics;
- atomic state/resume;
- exact download filename + SHA-256.

Artifact/ranking lama tetap historical dan harus diulang dengan pack ini.

## Prasyarat

1. Repo sinkron.
2. Python 3.11–3.13.
3. PyTorch CUDA sesuai driver laptop.
4. Project dependencies terpasang.
5. Gary payload tersedia bila lane Gary dipilih.
6. Disk cukup untuk dataset/artifact lokal.

Contoh Windows:

```powershell
cd <path>\iiot-ai-sensor-gateway
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
python -m pip install -e .
python scripts\probe_cuda.py
```

Jangan memasang dependency berat di VPS hanya untuk meniru laptop.

## Runner Canonical

Windows:

```powershell
py -3.13 scripts\laptop_bakeoff_runner.py \
  --device cuda \
  --seeds 42,43,44 \
  --lanes gary,uci,fidas,sim
```

PowerShell wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\laptop_full_model_bakeoff.ps1 \
  -Device cuda -Seeds 42,43,44 -Lanes gary,uci,fidas,sim
```

CMD wrapper:

```cmd
scripts\laptop_full_model_bakeoff.cmd --device cuda --seeds 42,43,44
```

Linux/WSL:

```bash
python3 scripts/laptop_bakeoff_runner.py \
  --python python3 \
  --device cuda \
  --seeds 42,43,44 \
  --lanes gary,uci,fidas,sim
```

Shell wrapper:

```bash
DEVICE=cuda SEEDS=42,43,44 LANES=gary,uci,fidas,sim \
  bash scripts/laptop_full_model_bakeoff.sh
```

## Dry-run

```bash
python scripts/laptop_bakeoff_runner.py \
  --dry-run \
  --lanes sim \
  --seeds 42 \
  --skip-public-prepare \
  --skip-lstm
```

Dry-run menulis state plan tanpa training/download/artifact besar.

## Resume dan State

State:

```text
models/bakeoff_runs/<timestamp>/state/RUN_STATE.json
```

Setiap step menyimpan:

- status;
- command;
- source fingerprint;
- fingerprint isi input yang dideklarasikan step;
- output list;
- start/finish/elapsed;
- error bila gagal.

Step di-skip hanya bila:

1. status completed;
2. command + source + declared-input fingerprint sama;
3. seluruh required output ada dan non-empty.

Input dataset/adapted payload, forecast windows, dataset NPZ, model checkpoint,
dan metrics menjadi input eksplisit pada step downstream. Perubahan isi file akan
membatalkan resume step terkait, sehingga output lama tidak dipakai diam-diam.
Folder persiapan internal `_run_data/` bukan lane model dan selalu dikecualikan
dari `summarize_bakeoff.py`.

Gunakan `--force` hanya bila sengaja mengulang. Jangan memakai resume script manual lama; wrappers hanya memanggil runner Python canonical.

## Run Baru Tanpa Menimpa Run Lama

Untuk membuat run baru tetap di dalam repo tetapi terpisah dari artifact lama,
runner otomatis membuat folder timestamp baru di `models/bakeoff_runs/` bila
`--run-dir` tidak diberikan. Runner menolak folder yang sudah berisi file
kecuali `--resume` ditulis eksplisit.

PowerShell dari terminal VSCode:

```powershell
cd C:\vscode\IIOT-Project\iiot-ai-sensor-gateway
py -3.13 scripts\laptop_bakeoff_runner.py --device cuda --seeds 42,43,44 --lanes gary,uci,fidas,sim
```

Hasil run baru dicetak oleh runner dan ada di `models/bakeoff_runs/<timestamp>/`;
run lama di `models/bakeoff/` tidak disentuh. Jika proses terhenti, ulangi
dengan path run yang tercetak dan tambahan `--resume`, tanpa `--force`:

```powershell
py -3.13 scripts\laptop_bakeoff_runner.py --run-dir models\bakeoff_runs\20260914 --resume --device cuda --seeds 42,43,44 --lanes gary,uci,fidas,sim
```

## Per-Seed Layout

```text
models/bakeoff_runs/<timestamp>/<lane>/<model>/seed_<seed>/
  model.pt
  training.json
  metrics.json
```

Summary:

```text
models/bakeoff_runs/<timestamp>/FULL_BAKEOFF_SUMMARY.json
```

Summary v2 menyimpan per-run evidence dan aggregate mean/std/min/max per model/lane. Model dengan baseline win tetapi data-quality gate gagal tidak dihitung promotion pass.

## Lane

### Gary

- regression/simulation besar;
- synthetic pressure/gas proxy;
- bukan RAB chip-identical;
- targets T/H/P.

### UCI

- hourly cadence harus terdeteksi sekitar hourly, bukan dilabeli 60 detik;
- T/H canonical;
- CO mg/m³/NO₂ µg/m³ tetap reference units;
- daily seasonal candidate hanya bila history cukup.

### Fidas

- sekitar 2-minute reference PM cadence;
- bukan PMS7003T chip-identical;
- PM target heavy-tail/clipping wajib diperiksa;
- seasonal period dapat inapplicable bila window tidak cukup; reason dicatat.

### Simulator v3

- bounded rise/recovery;
- warm-up/error/dropout;
- full RAB-like schema;
- synthetic harness only;
- target constant/saturated harus memblokir lane bila muncul kembali.

### Real RAB

Satu-satunya lane yang dapat membawa model menuju validated/production setelah calibration, labeling, site/device split, Pi benchmark, rollback, dan monitoring.

## Download Integrity

Catalog menyimpan exact `download_filename` dan SHA-256.

```text
uci_air_quality_360.zip
zenodo_fidas_pm_reference_7198378.csv
```

Runner tidak memilih file berdasarkan ukuran. UCI archive extraction mencari tepat satu `AirQualityUCI.csv`; mismatch gagal jelas.

## Quality Gate

Lane training diblokir jika `data_quality.status=FAIL`.

Periksa:

- cadence diagnostics;
- effective target names/count;
- blocked target reasons;
- train/test boundary fraction;
- active/dropped feature manifest;
- schema SHA-256;
- normalization report;
- split ranges;
- baseline applicability/duplicate reason.

## Baseline Fairness

- baseline dipilih per target pada validation split;
- selection dikunci untuk test;
- test tidak memilih baseline;
- SeasonalNaive tanpa period/history yang cukup = inapplicable;
- prediction identik dengan LastValue = duplicate, bukan independent baseline;
- ranking lintas unit tidak memakai raw RMSE campuran sebagai satu angka tanpa per-target context.

## Handoff setelah Selesai

Kirim/sinkronkan:

```text
models/bakeoff_runs/<timestamp>/FULL_BAKEOFF_SUMMARY.json
models/bakeoff_runs/<timestamp>/<lane>/<model>/seed_*/metrics.json
models/bakeoff_runs/<timestamp>/<lane>/<model>/seed_*/training.json
models/bakeoff_runs/<timestamp>/_run_data/<lane>/forecast-meta.json
```

Tidak perlu mengirim `model.pt` bila hanya review methodology/metrics. Jangan commit blob dataset/model.

Handoff wajib menyebut:

- Python/PyTorch/CUDA/GPU;
- commit/working tree fingerprint;
- seeds;
- lanes;
- blocked lanes/targets;
- mean/std skill/RMSE;
- target coverage;
- clipping/saturation;
- failures/resume state;
- explicit “not Raspberry Pi / not production”.

## Manual Dataset Besar

Bristol dan SensEURCity tetap manual-only sampai license/storage/schema disetujui. Jangan auto-download multi-GB di VPS. Adapter/manifest/fixture dapat disiapkan tanpa mengunduh blob.

## File Canonical

- `scripts/laptop_bakeoff_runner.py`
- `scripts/laptop_full_model_bakeoff.ps1`
- `scripts/laptop_full_model_bakeoff.sh`
- `scripts/laptop_full_model_bakeoff.cmd`
- `scripts/summarize_bakeoff.py`
- `scripts/prepare_lane_forecast.py`
- `scripts/download_dataset.py`
- `datasets/catalog.json`
- `docs/MODEL_COMPARISON.md`
