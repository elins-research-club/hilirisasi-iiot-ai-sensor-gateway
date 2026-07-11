# Riset Model Time-Series Edge untuk AI Sensor

Tanggal keputusan awal: 10 Juli 2026. Methodology hardening: 11 Juli 2026.

## Batas Kebenaran

- target inference adalah Raspberry Pi gateway;
- benchmark host bukan benchmark Pi;
- model tidak boleh dipromosikan tanpa data real dan baseline gate;
- forecast, anomaly, drift, dan node health adalah task berbeda;
- NO₂ tetap ordinal/ratio, bukan target ppm.

## Shortlist

### Rules dan quality gate

Wajib menjadi lapisan pertama karena dapat menangani missing, stale, sensor error, warm-up, sequence gap, dan threshold commissioning dengan perilaku deterministik.

### LastValue, Window Mean, Drift, dan SeasonalNaive

Baseline forecast paling penting. SeasonalNaive hanya valid bila period, horizon, dan history window cukup serta prediksinya tidak identik dengan LastValue. Baseline dipilih per target pada validation split dan dikunci untuk test.

### DLinear

Baseline neural ringan untuk trend/seasonal decomposition. Cocok sebagai pembanding formal terhadap FITS-inspired dan LSTM.

### FITS-inspired

Kandidat edge-first dengan representasi frekuensi rendah dan parameter kecil. Potensial untuk pola periodik, tetapi dapat lemah pada trend/non-periodik. Implementasi repo bukan reproduksi bit-for-bit paper.

### River Half-Space Trees + ADWIN

Kandidat streaming anomaly/drift:

- incremental;
- tidak membutuhkan batch retrain tiap record;
- HST membutuhkan input normalized 0–1;
- warm-up dan score-before-learn penting;
- ADWIN menandai perubahan distribusi, bukan otomatis bahaya lingkungan.

### LSTM

Existing nonlinear forecast candidate. Tetap berguna sebagai comparator tetapi lebih berat dan rawan kalah dari baseline pada sinyal smooth.

### Isolation Forest

Baseline anomaly window/batch yang sederhana. Cocok sebagai comparator sebelum streaming/deep anomaly model.

### Kandidat lanjutan

N-HiTS, boosting dengan lag features, USAD, autoencoder, atau time-series foundation model hanya layak setelah:

- lane data real cukup;
- task dan label jelas;
- baseline kuat tersedia;
- deployment budget diketahui.

## Architecture

```text
quality/rules
-> forecast baselines
-> streaming anomaly/drift
-> FITS-inspired/DLinear/LSTM experiments
-> decision layer
-> abstain atau status
```

## Promotion Rule

Model forecast:

- time-ordered split + purge/no-overlap assertions;
- cadence/horizon duration yang benar;
- no leakage;
- train-only active feature schema/hash;
- constant/near-constant dan clipping/saturation gate;
- MAE/RMSE/MASE;
- skill terhadap validation-selected applicable baseline;
- effective-target coverage dan per-target win;
- repeated seeds + mean/std bila stochastic;
- leave-device/site-out bila tersedia.

Model anomaly:

- event-level precision/recall/F1;
- false-alert/day;
- detection delay;
- warm-up false positive;
- contamination/threshold sweep.

Deployment:

- artifact safe-load;
- bounded memory;
- latency/RSS diukur pada target;
- fallback rules/baseline;
- model/version metadata;
- rollback.

## Keputusan Implementasi Wave Ini

- DLinear: tersedia train/eval/predict.
- FITS-inspired: tersedia train/eval/predict.
- LastValue/window-mean/drift/SeasonalNaive applicable: shared validation-selected baseline gate tersedia.
- LSTM: safe checkpoint dan canonical targets.
- River HST + ADWIN: orchestration dan smoke aktual tersedia melalui venv temporer; dependency tidak dipasang permanen.
- Decision layer: rules/quality first dan abstain.

## Yang Belum Dikerjakan

- full repeated-seed CUDA rerun dengan evaluator baru;
- public dataset benchmark penuh current methodology;
- real sensor benchmark;
- River benchmark pada data real;
- Pi latency/RSS/power;
- production promotion.

Lihat `docs/modeling-fits-river-decision.md` dan `docs/MODEL_COMPARISON.md`.
