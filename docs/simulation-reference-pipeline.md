# Simulation/Reference Pipeline

Lane ini dipakai untuk CI, contract/regression, dan pengujian metodologi sebelum data real RAB cukup.

## Compact v3 Simulator

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

Simulator menghasilkan:

- `compact_sensor.v3`;
- `processing_profile=hardware_only`;
- firmware/config/calibration provenance berlabel synthetic/unverified;
- per-sensor hardware state;
- boot ID/sequence/time basis;
- bounded seasonality + rise/recovery + autocorrelated-like short dynamics;
- warm-up/dropout/error/sequence gap scenarios;
- CO/CO₂/PM yang tidak meningkat tanpa batas sampai clipping konstan.

Nilai simulator bukan data hardware, bukan calibration, dan tidak boleh menjadi production accuracy evidence.

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

## Output Layer

```text
raw_payloads.jsonl
hardware_observations.jsonl
canonical_observations.jsonl
processed_timeseries.jsonl
windows.jsonl
lstm_windows.jsonl
normalization_report.json
```

Output mempertahankan source-event provenance dan preprocessing version.

## Public Reference Adapters

- UCI Air Quality;
- Bristol BME680;
- Zenodo Fidas PM reference;
- Gary historical/regression.

Setiap adapter menjaga unit, cadence, provenance, missingness, dan domain mismatch. Tidak ada horizontal fabrication antar dataset.

## Forecast Preparation

- cadence diinfer dari timestamp per node atau dideklarasikan lalu divalidasi;
- horizon disimpan sebagai steps dan duration;
- train-only active feature schema/hash;
- constant/near-constant target gate;
- boundary saturation/clipping report;
- time-ordered split + purge/no-overlap;
- baseline validation selection.

## Anomaly Harness

Synthetic event injection hanya menguji plumbing metric dan detector behavior:

```bash
$PY run_gateway.py inject-anomaly-fixture --output data/modeling/anomaly_fixture.jsonl --labels data/modeling/anomaly_labels.json
$PY run_gateway.py stream-detect --backend native --input data/modeling/anomaly_fixture.jsonl --output data/modeling/anomaly_detections.jsonl --feature-names temperature_c,pm25_ug_m3
$PY run_gateway.py benchmark-anomaly-events --detections data/modeling/anomaly_detections.jsonl --labels data/modeling/anomaly_labels.json --output data/modeling/anomaly_benchmark.json
```

Precision/recall/F1 fixture bukan field accuracy claim.

## Boundary

Simulation/reference hanya membuktikan software path dan methodology regression. Ia tidak membuktikan sensor response, calibration, accuracy, LoRa, power, Raspberry Pi resource, MQTT production, atau field behavior.
