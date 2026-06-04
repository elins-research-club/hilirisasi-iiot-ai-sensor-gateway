# Testing dan Troubleshooting

## Test Lokal

    py -3.13 -m compileall -q .
    py -3.13 scripts/check_environment.py
    py -3.13 -m unittest discover -s tests -p 'test_*.py' -q

## Workflow Gary

    py -3.13 run_gateway.py convert-gary --input-csv iot_telemetry_data.csv --output data/canonical/gary_stafford_canonical.jsonl
    py -3.13 run_gateway.py run --input-file data/canonical/gary_stafford_canonical.jsonl --output-dir data/processed
    py -3.13 run_gateway.py evaluate --canonical data/canonical/gary_stafford_canonical.jsonl --windows data/processed/lstm_windows.jsonl --output data/evaluation/gary_preprocessing_eval.json

## Status Evaluasi

- pipeline_status PASS berarti preprocessing berhasil, window terbentuk, dan NaN/Inf tidak ditemukan.
- dataset_coverage_status FULL pada workflow `derive-gary-schema` berarti field target tersedia di dataset turunan project-like.
- lstm_readiness READY pada workflow derived schema berarti window siap untuk uji bentuk input LSTM. Ini belum berarti dataset sudah setara data sensor real.
- dataset_coverage_status PARTIAL pada workflow Gary lama berarti pressure_hpa tidak tersedia dan gas BME688 asli hanya diwakili proxy LPG/smoke.

## Masalah Umum

- Tidak ada window: jumlah titik resampling valid belum mencapai window_size.
- File JSONL korup: pastikan tidak ada proses run lama yang masih menulis ke data/processed.
- Dataset besar masuk Git: cek .gitignore; file iot_telemetry_data.csv dan folder data/canonical, data/processed, data/evaluation harus di-ignore.
