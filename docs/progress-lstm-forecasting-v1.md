# Progress LSTM Forecasting v1

> **Historical v1 evidence.** Target proxy dan hasil eksperimen di dokumen ini dipertahankan untuk reproduksi, bukan model production. Target canonical, safe checkpoint, dan baseline gate terbaru berada di `lstm-forecasting.md` serta `MODEL_COMPARISON.md`.

## Tujuan

Dokumen ini mencatat progres forecasting time-series AI sensor v1. Tujuannya
adalah membuat hasil eksperimen LSTM terdokumentasi di Git, karena artifact
model dan dataset di `models/` serta `data/` sengaja di-ignore.

Forecasting v1 belum menjadi model final dan belum menjadi sistem prescriptive.
Outputnya dipakai untuk eksperimen awal, model selection, dan decision layer
lokal tahap berikutnya.

## Data dan Input

Eksperimen terakhir memakai dataset turunan Gary project schema dengan
`pressure_profile=dynamic`. Dataset ini memakai field target project:

- `temperature_c`
- `humidity_pct`
- `pressure_hpa`
- `bme_gas_raw`
- `co_raw`

Catatan penting: `pressure_hpa` pada workflow ini synthetic, bukan pressure asli
Gary. `bme_gas_raw` juga masih proxy dari LPG/smoke, bukan pembacaan sensor
BME688/BME668 real.

## Alur Eksperimen

Alur yang diuji:

```text
derived project payload
-> Raspberry Pi pre-model pipeline
-> LSTM-ready windows
-> forecast dataset X/y
-> LSTM multi-target training
-> baseline comparison
-> model selection candidate
-> forecast decision layer lokal
```

Konfigurasi eksperimen CUDA terakhir:

```text
horizon_steps: 5, 15, 30
window_size: 12, 24, 36
hidden_size: 64, 128
num_layers: 2
epochs: 30
batch_size: 256
eval_batch_size: 512
device: cuda
```

Semua 18 kombinasi berhasil selesai tanpa CUDA out-of-memory setelah evaluasi
dibuat batch-based.

## Hasil Utama

Best normalized LSTM RMSE sementara:

```text
run_id: w24_h5_hidden128
window_size: 24
horizon_steps: 5
hidden_size: 128
LSTM RMSE: 0.0045037
baseline RMSE: 0.0037609
baseline_comparison_status: MIXED
candidate_status: NEEDS_TUNING
```

Interpretasi: model terbaik sementara sudah dapat dilatih dan dievaluasi, tetapi
baseline last-value masih lebih baik secara overall. Ini bukan kegagalan
pipeline; ini berarti LSTM v1 masih perlu tuning dan validasi data real sebelum
dipakai sebagai model final.

## Interpretasi Window

- Window 12 ringan dan cukup stabil untuk horizon pendek.
- Window 24 menjadi kandidat terbaik sementara untuk horizon 5 step.
- Window 36 memberi konteks lebih panjang, tetapi belum otomatis lebih baik.

Hasil ini menunjukkan bahwa menambah panjang window tidak selalu meningkatkan
forecasting. Sensor environment berubah relatif pelan, sehingga baseline
last-value masih kuat.

## Analisis Per Target

Target perlu dibaca per sensor, bukan hanya overall RMSE:

- `co_raw`: generic CO proxy legacy yang berguna untuk eksperimen risiko udara, tetapi masih belum
  mengalahkan baseline pada eksperimen terakhir.
- `bme_gas_raw`: penting untuk gas proxy BME688-like, tetapi masih berasal dari
  derivasi LPG/smoke.
- `temperature_c` dan `humidity_pct`: lebih stabil, sehingga baseline sering
  kuat.
- `pressure_hpa`: synthetic pada dataset derived, jadi tidak boleh menjadi bukti
  validasi pressure sensor real.

Selector model sekarang membaca skill score semua target utama. Jika overall,
`co_raw`, dan `bme_gas_raw` masih kalah baseline, selector tetap memilih kandidat
dengan LSTM RMSE terendah tetapi memberi status `NEEDS_TUNING`.

## Kenapa Baseline Masih Menang

Baseline last-value kuat karena data sensor lingkungan sering berubah pelan.
Jika target 5 menit ke depan sangat mirip dengan nilai terakhir di input window,
prediksi sederhana dapat mengalahkan LSTM awal.

Faktor lain:

- dataset masih publik/synthetic, bukan data sensor project real;
- pressure masih synthetic;
- gas masih proxy;
- LSTM belum memakai tuning khusus per target;
- belum ada loss weighting untuk target penting seperti CO/gas.

## Output Lokal

Output eksperimen lokal berada di:

```text
models/forecast_experiments/max_dynamic_pressure_cuda_20260612_v2/summary.csv
models/forecast_experiments/max_dynamic_pressure_cuda_20260612_v2/best_model_selection.json
models/forecast_experiments/max_dynamic_pressure_cuda_20260612_v2/cuda_max_top5_rmse.png
models/forecast_experiments/max_dynamic_pressure_cuda_20260612_v2/cuda_max_rmse_summary.png
```

Folder `models/` di-ignore dari Git, sehingga dokumen ini menjadi ringkasan
resmi yang aman untuk repo.

## Narasi Slide Mingguan

- Eksperimen forecasting time-series berhasil dijalankan pada GPU.
- Pipeline menghasilkan evaluasi 18 kombinasi horizon, window, dan ukuran model.
- Model terbaik sementara belum mengalahkan baseline overall, sehingga status
  masih eksperimen.
- Decision layer lokal v1 disiapkan untuk mengubah forecast semua sensor utama
  menjadi status awal `normal`, `warning`, atau `critical`.
- Tahap berikutnya adalah tuning terarah dan validasi threshold dengan data
  sensor real.

## Rencana Lanjut

Tuning model tidak dikerjakan pada milestone ini. Roadmap berikutnya:

- loss weighting untuk semua target utama, khususnya CO/gas;
- target subset khusus gas/CO;
- dropout dan learning rate tuning;
- GRU sebagai pembanding LSTM;
- refinement horizon/window;
- validasi threshold decision layer memakai data sensor real.
