# Real Offline File Pipeline

Real offline capture adalah data hardware nyata yang direkam untuk calibration, replay, debugging, dan modeling tanpa harus terhubung live.

## Flow

```text
ESP32/radio/serial capture
-> immutable raw JSONL/binary artifact
-> checksum + capture metadata
-> parser/adapter
-> canonical JSONL
-> validation/resampling/features/windowing
-> evaluation
```

## Aturan

- raw artifact tidak ditimpa;
- satu record per line bila JSONL;
- capture metadata mencatat board/sensor/config/time/site;
- invalid frame tetap disimpan pada raw lane;
- canonical output terpisah;
- missing tetap null;
- calibration/warm-up state dicatat;
- artifact besar gitignored.

## Replay

Gunakan live receiver replay agar raw/accepted/rejected semantics sama dengan runtime:

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY run_gateway.py receive-real-live \
  --replay-file data/real_offline/capture.jsonl \
  --output-dir data/real_offline/processed
```

## Dataset Promotion

Capture baru boleh menjadi modeling lane setelah:

- identity dan timestamp review;
- sensor/unit mapping;
- missing/error analysis;
- calibration metadata;
- time/device/site split plan;
- privacy/governance review bila ada metadata lokasi.

## Belum Ada Klaim

Repo belum memiliki capture full RAB dari board lapangan pada wave ini. Fixture test bukan real hardware data.
