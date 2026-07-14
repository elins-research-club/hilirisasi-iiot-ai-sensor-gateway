# Analisis Bake-off CUDA (13 Juli 2026) — Historical

> **Superseded:** full repeated-seed Fidas+Sim, parameter refinement, dan ranking final tersedia di `BAKEOFF_RESULTS_ANALYSIS_2026-07-14.md`. Angka di bawah dipertahankan sebagai jejak historical dan tidak boleh dipakai sebagai ranking terkini.
>
> Evidence level: **EXPERIMENTAL / proxy lanes only**. Bukan production RAB, bukan Raspberry Pi.

## Ringkasan jujur

| Lane | Cadence | Window | Horizon | Multi-seed? | Best family (skill mean / best seed) | Catatan |
|---|---|---|---|---|---|---|
| **Gary** | 60s | 48 | 5 (~5 min) | ya (42/43/44) | **lstm_residual** skill mean ~0.146 (4/4 PROMISING after majority rescore); **fits** ~0.138 paling stabil 3/3 wins | pressure sintetik → skill LSTM inflated; prefer fits for edge stability |
| **UCI** | 3600s | 48 | 5 (~5 jam) | ya | **lstm_residual** ~0.379; **fits** ~0.374 | Outdoor meteo proxy; CO UCI **bukan** co_ppm |
| **Fidas** | 120s | 12 (post-fix) | 5 (~10 min) | **tidak** (legacy single) | **fits** skill ~0.277 | Perlu re-run multi-seed + cap window |
| **Sim** | 60s (legacy meta incomplete) | 12 | 5 | **tidak** | **fits** skill ~0.256 | Full RAB fields; butuh re-run v3 |

## Temuan per model

1. **FITS-inspired residual (`fits_edge_v2`)**  
   - Paling stabil di edge models (Gary/Fidas/Sim).  
   - Params sangat kecil (~100–700). Early-stop sering epoch 11–22.  
   - Gary seed skill ≈ **0.130–0.133** (3/3 target wins).

2. **FITS official-style (`fits_official_edge_v3`)**  
   - Kalah dari FITS-inspired di hampir semua lane.  
   - Gary: skill ≈ 0.11, sering **kalah di pressure** (skill negatif besar).  
   - UCI: skill ≈ 0.289 (baik, tapi di bawah fits/lstm).  
   - Early-stop terlalu cepat di beberapa seed (best_epoch 2).

3. **DLinear residual**  
   - Solid baseline neural.  
   - UCI sangat kuat (skill ≈ **0.354** multi-seed).  
   - Gary skill kecil (~0.07) — hampir LastValue.

4. **LSTM residual**  
   - UCI juara skill (~**0.379**).  
   - Gary overall skill bagus tapi **pressure_hpa sering kalah** (target sintetik rendah-variansi).  
   - **Patch 13 Jul:** gate LSTM diselaraskan ke majority-win; offline rescore → Gary LSTM seed 42/43/44 **PROMISING** (pass 4/4).  
   - Tetap **bukan** default edge: params/latency jauh lebih besar dari FITS/DLinear.

## Bug / improvement yang diimplementasikan

1. **Fidas OOM** — cap window load + native window 12 + chunked finite.  
2. **`_load_dataset` OOM risk** — `np.isfinite(...).all()` diganti `finite_or_raise` chunked.  
3. **LSTM gate ketat berlebih** — all-target-win diganti majority + overall RMSE.  
4. **LSTM metrics** — tambah `baseline_gate` selaras edge (summary/ranking).  
5. **Summarizer** — `lane_rankings` + notes anti-overclaim.  
6. **Runner** — `--fits-official-individual` opsional; Fidas RAM-safe.

## Rekomendasi parameter re-run (laptop CUDA)

Default yang disarankan (bukan production claim):

```text
device=cuda epochs=80 patience=10 batch=256 seeds=42,43,44 horizon=5
```

Per lane:

| Lane | Window | Seasonal | Max windows | Max samples/split | Catatan |
|---|---|---|---|---|---|
| Gary | 48 | 0 | uncapped | uncapped | pressure sintetik → interpret hati-hati |
| UCI | 48 | 24 (harian / 1h cadence) | uncapped | uncapped | seasonal_naive applicable |
| Fidas | **12** | 720 | **50000** | **20000** | jangan rebuild 144 di laptop 16–32GB |
| Sim | 12–60 | 1440 jika cadence 60s | uncapped kecil | uncapped | generate v3 mixed |

Opsional eksperimen (setelah baseline re-run hijau):

- `fits_official --individual` (per-channel upsampler)  
- FITS `frequency_bins` 4/6/8 grid kecil  
- pred_len tetap **1** (label single-horizon repo)

## Ranking praktis sementara (proxy only)

1. **Rules + data quality gate** (L0, wajib)  
2. **Validation-selected baseline** (LastValue / SeasonalNaive / window_mean / drift)  
3. **FITS-inspired residual** — default edge candidate  
4. **LSTM residual** — kuat di UCI; Gary mixed-target  
5. **DLinear residual** — sanity + UCI kuat  
6. **fits_official** — research comparator, belum unggul konsisten  

**Production winner: belum ada.** Butuh data RAB real + metrik Pi.

## Next action

1. Sync code `88ac54f+` ke laptop.  
2. Re-run Fidas+Sim multi-seed (atau full force bila evaluator berubah).  
3. Kirim `FULL_BAKEOFF_SUMMARY.json` / bilang bakeoff selesai.  
4. Optional: nyalakan `scripts/remote/laptop_agent.ps1` agar Hermes enqueue/awasi.
