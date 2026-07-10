# Simulation/Reference Pipeline

Lane ini dipakai untuk CI, regression, contract testing, dan metodologi model sebelum data real cukup.

## Compact v2 Simulator

```bash
PY=/home/ubuntu/.hermes/hermes-agent/venv/bin/python3
$PY run_gateway.py simulate \
  --scenario mixed \
  --count 120 \
  --nodes 2 \
  --interval-sec 60 \
  --output data/simulated/payloads.jsonl

$PY run_gateway.py run \
  --input-file data/simulated/payloads.jsonl \
  --output-dir data/processed
```

Simulator menghasilkan seluruh field RAB, per-sensor status, boot ID, sequence, dan compact v2. Nilai simulator bukan data hardware.

## Scenarios

```text
normal
air_rise
battery_drop
missing_data
sensor_error
node_silent
sequence_gap
mixed
```

## Public Reference Adapters

- UCI Air Quality;
- Bristol BME680;
- Zenodo Fidas PM reference;
- Gary historical/regression.

Setiap adapter menjaga unit dan missing. Tidak ada horizontal fabrication antar dataset.

## Output

Pipeline menghasilkan accepted/rejected records, resampled points, feature vectors, normalized vectors, dan windows. Modeling artifacts masuk `data/modeling`/`models`, bukan Git.

## Boundary

Hasil simulation/reference hanya membuktikan software path. Ia tidak membuktikan calibration, accuracy, battery, radio, atau kondisi lapangan.
