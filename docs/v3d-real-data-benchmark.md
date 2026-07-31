# Benchmark V3D Data Nyata untuk Esai TRACIVAL 2026

Tanggal freeze: 31 Juli 2026.

## Tujuan

Benchmark ini menguji tesis bahwa model dengan akurasi agregat terbaik pada satu konteks belum tentu menjadi pilihan paling tepat pada konteks lain. Evaluasi membaca mutu dan asal data, kemampuan mengalahkan baseline, kestabilan lima seed, konsistensi lintas station/mote, serta sumber daya yang diukur pada mesin yang sama.

Benchmark bukan validasi sensor RAB, Raspberry Pi, konsumsi energi, atau kesiapan produksi.

## Branch dan sumber

- Branch: `research/v3d-real-data-benchmark`.
- Source baseline: `d26ee92`.
- Runner: `scripts/v3d_real_data_benchmark.py`.
- Pipeline: `src/iiot_ai_sensor_gateway/v3d_real_data.py`.
- Analysis pack: `scripts/v3d_analysis_pack.py`.

## Dataset

| Dataset | Raw bytes | SHA-256 | Series lolos gate | Train/val/test windows |
|---|---:|---|---:|---:|
| UCI Air Quality | 1.543.989 | `d4a64013fb385288a8a48d9d193ca7079b2e1bbddf6f8d458feb8c08ab2b8a2a` | 1 | 4.143 / 531 / 1.060 |
| Beijing Multi-Site Air Quality | 8.192.212 | `b04da438b2f331ac0ffd45aebdfec0d20d2367feb5f6948c4b1f7ce1191e33c4` | 12/12 | 12.000 / 3.600 / 4.800 |
| Intel Berkeley Lab Sensor Data | 34.422.518 | `d99288c8f406ca6604d359ceaa0d8adfffa79e7095061a1e27dc4399f48c7225` | 25/54 | 9.526 / 2.121 / 2.036 |

UCI dan Beijing mempertahankan cadence satu jam. Intel diresampling deterministik menjadi median lima menit per mote untuk membatasi beban komputasi; raw gzip tetap immutable. Analisis utama memakai complete windows tanpa imputasi.

## Protokol yang dibekukan

- split kronologis 65% train, 15% validation, 20% test;
- normalisasi z-score fit pada train saja;
- UCI/Beijing: lookback 168 jam, horizon 24 jam;
- Intel: lookback 24 jam pada interval lima menit, horizon satu jam;
- baseline kandidat: LastValue, WindowMean, Drift, SeasonalNaive bila berlaku;
- baseline dipilih per target pada validation dan dikunci sebelum test;
- model: DLinear project, FITS-inspired project, residual LSTM kompak, dan PatchTST-inspired kompak;
- seed `42,43,44,45,46`;
- 12 epoch maksimum, patience 3, batch 256;
- MASE memakai skala one-step naive dari train; skill membandingkan RMSE model dengan baseline horizon yang dipilih pada validation;
- resource diukur pada CPU VPS yang sama;
- hasil gagal baseline tetap disimpan.

PatchTST yang diuji adalah adaptasi project channel-independent dengan patch length 16, shared Transformer encoder satu layer, dan residual last-value head. Ia bukan reproduksi bit-for-bit kode resmi PatchTST.

## Integritas run

- 3 dataset × 4 model × 5 seed = 60 run;
- tepat lima seed pada seluruh 12 pasangan dataset-model;
- satu prepared-data hash per dataset;
- seluruh run meminta 12 epoch, patience 3, batch 256;
- tidak ada run negatif yang dihapus;
- seluruh ringkasan dapat ditelusuri ke `run.json` dan `series_metrics.csv`;
- analysis pack gagal tertutup bila 60 run tidak tepat membentuk 3 dataset × 4 model × 5 seed, memakai schema/protokol lain, memuat metrik non-finite, atau mempunyai hash provenance yang tidak konsisten.

## Ringkasan hasil

| Dataset | Model | Mean MASE | SD MASE | Mean skill | Gate | Parameter | Latency CPU ms/sampel |
|---|---|---:|---:|---:|---:|---:|---:|
| UCI | LSTM | 2,4965 | 0,0272 | 0,0161 | 5/5 | 4.674 | 0,1578 |
| UCI | FITS | 2,5128 | 0,0187 | 0,0152 | 5/5 | 66 | 0,0034 |
| UCI | DLinear | 2,5615 | 0,1480 | 0,0008 | 3/5 | 676 | 0,0067 |
| UCI | PatchTST | 2,5168 | 0,0056 | -0,0012 | 2/5 | 9.761 | 0,0825 |
| Beijing | LSTM | 3,6657 | 0,0030 | 0,0817 | 5/5 | 4.674 | 0,1539 |
| Beijing | PatchTST | 3,7972 | 0,0748 | 0,0631 | 5/5 | 9.761 | 0,0811 |
| Beijing | DLinear | 3,8030 | 0,0545 | 0,0862 | 5/5 | 676 | 0,0066 |
| Beijing | FITS | 3,9027 | 0,0293 | 0,0527 | 5/5 | 66 | 0,0034 |
| Intel | PatchTST | 6,8903 | 0,1648 | 0,0115 | 4/5 | 10.241 | 0,1402 |
| Intel | LSTM | 7,0800 | 0,0601 | 0,0243 | 4/5 | 4.674 | 0,2584 |
| Intel | DLinear | 7,4347 | 0,2311 | -0,0216 | 1/5 | 1.156 | 0,0104 |
| Intel | FITS | 8,4327 | 0,1739 | -0,1723 | 0/5 | 66 | 0,0044 |

## Interpretasi utama

### UCI

LSTM memberi mean MASE terendah. FITS hanya sekitar 0,65% lebih tinggi, tetapi memakai sekitar 71 kali lebih sedikit parameter dan median latency CPU sekitar 46 kali lebih rendah. Karena keduanya lolos pada lima seed, UCI mempunyai dua pilihan kontekstual: LSTM untuk prioritas MASE dan FITS untuk alternatif ringan yang sangat dekat.

PatchTST tidak memberi nilai tambah konsisten: mean skill sedikit negatif dan hanya dua dari lima seed lolos gate.

### Beijing

LSTM memberi mean MASE terendah dan variasi antarseed terkecil. DLinear memberi mean skill tertinggi terhadap baseline, lolos lima seed, dan jauh lebih ringan; mean MASE-nya sekitar 3,75% lebih tinggi daripada LSTM. Keduanya menjawab tujuan berbeda.

### Intel

PatchTST memberi mean MASE terendah, tetapi hanya empat seed lolos dan variasinya lebih besar daripada LSTM. LSTM memiliki mean skill yang lebih tinggi dan SD MASE lebih kecil. FITS gagal baseline pada lima seed dan dipertahankan sebagai hasil negatif.

## Batas penting

- Gate model berarti mean skill positif dan minimal setengah target mengalahkan baseline; gate bukan berarti setiap target menang.
- MASE di atas satu dapat terjadi karena penyebut memakai perubahan one-step train, sedangkan horizon utama lebih panjang.
- RMSE mentah tidak dipakai untuk ranking lintas target/unit.
- Latency, RAM, dan training time hanya berlaku pada CPU VPS aktual.
- Parameter count bukan energi.
- UCI/Beijing/Intel tidak membuktikan performa sensor atau hardware project.
- PatchTST project tidak boleh disebut implementasi resmi.

## Reproduksi

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
PYTHONPATH=src $PY scripts/v3d_real_data_benchmark.py prepare \
  --datasets uci,beijing,intel
PYTHONPATH=src $PY scripts/v3d_real_data_benchmark.py benchmark \
  --datasets uci,beijing,intel \
  --models dlinear,fits,lstm,patchtst \
  --seeds 42,43,44,45,46 \
  --epochs 12 --patience 3 --batch-size 256 \
  --output-dir models/v3d --summary-dir data/v3d/results
$PY scripts/v3d_analysis_pack.py
```

## Verifikasi

```text
compileall: PASS
unit tests: 94/94 PASS
config check: PASS
git diff --check: PASS
```

Peringatan PyTorch mengenai nested tensor muncul karena encoder PatchTST memakai `norm_first=True`; warning tidak mengubah status run dan didokumentasikan.
