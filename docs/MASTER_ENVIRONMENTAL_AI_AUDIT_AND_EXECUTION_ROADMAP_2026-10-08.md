# Environmental AI — audit evidence dan master execution roadmap (8 Oktober 2026)

**Authority untuk pekerjaan *berikutnya*; bukan klaim bahwa target sudah terimplementasi.** Scope: forecasting, anomaly/drift environmental, ESP32-C6 dan gateway. Sensor health/RUL serta vibration PM berada pada repo masing-masing; vision paused. `sensor_ai.v2` tetap kontrak live canonical; leak detection hanya evaluation plan.

## Audit snapshot dan batas bukti

- Git saat audit dimulai: `main`, clean, sinkron dengan `origin/main`, HEAD `aa9c6d6`.
- Host: `PYTHONPATH=src` + Hermes Python menjalankan 164 unittest OK; compileall dan check-config default OK. Ini bukan pengujian fisik ESP32-C6 maupun benchmark Pi.
- CRG snapshot saat audit: 740 nodes, 8423 edges, 94 files, indexed commit `cad0a494` (17 September): **stale**; zero impact result untuk evaluator bukan bukti tidak ada dependency. Graphify cross-repo membantu navigasi tetapi bukan authority hasil test.
- Model ranking Gary/UCI/Fidas/simulator, termasuk FITS dan DLinear, adalah **historical/reference-only**. Jangan mengubah angka tersebut menjadi claim 60-s project-real tanpa capture baru. CO₂ ~0,55 s historical versus 60 s runtime tidak boleh disamakan; horizon 5 langkah mengikuti cadence aktual, bukan otomatis 5 menit. Sentinel `-1` bukan nilai gas fisik maupun imputasi normal.
- Status live berdasarkan dokumentasi sebelumnya, **tidak diverifikasi ulang dari Pi/broker di audit host ini**. Node 1 dapat membawa dummy HardProg values sekalipun RF live. Node 2 `>BhHH` 7 byte berdasarkan source 5 Oktober, menunggu korelasi payload fisik. Jangan mengubah protected services.

## Capability inventory

| Capability | Status | Bukti/gap yang membatasi | Next gate |
|---|---|---|---|
| Firmware mock/v3 & driver hardware | PARTIAL | Host source dan build profile; pin/rail/sensor/LoRa fisik belum tervalidasi di audit ini | Board-level checks + logged sample/CRC |
| Compact v1/v2/v3 parser & real receiver | DONE software / PARTIAL field | Schemas/tests tersedia; diversity upstream source dan field replay perlu diverifikasi | Golden payload fixtures dari tiap source |
| ChirpStack → `sensor_ai.v2` MQTT outbox | DONE existing implementation / PARTIAL current verification | Dokumentasi live historical; current independent read-only end-to-end check belum dilakukan | Pi + MQTT + Prometheus correlated readback |
| Preprocessing, normalization, resampling/window | DONE host harness / PARTIAL project-real | 164-test host suite; consistency terhadap capture sensor nyata masih open | Golden offline vs shadow parity |
| Dataset catalog/adapters + provenance | PARTIAL | Reference data bukan chip-identical; long-duration project-real unlabeled/labelled capture belum cukup | Audit license/hash/unit/site + capture manifest |
| Forecast baselines, linear, DL, pretrained adapters | DONE research harness / PARTIAL quality | No fair same-dataset project-real promotion evidence | Frozen folds, train-only transforms, baseline-first bake-off |
| Streaming/static anomaly, drift, hysteresis | DONE host harness / PARTIAL quality | Labeled event precision/false-alert/day/delay belum dibuktikan field | Controlled positives and negative controls |
| Model registry, manifest, checksums | DONE host harness / PARTIAL target | Artifact/runtime parity dapat dites; Pi long-soak/rollback belum dibuktikan baru | Target shadow/parity/resource test |
| Field model promotion | BLOCKED | Hardware/source diversity, ground truth, soak dan target evidence | Separate approval and field acceptance |

## Frozen reference E2E dan negative path

`source (mock/fixture/replay/ChirpStack) → durable raw → versioned parse/validation (reject invalid contract, duplicate/late, wrong time basis) → node+boot scoped preprocessing → 60-s resampling with missing mask → train-consistent features/normalizer → contiguous window → baseline and candidate forecast → rule/stream anomaly and drift → decision/abstain/hysteresis → canonical sensor_ai.v2 for ChirpStack (v1 compact-only) → SQLite outbox QoS1 MQTT → consumer schema/Prometheus`.

Offline reference validation must inject: corrupt checksums; unknown versions; invalid physical units; `null` vs zero and historical `-1`; wrong cadences; out-of-order/duplicate across reboot; missing buckets; stale observations; insufficient warm-up; changed schema/manifest/checksum; model unavailable; MQTT disconnect/replay; retained stale status. **Expected:** loss/invalid data causes rejection or explicit abstention, not a confident `normal` forecast. Compare event IDs and output against golden fixtures; never connect replay to production broker.

## Sequenced work packages (do not execute automatically)

| Order | Package / status | Acceptance, artifacts and stop condition |
|---|---|---|
| G0 | Methodology/evidence audit — PARTIAL | Inventory SHA/path/units/cadence/holdouts of existing artifact; quarantine incompatible CO₂ rankings. Publish `evidence_ledger.csv` referencing real local artifacts, no synthesized metrics; fail on missing metadata. |
| G1 | Source + dataset v3 quality — PLANNED | Persist receive and event timestamps/time quality, actual sample cadence, missingness masks, device/site & sensor model IDs; train-only fit. Deliver immutable capture manifests and source-to-canonical golden fixtures. Stop if units/permission/license uncertain. |
| G2 | Fair forecast bake-off — PLANNED | Pre-register targets/horizons in seconds and splits; same contiguous targets/folds for LastValue/Mean/Drift/SeasonalNaive, Ridge/ElasticNet, DLinear/NLinear, FITS-inspired, TSMixer-lite, LSTM. Rolling-origin and disjoint node/site holdout; purge overlapping labels/window contexts; validation-only tuning, one locked final test. Report MAE/RMSE/MASE, skill, physical-unit inverse transform, per-horizon coverage, seed variance and clipping. If a baseline wins, **retain baseline**. |
| G3 | Uncertainty/pretrained comparison — PLANNED | Calibrate interval on validation/calibration block only; report nominal vs empirical coverage/width and drift of coverage. Granite TTM/Chronos-Bolt/FlowState research-only same folds; log memory/latency/license and abstain on unavailable dependencies. No automatic install/promotion. |
| G4 | Environmental anomaly & drift quality — PLANNED | Same event definitions, labels, warm-up, thresholds and debouncing across rule/RobustZScore/Page-Hinkley/EWMA/CUSUM/IF/ECOD/COPOD/River challengers. Report event precision/recall/F1, false alerts per *observed day*, detection delay, negative-control false positives, confidence/abstention. No physical leak assertion from residual alone. |
| G5 | Software shadow parity — PLANNED | Replay project-real source to side-effect-free local broker/fixture, compare exact ordering/schema/event identity, duplicate after restart, thresholds and decisions. All tests must be deterministic on pinned artifacts; rollback must restore prior manifest. |
| G6 | Pi 5 bounded shadow — BLOCKED until target authorized/available | Signed artifact/hash + runtime/feature/cadence parity; cold/warm p50/p95/p99, peak RSS, CPU, thermal, dropouts, reboot, soak and rollback. Report actual Pi firmware/image and host details; stop on resource budget or deviation. Never restart production services for the audit. |
| G7 | Project-real field promotion — BLOCKED | Independent multi-day multi-node data, calibrated event labels, site holdout, signed off threshold and false-alert budget, shadow soak, roll-back drill, operational owner approval. Separate deployment approval required. |

G0–G5 can be completed mostly using fixture/reference data and local replay, but **project-real model-quality acceptance** of G1–G4 needs additional real capture. G6/G7 cannot be declared DONE from host-only measurements.

## Dataset and controlled gas-event priorities

First collect physical-node continuous 60-s observations and raw receive metadata (days-to-weeks, multiple site/node), including failures and gaps. Next acquire paired independent reference instruments and verified units/calibration; then controlled release/rise plus ventilation, occupancy, airflow and benign negative events under approved lab safety protocol. No uncontrolled hazardous release or public leak alarms. Compare simple persistence/change-point/rules to forecast residuals before considering a dedicated classifier. Public UCI/Bristol/Fidas/Gary remain external transfer/compatibility lanes, not proof of real RAB chip accuracy.

## Research decisions and exclusions

- **Keep:** LastValue and simple statistical baselines, validation-only locked baseline selection, lightweight Ridge/NLinear, current FITS/DLinear and local anomaly comparator harnesses.
- **Try only on matched folds:** multivariate covariates, NLinear/TSMixer-lite, pretrained TTM/Chronos/FlowState, robust/conformal intervals, drift adaptive thresholds. Quantile availability alone does not demonstrate calibrated coverage.
- **Defer:** long-context large neural tuning, pretrained production deployment, new leak subsystem, new backends, new hardware drivers without board evidence, security/infrastructure redesign. Stop challengers failing baseline, resource, data, or licensing gates.
- External methods: rolling-origin requires future observations never enter training (<https://otexts.com/fpp3/tscv.html>); Chronos-Bolt supports quantile forecast outputs (<https://github.com/amazon-science/chronos-forecasting>); River ADWIN is a change detector, not a leak classifier (<https://github.com/online-ml/river/blob/main/river/drift/adwin.py>).

## Audit follow-up and evidence hygiene

This is a **bounded fresh host audit**, not certification of every firmware path, training artifact, local dataset or live service. Outstanding: complete source-by-source audit of firmware and model runtime, fresh firmware mock/hardware builds, end-to-end synthetic replay, device/site benchmark artifact inspection, real source readback and target Pi QA. Mark these explicitly OPEN; do not infer pass from historical docs. Test command must include `PYTHONPATH=src` unless package installed editable. Audit code changes and repeat regression before any promotion; do not use final test for tuning.
