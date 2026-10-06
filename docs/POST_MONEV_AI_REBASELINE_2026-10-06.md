# Post-Monev AI Re-baseline — Sensor Gateway — 6 Oktober 2026

## Keputusan

Setelah Monthly Evaluation, ownership dipisah lebih tegas:

1. repo ini tetap memiliki **environmental forecasting + anomaly monitoring**;
2. **Sensor Health & Lifetime Prognostics** dipindahkan ke repo sibling `iiot-sensor-health-ai`;
3. permintaan leak detection/prediction dari mitra tetap menjadi plan evaluasi pada workstream environmental existing.

Motor predictive maintenance tetap berada di repo terpisah `iiot-predictive-maintenance-ai`. Computer vision tidak dipindahkan ke repo ini dan berstatus paused.

## Sensor Health / Lifetime

**Moved:** source/schema/test/reference runner sekarang berada di
`../iiot-sensor-health-ai`. Repo environmental hanya menjadi salah satu future
telemetry/provenance source untuk lane sensor-health.

## Environmental Monitoring Existing / Leak Detection Plan

Tidak dibuat subsystem baru untuk leak detection. Lane ini tetap memakai source,
contract, forecasting, anomaly/drift, simulation/reference pipeline, dan event
benchmark yang sudah ada di repo. Requirement dari mitra ditangani sebagai plan:

1. kumpulkan controlled labeled gas/release event dan negative controls;
2. evaluasi apakah anomaly/change detection existing sudah cukup;
3. bandingkan threshold/change-point/classifier hanya jika evidence menunjukkan kebutuhan;
4. forecasting tetap dipakai sebagai trajectory/early-warning support, bukan otomatis physical-leak confirmation.

## Reference E2E

Environmental lane tetap divalidasi melalui `docs/simulation-reference-pipeline.md`.
Sensor-health memiliki runner dan QA independen di repo barunya.

## Data berikutnya yang paling bernilai

Sensor-health membutuhkan longitudinal unit-level history, calibration/reference residual, exposure, failures, maintenance dan replacement reason. Environmental leak-detection plan membutuhkan controlled labeled rising/release events, normal environmental variation, airflow/context, dan negative controls sebelum memutuskan apakah detector dedicated memang perlu. Tanpa data tersebut, menaikkan kompleksitas model tidak lebih penting daripada memperbaiki ground truth.

Authority lintas-project: `../../Project Context/MONEV_2026/23_POST_MONEV_AI_SCOPE_REBASELINE_E2E_PLAN_2026-10-06.md`.

