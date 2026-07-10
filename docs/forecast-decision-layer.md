# Forecast dan Decision Layer

Decision layer tidak bergantung pada satu model. Urutan evaluasi:

```text
source/quality validity
-> sensor rules
-> battery/node rules
-> anomaly/drift
-> forecast candidate
-> output atau abstain
```

## Output `sensor_decision.v1`

```json
{
  "schema_version": "sensor_decision.v1",
  "env_status": "warning",
  "main_factor": "co2_ppm",
  "battery_status": "normal",
  "node_health": "healthy",
  "confidence": 0.8,
  "abstain": false,
  "reason": "co2_ppm crossed project commissioning threshold",
  "forecast_status": "available",
  "model_readiness": "EXPERIMENTAL",
  "no2_semantics": "ordinal_ratio_only"
}
```

## Abstain

Data berikut menghasilkan `env_status=unknown` dan `abstain=true`:

- invalid;
- stale;
- offline;
- all-sensor failure;
- missing identity/contract;
- model/checkpoint invalid bila model dibutuhkan.

## Rules

Default code berisi commissioning threshold untuk development/test. Nilai tersebut bukan batas regulasi dan harus dipindahkan ke deployment config setelah review domain.

Field rules saat ini:

- temperatur;
- kelembapan;
- CO;
- O₃;
- CO₂;
- PM2.5;
- battery voltage;
- NO₂ ratio ordinal.

## Anomaly dan Drift

- anomaly score dapat menaikkan warning/critical;
- drift menghasilkan warning/model-drift factor;
- drift bukan bukti lingkungan berbahaya;
- warm-up streaming model harus selesai sebelum alert.

## Confidence

Confidence diturunkan ketika:

- banyak field missing;
- quality partial/degraded;
- model belum promising/validated.

Confidence bukan probabilitas terkalibrasi sebelum calibration study dilakukan.

## Main Factor

Priority risk factor mengikuti canonical field. Legacy proxy tidak digunakan sebagai production factor.

## Integrasi

Decision output dapat dipetakan ke `sensor_ai.v1.ai`, tetapi event builder default tetap abstain sampai decision aktual disediakan.
