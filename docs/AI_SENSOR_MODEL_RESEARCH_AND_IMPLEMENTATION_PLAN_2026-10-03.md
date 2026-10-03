# AI Sensor Model Research, Audit, and Implementation Plan — 3 October 2026

## Scope and Decision Rule

This document refreshes the model strategy for the environmental **AI sensor
gateway** running on Raspberry Pi. It does not cover computer vision,
predictive-maintenance vibration, backend storage, dashboard, or protected
HardProg acquisition/radio code.

The main conclusion is deliberately conservative:

> There is no justified single "best model" for the project yet. The correct
> engineering target is a **lane-specific model portfolio** evaluated with the
> same leakage-safe protocol, followed by Raspberry Pi and field validation.

Current source/tests/runtime evidence remains authoritative over historical
rankings. A model is not promoted because it is newer, larger, pretrained, or
successful on a public benchmark.

## Executive Findings

### What is already strong

- source contract, validation, gateway preprocessing, resampling, feature
  extraction, windowing, and model provenance are separated cleanly;
- temporal train/validation/test splitting uses purge and explicit time ranges;
- baseline selection is performed per target on validation and locked for test;
- data-quality gates reject constant/boundary-saturated targets;
- FITS/DLinear/LSTM training uses safe checkpoints, early stopping, AdamW,
  gradient clipping, and explicit residual strategies;
- live deployment uses checksum-locked lightweight NumPy FITS instead of
  installing the full training stack on the Pi;
- online anomaly scoring is score-before-learn, node-isolated, warm-up gated,
  and restart state is rehydrated in a bounded way;
- decision logic is rules/quality first and can abstain;
- forecast output is currently shadow/informational and does not silently drive
  environmental severity.

### Important audit findings

1. **CO2 historical model cadence mismatch** — already documented separately.
   The 28 August capture is ~0.55 s cadence while live forecast consumes 60 s
   buckets. The old 5-step model therefore did not learn a 5-minute horizon.
2. **FITS/DLinear/FITS-style are target-history-only.** They ignore non-target
   covariates even though the dataset may contain a much richer feature set.
   This is a valid lightweight design, but must not be described as a full
   multivariate covariate model.
3. **LSTM is the current full-feature challenger.** It consumes all active
   features and predicts the configured target set.
4. **`fits_official` is an adaptation/comparator**, not a faithful official
   multi-step FITS reproduction when a direct `t+h` label is paired with
   internal `pred_len=1`.
5. **Live forecast runtime is single-backend/single-manifest.** `LiveEdgeForecaster`
   currently supports the NumPy FITS artifact only and one target node.
6. **Live anomaly is not actually robust-statistics MAD.** `NativeRobustAnomaly`
   uses online Welford mean/variance and a z-score. The name is historical;
   the implementation is a lightweight online standard-score detector.
7. **River HST+ADWIN exists but is not the live backend.** It remains an optional
   research challenger.
8. **Forecast residuals are not currently used by live anomaly detection.**
9. **Forecasted values do not currently drive `env_status`.** This is a safe
   shadow boundary and should remain until calibrated evidence exists.
10. **Meaningful SeasonalNaive may require much longer history than the model
    window.** At 60 s cadence, daily seasonality is 1440 steps; current 12/16
    step model windows cannot represent that baseline directly.
11. **The current single temporal holdout is necessary but not sufficient for
    final model selection.** Rolling-origin evaluation and device/site holdouts
    are needed once real project data is long enough.
12. **Large-lane sample caps can bias multi-node datasets** because collection
    is deterministic and globally capped. Final real-device evaluation should
    use balanced/group-aware sampling or no cap.

## Methodology Defects Closed in This Audit

### MASE denominator

MASE scaling now comes from the **training split only**. The same scale is used
for validation/test metrics and is recorded in metrics provenance. Previously,
edge evaluation could derive the scale from the split being evaluated and LSTM
did not emit MASE despite the documented metric requirement.

### LSTM top-level status

LSTM `status` is now `PASS` only when the baseline + data-quality gate passes.
Finite input data alone no longer allows a top-level `PASS` when the model lost
to the locked baseline.

### Normalization clipping gate

The repeated-seed bake-off runner now evaluates `normalization_report.json`
before training. It blocks a lane if either overall clipping or any observed
feature exceeds the configurable clipping threshold. The default engineering
threshold is 5%, but it is explicit and configurable rather than implicit.

### HardProg feature selection provenance

HardProg forecast feature selection now uses **training data only**. Future
validation/test variation cannot cause a feature to be retained. New NPZ files
also carry a deterministic feature-schema SHA-256 rather than leaving the
checkpoint schema hash null.

### HardProg cadence and sentinel provenance

The builder records invalid sentinel counts, usable points, capture duration,
and observed cadence. It can fail closed against an expected runtime cadence.

## Current Model Audit

| Model / layer | Current implementation | Strength | Current limitation | Current role |
|---|---|---|---|---|
| LastValue | validation/test baseline | hard to beat on smooth short horizons, trivial runtime | no trend/seasonality | mandatory baseline |
| Window mean | baseline | stable/no training | can lag transitions | mandatory baseline |
| Drift | linear extrapolation baseline | tests short trend value | unstable on noise | mandatory baseline |
| SeasonalNaive | conditional baseline | interpretable seasonality | current model windows often too short for real daily seasonality | mandatory when applicable |
| DLinear residual | target-only PyTorch | tiny, fast, interpretable decomposition | ignores cross-sensor covariates | lightweight challenger |
| FITS-inspired | target-only frequency residual | tiny and historically strong on several proxy lanes | depends on sampling/periodicity; current CO2 deployment evidence has cadence mismatch | lightweight challenger |
| FITS official-style | target-only frequency interpolation adaptation | useful research comparison | current direct-label setup is not faithful contiguous multi-step FITS | research comparator |
| LSTM residual | full-feature PyTorch | uses covariates and nonlinear dynamics | stochastic, larger dependency/runtime, historical proxy results mixed | nonlinear challenger |
| Online z-score | Welford statistics | dependency-free and cheap | not robust to contamination; max-z only; no feature attribution output | live fallback anomaly |
| Page-Hinkley-style | per-feature drift | cheap online mean-shift marker | hard-coded detector parameters in live path | live drift marker |
| River HST + ADWIN | optional dependency | true streaming multivariate challenger | not field-benchmarked; not live default | research challenger |
| Rules / quality | deterministic | safest source validity and commissioning protection | not learned | mandatory L0 |

Historical proxy/Pi rankings remain useful for regression only. The old CO2
`PROMISING` label is superseded by the cadence/sentinel audit and must not be
used as 5-minute model-quality evidence.

## Research Landscape and What Is Worth Testing

### Tier A — highest information value / lowest implementation risk

#### NLinear

Add NLinear beside DLinear. It is extremely small and explicitly normalizes
against the last observation to handle distribution shift. It should be a
baseline/challenger before larger architectures.

#### Ridge / ElasticNet lag regression

Use lagged target + selected covariates + calendar/time features. This is a
strong small-data sanity check, easy to interpret and export, and useful for
detecting whether deep models are learning anything beyond linear covariates.

#### TSMixer-lite

This is the **first new trainable multivariate architecture recommended for the
project**. It mixes along time and feature dimensions, directly addressing the
current gap where FITS/DLinear ignore cross-sensor information, while retaining
an all-MLP architecture suitable for small edge-oriented variants.

#### Granite TTM R3

Use initially as a **zero-shot/few-shot benchmark**, not an automatic runtime
replacement. The current Apache-2.0 Granite TTM R3 artifact is only a few MB,
supports multivariate/covariate forecasting, and is explicitly positioned for
CPU-friendly use. It is unusually relevant because project-real data may be
scarce at first.

### Tier B — strong second-wave forecasting challengers

#### TiDE-lite

Dense encoder-decoder with dynamic covariates. Attractive when exogenous sensor
signals materially help a target. Benchmark after TSMixer because it solves a
similar covariate gap with more architecture complexity.

#### N-HiTS

Very good long-horizon hierarchical forecaster and efficient relative to many
Transformer baselines. More compelling after the project has weeks of 60-s
data and multiple horizons; less informative on the current short captures.

#### ModernTCN-lite

Pure convolution is interesting for Pi deployment because convolution has good
runtime tooling and ModernTCN was designed to retain an efficiency advantage.
Prioritize if TSMixer/LSTM show that nonlinear local/multiscale patterns matter.

#### TimeMixer

Multiscale MLP architecture with good short/long forecasting evidence. Worth a
host benchmark, but TSMixer-lite is the simpler first MLP challenger.

#### Koopa

Worth testing on clearly non-stationary lanes. The architecture explicitly
models time-varying dynamics, but deployment complexity is higher than linear
or mixer models.

### Tier C — pretrained / larger research comparators

#### Granite FlowState R1

Particularly relevant to this project because sampling-rate mismatch has already
caused a real evidence defect. FlowState is designed for varying sampling rates
and horizon lengths. Benchmark it as a zero-shot comparator after the canonical
60-s dataset exists; do not use it to avoid fixing the data contract.

#### Chronos-Bolt Tiny

At 9M parameters, Bolt Tiny is a reasonable zero-shot probabilistic reference.
It is still a larger deployment/dependency step than TTM R3 or local models and
should first run in host/Pi shadow benchmarks.

#### PatchTST / iTransformer

Both are strong research baselines for long multivariate sequences, but their
attention-based runtime and added complexity make them lower priority on this
Pi gateway than TSMixer/ModernTCN/TTM.

#### TimesFM 3.0

Useful only as an external research comparator for now. The 3.0 pretrained
weights are currently under a non-commercial/non-production license, and the
family is far larger than the local edge candidates. It must not become the
project deployment dependency under the current licensing/resource profile.

### Not recommended as first-wave work

- large general Transformers solely because they are newer;
- a Mamba/SSM implementation before simpler mixers/TCN prove insufficient;
- bespoke deep autoencoders/USAD/TranAD before simple anomaly baselines and
  real event labels exist;
- any model requiring test-set hyperparameter selection;
- automatic retraining on Pi before data provenance and rollback are mature.

## Anomaly and Drift Research Strategy

### Keep simple baselines first

The 2025 streaming anomaly benchmark literature shows that anomaly type strongly
changes which detector works and that reconstructed static baselines can be very
competitive. Therefore HST must not be treated as the assumed winner.

Recommended comparison set:

1. current online z-score + Page-Hinkley;
2. **rolling median/MAD robust z-score** (new dependency-free baseline);
3. EWMA/CUSUM residual detector;
4. Isolation Forest sliding-window reconstruction;
5. ECOD and COPOD for interpretable multivariate tail anomaly;
6. River HST + ADWIN;
7. xStream or RRCF if streaming evaluation justifies another online method;
8. Granite TSPulse R1 as the compact pretrained challenger;
9. adaptive conformal scoring on forecast residuals/prediction intervals.

TSPulse is attractive because it is small and CPU-friendly, but recent ICLR
2026 work also reports that time-series foundation models can fail to beat very
simple anomaly one-liners under common reconstruction/forecast-error setups.
Therefore TSPulse is a challenger, not a default winner.

## Forecast Task Redesign Before the Next Real Bake-off

### 1. Make horizon semantics explicit

Training and runtime cadence must match. Store both:

```text
source_cadence_seconds
model_input_cadence_seconds
horizon_steps
horizon_duration_seconds
```

No metadata-only relabeling is permitted.

### 2. Move from single direct `t+h` to multi-horizon output

For a 60-s canonical cadence, build contiguous labels such as:

```text
t+1, t+2, t+3, t+4, t+5
```

and optionally operational horizons such as 1, 5, 15, and 30 minutes once data
duration supports them. A model may still be scored at selected horizons, but
the training/evaluation contract must describe exactly what was predicted.

### 3. Add rolling-origin evaluation

Keep a final untouched test period, but use multiple rolling origins in the
development region. Report median/mean/std skill by fold and seed. One lucky
temporal split must not decide the deployment candidate.

### 4. Add group generalization

When multiple physical nodes/sites exist:

- within-device temporal CV;
- leave-device-out;
- leave-site/room-out where possible;
- final newest-time holdout.

Do not pool these into one metric without reporting each domain separately.

### 5. Make real seasonality a separate baseline history

SeasonalNaive should not depend on the neural model window. Maintain enough
history for applicable seasonal periods (for example 1440 points for a daily
period at 60-s cadence) or mark it explicitly inapplicable.

### 6. Add uncertainty

Every model selected for serious shadow testing should provide either:

- native quantiles/distribution; or
- conformal prediction intervals calibrated on held-out past data.

This is more useful for anomaly detection than a naked point forecast.

## Evaluation Protocol — Required for Every Candidate

### Dataset gates

- raw immutable provenance;
- invalid sentinel removal before model fit;
- cadence validation and no silent relabeling;
- resample before window construction;
- train-only imputation/statistics/schema selection;
- normalization clipping gate;
- constant / saturation / target coverage checks;
- no input/label overlap across temporal boundaries;
- device/site split provenance;
- missingness and gap statistics.

### Forecast metrics

- MAE and RMSE in normalized and physical units;
- MASE with denominator fitted on training observations only;
- skill against validation-selected baseline;
- per-target and per-horizon wins;
- fold/seed mean, median, std and worst fold;
- prediction-interval coverage/width when probabilistic;
- calibration error when uncertainty is exposed.

### Anomaly metrics

- point and event precision/recall/F1;
- false alerts/day;
- detection delay;
- warm-up false positives;
- anomaly-type breakdown;
- drift/no-drift breakdown;
- calibration/false-alarm control for conformal scores.

### Resource metrics on the actual Pi

- cold startup time;
- artifact + dependency size;
- p50/p95/p99 inference latency;
- steady and peak RSS;
- CPU utilization;
- temperature/throttling;
- power only when actually measured;
- restart/recovery and state rehydration;
- output parity against the source model after export/quantization.

## Candidate Matrix for the Next Real Dataset

### Forecast wave 1

```text
LastValue
WindowMean
Drift
SeasonalNaive (independent history)
NLinear
DLinear
Ridge / ElasticNet lags
FITS-inspired
TSMixer-lite
LSTM residual
Granite TTM R3 zero-shot
```

Only if wave 1 leaves meaningful unresolved error:

```text
TiDE-lite
N-HiTS
ModernTCN-lite
TimeMixer
FlowState zero-shot
Chronos-Bolt Tiny
```

### Anomaly wave 1

```text
rules / source quality
online z-score
rolling median/MAD
EWMA/CUSUM
IsolationForest window
ECOD/COPOD
River HST + ADWIN
TSPulse R1
forecast-residual conformal score
```

## Lane-specific Recommendation

### Temperature / humidity / pressure

Start with NLinear/DLinear + TSMixer-lite. These signals are smooth enough that
simple baselines may remain very hard to beat. Use LSTM/TCN only if cross-variate
or nonlinear gain is repeatable.

### CO2 / CO / O3 / PM

Treat forecasting and anomaly detection as separate questions. Event-driven
changes may make anomaly/change detection more useful than long-horizon point
forecasting. TSMixer/TTM are interesting only after real calibrated data exists.

### NO2

Keep ordinal/raw-ratio semantics until reference calibration exists. Do not
manufacture ppm targets.

### Battery / current / power

Rules + trend/rate models first. A deep model is unjustified until chemistry,
load profile, and degradation labels exist.

### BME gas resistance/raw lanes

Do not turn a proxy/raw sensor response into a stronger semantic target than the
hardware/calibration supports. It may still be useful as a covariate/anomaly
feature.

## Deployment Architecture Plan

### Phase 0 — methodology closure (current task)

- [x] CO2 cadence/sentinel mismatch identified and manifest downgraded;
- [x] training-only MASE scale;
- [x] LSTM status aligned with baseline gate;
- [x] clipping enforced in repeated-seed bake-off harness;
- [x] HardProg feature selection train-only;
- [x] HardProg feature schema SHA-256;
- [x] full post-change host QA and documentation closure: **132/132 PASS**, compile/config/manifest/diff checks PASS.

### Phase 1 — dataset/evaluator v2

**IMPLEMENTED HOST — field evidence still open.**

- [x] contiguous multi-horizon target arrays;
- [x] leakage-safe rolling-origin folds with actual per-fold training/evaluation;
- [x] strict leave-group-out split that excludes held group from train statistics/schema;
- [x] independent seasonal baseline history;
- [x] physical-unit + normalized metrics and prediction-range diagnostics;
- [x] split-conformal intervals calibrated only on validation;
- [x] exact dataset/model feature schema + SHA linkage;
- [ ] balanced large-lane sampling policy is still dataset-specific and must be
  selected only when a real large multi-node capture requires a cap.

### Phase 2 — low-cost new models

**IMPLEMENTED HOST.**

- [x] NLinear;
- [x] dependency-light Ridge/ElasticNet lag regression;
- [x] TSMixer-lite multivariate challenger;
- [x] common evaluator/metrics/baseline contract;
- [x] repeated-seed TSMixer and development rolling-origin harness;
- [x] final holdout is opt-in after candidate lock, not default bake-off input.

### Phase 3 — pretrained comparators

**IMPLEMENTED AS OPTIONAL HOST RESEARCH ADAPTERS; model packages/weights are not
installed into the gateway runtime.**

- [x] Granite TTM R3 zero-shot adapter, local-cache-first/download opt-in;
- [x] Chronos-Bolt Tiny target-history comparator + native quantile interval;
- [x] FlowState R1 adapter with explicit scale-factor requirement and context gate;
- [x] fail-closed research catalog/license boundary;
- [ ] few-shot foundation-model training remains intentionally deferred until
  project-real data is long enough to justify it.

All downloads/models remain optional research dependencies and may not enter the
gateway release merely because host evaluation succeeds.

### Phase 4 — anomaly v2

**IMPLEMENTED HOST HARNESS; real labeled threshold calibration remains open.**

- [x] explicit `NativeOnlineZScoreAnomaly` semantic alias for historical z-score;
- [x] rolling MAD + per-feature attribution;
- [x] EWMA/CUSUM comparator;
- [x] persistence/hysteresis/debounce;
- [x] event + point precision/recall/F1, false-alert/day, delay, warm-up FP,
  and per-event-kind breakdown;
- [x] IsolationForest static research harness on separate fit/eval sources;
- [x] ECOD/COPOD optional adapters with actionable dependency gate;
- [x] TSPulse catalog/context/dependency preflight;
- [x] forecast split-conformal uncertainty available for residual-based follow-up;
- [ ] threshold calibration on project-real labeled events remains field-data blocked.

### Phase 5 — model runtime abstraction

Replace one hard-coded FITS runtime with a manifest-driven registry:

```text
model manifest
  -> target node/lane
  -> ordered inputs + schema hash
  -> cadence + horizons
  -> runtime backend
       numpy
       onnxruntime
       optional torch research
  -> artifact hash
  -> readiness/evidence
```

Prefer NumPy or ONNX Runtime for deployable local models. PyTorch/granite-tsfm
can remain an optional research runtime until Pi resource measurements justify
shipping it.

**IMPLEMENTED for local v2 candidates:** checksum-locked manifest v2 + registry,
NumPy Ridge/ElasticNet/NLinear, optional Torch TSMixer shadow runtime, v1/v2
manifest dispatch, cadence/missing/schema fail-closed gates, and source/runtime
parity test. ONNX remains an optional future backend rather than an unnecessary
dependency today.

### Phase 6 — Raspberry Pi shadow bake-off

For each surviving candidate:

1. export and parity test on host;
2. package without changing protected HardProg;
3. bounded Pi preflight;
4. shadow inference only;
5. latency/RSS/CPU/thermal/size measurements;
6. reboot/rehydration test;
7. rollback proof;
8. long soak only after short acceptance passes.

**SOFTWARE HARNESS IMPLEMENTED; CURRENT HARDWARE BLOCKED.**

`benchmark-forecast-runtime-v2` measures cold start, p50/p95/p99 latency,
throughput, process RSS, CPU-time ratio, thermal best-effort, artifact/dependency
footprint, and source/runtime parity while labelling the actual host. Probe on
3 October found `iiotgw` / `100.93.215.11` offline in Tailscale (last seen about
21 h), Tailscale/ICMP ping timeout, and read-only SSH unavailable. Therefore no
new Pi resource, reboot, rehydration, or soak evidence is claimed in this task.

### Phase 7 — field model promotion

Promotion requires project-real data, repeated temporal + device/site evidence,
Pi resource evidence, stable calibration, and field soak. `PROMISING` remains a
lane-specific research label; `PRODUCTION` is a separate deployment/operations
gate.

**BLOCKED BY EVIDENCE, intentionally not forced.** No current project-real
days-to-weeks 60-s capture with independent nodes/rooms + labels exists in this
workstream, and current Pi is unreachable. No model is promoted to production.

## Data Collection Guidance

Do not use a fixed number of rows as a universal quality guarantee. Required
duration depends on cadence, seasonality, horizon, number of folds, and devices.
For 60-s environmental data:

- 21 minutes is only the mathematical minimum for one `window=16, horizon=5`
  sample and has no model-selection value;
- at least one full daily cycle requires 1440 observations;
- meaningful rolling-origin + daily seasonality needs multiple complete cycles;
- cross-device/site validation requires physically independent nodes/sites,
  not duplicated windows from one capture.

The practical target should therefore be **days to weeks of clean continuous
data per physical node**, then expand to multiple nodes/rooms before any field
accuracy claim.

## External Research Reviewed

Primary/reference material reviewed in this refresh includes:

- DLinear/NLinear — Zeng et al., AAAI 2023;
- TSMixer — Google Research / TMLR 2023;
- TiDE — Google Research / TMLR 2023;
- N-HiTS — Challu et al., AAAI 2023;
- PatchTST — ICLR 2023;
- TimeMixer, iTransformer, ModernTCN — ICLR 2024;
- Koopa — NeurIPS 2023;
- Granite TTM R3, Granite TSPulse R1, FlowState — IBM 2026 ecosystem;
- Chronos/Chronos-Bolt — Amazon Science;
- TimesFM 3.0 — Google Research, including the current weight-license boundary;
- 2025 streaming anomaly detection systematic benchmark;
- COPOD / ECOD interpretable outlier baselines;
- adaptive conformal anomaly detection with TS foundation models — ICLR 2026;
- rolling-origin evaluation and training-denominator MASE guidance from
  Forecasting: Principles and Practice / Hyndman.

Useful source URLs:

- https://ojs.aaai.org/index.php/AAAI/article/view/26317
- https://research.google/pubs/tsmixer-an-all-mlp-architecture-for-time-series-forecasting/
- https://research.google/pubs/long-horizon-forecasting-with-tide-time-series-dense-encoder/
- https://ojs.aaai.org/index.php/AAAI/article/view/25854
- https://proceedings.iclr.cc/paper_files/paper/2024/hash/a7ac8a21e5a27e7ab31a5f42a0117bdb-Abstract-Conference.html
- https://proceedings.iclr.cc/paper_files/paper/2024/hash/86b1437c1e4c3b3c4debff98234a67e7-Abstract-Conference.html
- https://proceedings.neurips.cc/paper_files/paper/2023/hash/28b3dc0970fa4624a63278a4268de997-Abstract-Conference.html
- https://huggingface.co/ibm-granite/granite-timeseries-ttm-r3
- https://huggingface.co/ibm-granite/granite-timeseries-tspulse-r1
- https://research.ibm.com/publications/flowstate-sampling-rate-equivariant-time-series-forecasting
- https://github.com/amazon-science/chronos-forecasting
- https://link.springer.com/article/10.1007/s10462-024-10995-w
- https://research.ibm.com/publications/adaptive-conformal-anomaly-detection-with-time-series-foundation-models-for-signal-monitoring
- https://otexts.robjhyndman.com/fpp3/tscv.html
- https://robjhyndman.com/hyndsight/rolling_mase.html

## Final Engineering Position

The most useful next experiment is **not** "replace FITS with the newest model".
It is:

```text
correct real 60-s dataset
-> rolling/group evaluation
-> stronger simple baselines (NLinear/Ridge)
-> TSMixer-lite multivariate challenger
-> existing FITS/DLinear/LSTM
-> Granite TTM R3 zero-shot comparator
-> same evaluator + same folds + same resource protocol
```

For anomaly detection:

```text
rules + robust simple detector
-> streaming/static baselines
-> TSPulse/conformal challengers
-> false-alert/day + event-delay evidence
```

Only measured project-real evidence should decide the eventual deployment model.
