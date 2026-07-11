# Testing dan Troubleshooting

## Verification Bundle

```bash
cd /home/ubuntu/projects/iiot-project/iiot-ai-sensor-gateway
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
export PYTHONPATH=src

$PY -m compileall -q src tests scripts run_gateway.py
$PY -m unittest discover -s tests -p 'test_*.py' -q
$PY run_gateway.py check-config --config config/default.toml
$PY -c "import json,pathlib; [json.loads(p.read_text()) for p in pathlib.Path('schemas').glob('*.json')]"
git diff --check
```

Guards:

```bash
rg -n 'MovingAverage|PreprocessedSample|class Preprocessor' \
  firmware/esp32-c6-sensor-node/src firmware/esp32-c6-sensor-node/include

rg -n 'distance_mm|"dist"|VL53|SEN0377' \
  src firmware/esp32-c6-sensor-node/src \
  firmware/esp32-c6-sensor-node/include schemas
```

Expected active source: tidak ada hasil.

## Firmware Compile

```bash
cd firmware/esp32-c6-sensor-node
/home/ubuntu/.venvs/platformio/bin/pio run -e mock
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware
/home/ubuntu/.venvs/platformio/bin/pio run -e hardware-bme68x
```

`mock`/`hardware`/`hardware-bme68x` hanya compile evidence. Sensor/bridge/E32/rail/flash/calibration/power tetap hardware open.

## Contract v3 Targeted Tests

```bash
$PY -m unittest discover -s tests -p 'test_gateway_preprocessing_v3.py' -v
```

Expected coverage:

- v3 provenance;
- `pp=hardware_only` required;
- no ToF/distance;
- filter state isolation per node/boot;
- v2 no double smoothing;
- resampling does not mix nodes;
- mixed preprocessing version rejected.

## Receiver Replay

```bash
$PY run_gateway.py receive-real-live \
  --replay-file tests/fixtures/real_payload_samples.jsonl \
  --output-dir /tmp/iiot-receiver-check \
  --max-messages 8
```

Periksa append-only files:

```text
raw_envelopes.jsonl
accepted_payloads.jsonl
rejected_payloads.jsonl
receiver_events.jsonl
```

Jalankan dua kali; line lama tidak boleh hilang.

## L0–L4 Replay

```bash
$PY run_gateway.py simulate --scenario mixed --count 240 --nodes 2 --interval-sec 60 --output /tmp/iiot-v3.jsonl
$PY run_gateway.py run --input-file /tmp/iiot-v3.jsonl --output-dir /tmp/iiot-v3-processed
```

Expected:

```text
raw_payloads.jsonl
hardware_observations.jsonl
canonical_observations.jsonl
processed_timeseries.jsonl
windows.jsonl
lstm_windows.jsonl
normalization_report.json
```

Periksa `source_event_ids` dan `preprocessing_version` pada L3.

## Dataset Quality Tests

```bash
$PY -m unittest discover -s tests -p 'test_dataset_quality_and_baselines.py' -v
```

Coverage:

- cadence inference;
- integer-multiple gap vs irregular cadence;
- declared cadence conflict;
- horizon duration;
- train-only active feature schema/hash;
- constant/near-constant target;
- boundary saturation/clipping;
- SeasonalNaive applicability;
- duplicate baseline exclusion;
- bounded simulator.

### Cadence conflict

Bila hourly dataset diberi `--cadence-sec 60`, preparation harus gagal. Gunakan `--cadence-sec 0` untuk infer atau berikan nilai yang benar.

### Too irregular

Periksa timezone, duplicate timestamp, DST, multi-node grouping, missing row, dan adapter cadence policy. Jangan memaksa 60 detik hanya agar pipeline lanjut.

### Target blocked

`data_quality.blocked_targets` menjelaskan `train_near_constant`, `test_near_constant`, atau `*_boundary_saturation`. Model dapat dilatih untuk diagnosis, tetapi tidak boleh PROMISING.

### Feature schema mismatch

Checkpoint dan dataset wajib memiliki ordered feature schema/hash sama. Live window boleh memiliki superset; inference hanya memilih feature yang dilatih dan fail-closed bila feature wajib hilang.

## Forecast Evaluation

```bash
$PY run_gateway.py train-edge-forecast --dataset <NPZ> --output-dir <DIR> --model-type dlinear
$PY run_gateway.py evaluate-edge-forecast --dataset <NPZ> --model <DIR>/model.pt --seasonal-period <PERIOD>

$PY run_gateway.py train-lstm-forecast --dataset <NPZ> --output-dir <DIR> --forecast-strategy residual
$PY run_gateway.py evaluate-lstm-forecast --dataset <NPZ> --model <DIR>/model.pt --seasonal-period <PERIOD>
```

Periksa:

- `baseline_selection_split=val`;
- `selected_by_target`;
- inapplicable/duplicate baseline reasons;
- `data_quality_passed`;
- effective target count;
- horizon duration;
- normalized dan denormalized per-target metrics;
- model readiness.

Test set tidak boleh memilih baseline.

## Anomaly/Drift Harness

```bash
$PY run_gateway.py inject-anomaly-fixture --output /tmp/anomaly-fixture.jsonl --labels /tmp/anomaly-labels.json
$PY run_gateway.py stream-detect --backend native --input /tmp/anomaly-fixture.jsonl --output /tmp/anomaly-detections.jsonl --feature-names temperature_c,pm25_ug_m3
$PY run_gateway.py benchmark-anomaly-events --detections /tmp/anomaly-detections.jsonl --labels /tmp/anomaly-labels.json --output /tmp/anomaly-benchmark.json --merge-gap-sec 60 --match-tolerance-sec 120
```

Output fixture hanya harness evidence. Field accuracy memerlukan label real. Drift event tidak otomatis bahaya lingkungan.

## Laptop Runner Dry-Run

```bash
$PY scripts/laptop_bakeoff_runner.py \
  --dry-run --lanes sim --seeds 42 --skip-public-prepare --skip-lstm
```

Dry-run harus menulis state plan tanpa membuat model/data besar. Real runner memakai atomic state dan output fingerprint.

## Serial CPU/Reconnect

Unit test membuktikan sleep/backoff dipanggil. Hardware acceptance:

1. jalankan port nyata;
2. cabut radio/USB;
3. pastikan tidak tight-loop;
4. pasang kembali;
5. pastikan reconnect;
6. ukur CPU/RSS pada Pi target.

## Masalah Umum

### Receiver menolak v3

Periksa:

- semua required metadata ada;
- `pp=hardware_only`;
- boot ID/sequence valid;
- `tb` valid;
- sensor states/schema valid;
- no NaN/Inf;
- no duplicate/out-of-order.

### V2 double smoothing

Pastikan:

```toml
[preprocessing]
apply_to_v2 = false
```

V2 harus diberi `legacy_node_preprocessed.v2`.

### Uptime menjadi 1970

Bug. `tb=uptime_s` harus memakai gateway receive-time authority.

### Mixed preprocessing version bucket

Pipeline sengaja gagal. Jangan average records dari config/version berbeda. Pisahkan activation boundary atau replay per version.

### Clipping tinggi

Jangan hanya memperlebar physical range atau menyembunyikan outlier. Verifikasi unit/domain, lane reference, calibration, transform/log1p/robust strategy, dan promotion threshold.

### Hardware build sukses tetapi semua null

Compile bukan hardware validation. Periksa wiring, power, I²C/UART, warm-up, address, bridge, dan calibration.

### MQTT tidak publish

Sensor production publisher/outbox/LWT/TLS belum E2E. Repo saat ini menyediakan contract/schema/builder.

## Acceptance Hardware Masih Open

- real v3 payload board;
- I²C/ADC/UART/SC16IS752;
- warm-up/errors;
- E32 packet loss/range/recovery;
- rail/current/thermal;
- calibration/reference;
- Pi resource benchmark;
- broker/TLS/ACL;
- 24–72 jam soak.
