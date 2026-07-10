# Testing dan Troubleshooting

## Verifikasi Host

```bash
cd /home/ubuntu/projects/iiot-project/iiot-ai-sensor-gateway
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3

$PY -m compileall -q src tests run_gateway.py scripts/download_dataset.py
$PY -m unittest discover -s tests -p 'test_*.py' -q
$PY run_gateway.py check-config --config config/default.toml
git diff --check
```

## Firmware

```bash
cd firmware/esp32-c6-sensor-node
/home/ubuntu/.venvs/platformio/bin/pio run -e mock
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware
```

`mock` adalah acceptance CI. `hardware` hanya compile/profile gate; tidak membuktikan sensor atau board.

## Receiver Replay

```bash
$PY run_gateway.py receive-real-live \
  --replay-file tests/fixtures/real_payload_samples.jsonl \
  --output-dir /tmp/iiot-receiver-check \
  --max-messages 8
```

Periksa:

```text
raw_envelopes.jsonl
accepted_payloads.jsonl
rejected_payloads.jsonl
receiver_events.jsonl
```

Jalankan command dua kali pada output dir yang sama. Line lama harus tetap ada.

## Compact v2 Contract

Expected:

- full field v2 parse;
- uptime memakai receive time;
- stable event ID;
- duplicate/out-of-order ditolak;
- reboot diterima;
- partial sensor accepted dengan issue;
- unsupported version/flags malformed ditolak.

## Serial CPU/Reconnect

Unit test membuktikan explicit sleep dan reconnect backoff dipanggil. Untuk hardware:

1. jalankan receiver pada port nyata;
2. cabut radio/USB;
3. pastikan process tidak tight-loop;
4. pasang kembali;
5. pastikan reconnect;
6. ukur CPU dengan alat OS target.

Jangan mengklaim CPU bounded pada Pi hanya dari fake-source test.

## Dataset Adapter

Describe catalog:

```bash
$PY scripts/download_dataset.py uci_air_quality_360 --describe-only
```

Adapter fixture diuji oleh unittest. Untuk archive asli, catat:

- source checksum;
- row count;
- reject count;
- missing rate;
- timezone;
- unit/provenance.

## Edge Models

Smoke tests melatih FITS-inspired dan DLinear pada fixture kecil. Ini bukan benchmark.

Actual run:

```bash
$PY run_gateway.py train-edge-forecast --dataset <NPZ> --output-dir <DIR> --model-type fits
$PY run_gateway.py evaluate-edge-forecast --dataset <NPZ> --model <DIR>/model.pt
```

Periksa `baseline_gate`, bukan hanya loss.

## Optional River

Bila belum terpasang, command harus gagal dengan pesan dependency yang actionable, bukan traceback tak jelas.

```bash
python -m pip install -e '.[streaming]'
```

Jangan install otomatis dalam automation tanpa izin.

## Masalah Umum

### Config tidak ditemukan

Gunakan path eksplisit:

```bash
$PY run_gateway.py check-config --config config/default.toml
```

Config missing harus fail-closed.

### Receiver menolak payload

Periksa category:

- JSON malformed;
- version tidak didukung;
- boot ID hilang;
- flags bukan string/list;
- value non-finite/range invalid;
- duplicate/out-of-order;
- status/quality error.

### Uptime menjadi 1970

Itu bug. Compact numeric uptime harus menghasilkan `time_quality=gateway_received` dan timestamp sama dengan receive time.

### Flash warning mismatch

Pastikan build memakai environment dan sdkconfig terpisah:

```bash
pio run -e mock -t clean
pio run -e mock
pio run -e hardware -t clean
pio run -e hardware
```

Profile 16 MB masih harus dicocokkan dengan chip fisik.

### Hardware build sukses tetapi semua sensor null

Normal untuk driver yang belum selesai/board tidak terpasang. Hardware mode fail-closed dan tidak mengisi placeholder.

### MQTT tidak publish

Repo belum memiliki production MQTT publisher. Hanya contract/schema/builder yang tersedia.

## Acceptance Hardware Belum Selesai

- board pin/flash;
- I²C addresses;
- ADC calibration;
- UART bridge;
- 5 V rails;
- sensor warm-up;
- E32 link;
- shunt/calibration INA226;
- real dataset;
- Pi resource metrics;
- multi-day soak.
