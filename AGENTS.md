# AGENTS.md - iiot-ai-sensor-gateway

## Scope Repo

- Repo ini khusus AI Sensor Gateway: ESP32-C6 firmware ringan + Raspberry Pi Python pipeline + LSTM forecasting awal.
- Jangan kerjakan backend, dashboard, MQTT broker, computer vision, OpenClaw runtime, atau model prescriptive final kecuali diminta eksplisit.
- ESP32-C6: firmware C/C++ untuk baca sensor, validasi ringan, moving average, sequence, payload LoRa.
- Raspberry Pi/laptop: Python untuk parse, validasi, resampling, feature extraction, normalisasi, windowing, dataset/model forecasting.

## Data dan Artifact

- Jangan masukkan dataset/model besar ke Git.
- `iot_telemetry_data.csv`, `data/`, `models/`, `*.pt`, `*.npz`, cache, dan `.env` harus tetap lokal/ignored.
- Runtime gateway tetap stdlib-first; NumPy/PyTorch hanya optional ML dependency.

## Windows Editing Notes

- Di PowerShell, patch multiline lewat wrapper batch kadang rapuh.
- Untuk edit besar di Windows, boleh tulis file dengan `[System.IO.File]::WriteAllText(..., (New-Object System.Text.UTF8Encoding($false)))`, lalu wajib cek `git diff`.
- Jangan pakai `Set-Content -Encoding utf8` biasa untuk file project karena PowerShell lama bisa menambah BOM.

## Verifikasi

- Setelah edit Python, jalankan:

```powershell
py -3.13 -m compileall -q src tests run_gateway.py
py -3.13 -m unittest discover -s tests -p 'test_*.py' -q
```

- Untuk workflow LSTM forecasting, cek command utama:

```powershell
py -3.13 run_gateway.py prepare-forecast-dataset --help
py -3.13 run_gateway.py train-lstm-forecast --help
py -3.13 run_gateway.py evaluate-lstm-forecast --help
py -3.13 run_gateway.py predict-lstm-forecast --help
py -3.13 run_gateway.py run-forecast-experiments --help
```

## Dokumentasi

- Update README dan `docs/` jika mengubah alur pipeline/model.
- Update `../Project Context/AI_SENSOR_GATEWAY_PRE_MODEL.md` atau konteks terkait jika keputusan arsitektur berubah.
