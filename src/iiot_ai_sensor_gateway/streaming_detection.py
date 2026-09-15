from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

STREAM_SCHEMA = "iiot.ai_sensor.streaming_detection.v1"


class RiverDependencyError(RuntimeError):
    """Raised when River-backed streaming detection is requested but unavailable."""


class AnomalyModel(Protocol):
    def score_one(self, features: dict[str, float]) -> float: ...

    def learn_one(self, features: dict[str, float]) -> Any: ...


class DriftDetector(Protocol):
    drift_detected: bool

    def update(self, value: float) -> Any: ...


class NativeRobustAnomaly:
    """Dependency-free online z-score ensemble for a deployable fallback lane.

    It scores before learning the current sample, keeps one Welford accumulator
    per feature, and maps the largest absolute z-score to [0, 1]. It is not
    labelled as Half-Space Trees and remains an experimental comparator.
    """

    def __init__(self, *, z_scale: float = 3.0) -> None:
        if z_scale <= 0:
            raise ValueError("z_scale must be positive")
        self.z_scale = z_scale
        self._stats: dict[str, list[float]] = {}

    def score_one(self, features: dict[str, float]) -> float:
        max_z = 0.0
        for name, value in features.items():
            count, mean, m2 = self._stats.get(name, [0.0, 0.0, 0.0])
            if count < 2 or m2 <= 0:
                continue
            variance = m2 / (count - 1.0)
            if variance > 0:
                max_z = max(max_z, abs(value - mean) / math.sqrt(variance))
        return 1.0 - math.exp(-max_z / self.z_scale)

    def learn_one(self, features: dict[str, float]) -> NativeRobustAnomaly:
        for name, value in features.items():
            count, mean, m2 = self._stats.get(name, [0.0, 0.0, 0.0])
            count += 1.0
            delta = value - mean
            mean += delta / count
            m2 += delta * (value - mean)
            self._stats[name] = [count, mean, m2]
        return self


class NativePageHinkley:
    """Small dependency-free Page-Hinkley-style mean-shift detector."""

    def __init__(self, *, delta: float = 0.005, threshold: float = 0.25) -> None:
        if delta < 0 or threshold <= 0:
            raise ValueError("Page-Hinkley delta must be >= 0 and threshold must be positive")
        self.delta = delta
        self.threshold = threshold
        self.drift_detected = False
        self._count = 0
        self._mean = 0.0
        self._cumulative = 0.0
        self._minimum = 0.0

    def update(self, value: float) -> NativePageHinkley:
        self._count += 1
        self._mean += (value - self._mean) / self._count
        self._cumulative += value - self._mean - self.delta
        self._minimum = min(self._minimum, self._cumulative)
        self.drift_detected = self._count >= 8 and (
            self._cumulative - self._minimum > self.threshold
        )
        if self.drift_detected:
            self._count = 0
            self._mean = 0.0
            self._cumulative = 0.0
            self._minimum = 0.0
        return self


@dataclass(frozen=True)
class StreamingConfig:
    warmup_samples: int = 64
    anomaly_threshold: float = 0.7
    learn_after_score: bool = True

    def validate(self) -> None:
        if self.warmup_samples < 1:
            raise ValueError("warmup_samples must be >= 1")
        if not 0.0 <= self.anomaly_threshold <= 1.0:
            raise ValueError("anomaly_threshold must be in [0, 1]")


class StreamingDetectionPipeline:
    """Online anomaly + per-feature drift orchestration independent of River imports."""

    def __init__(
        self,
        anomaly_model: AnomalyModel,
        drift_detectors: dict[str, DriftDetector],
        config: StreamingConfig | None = None,
    ) -> None:
        self.anomaly_model = anomaly_model
        self.drift_detectors = drift_detectors
        self.config = config or StreamingConfig()
        self.config.validate()
        self.seen = 0

    @staticmethod
    def _validate_features(features: dict[str, Any]) -> dict[str, float]:
        if not isinstance(features, dict) or not features:
            raise ValueError("streaming features must be a non-empty object")
        normalized: dict[str, float] = {}
        for name, value in features.items():
            if isinstance(value, bool):
                raise ValueError(f"feature {name!r} must be numeric")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"feature {name!r} must be finite")
            if not 0.0 <= number <= 1.0:
                raise ValueError(
                    f"feature {name!r} must be normalized to [0, 1] before Half-Space Trees"
                )
            normalized[str(name)] = number
        return normalized

    def process(
        self,
        features: dict[str, Any],
        *,
        timestamp: str | None = None,
        node_timestamp_ms: float | int | None = None,
        timestamp_basis: str | None = None,
    ) -> dict[str, Any]:
        values = self._validate_features(features)
        raw_score = float(self.anomaly_model.score_one(values))
        if not math.isfinite(raw_score):
            raise ValueError("anomaly model returned a non-finite score")
        anomaly_score = max(0.0, min(1.0, raw_score))
        if self.config.learn_after_score:
            self.anomaly_model.learn_one(values)
        self.seen += 1
        drift_features: list[str] = []
        for name, value in values.items():
            detector = self.drift_detectors.get(name)
            if detector is None:
                continue
            detector.update(value)
            if bool(detector.drift_detected):
                drift_features.append(name)
        warmed_up = self.seen >= self.config.warmup_samples
        is_anomaly = warmed_up and anomaly_score >= self.config.anomaly_threshold
        receive_timestamp = datetime.now(tz=UTC).isoformat()
        result = {
            "schema": STREAM_SCHEMA,
            # ``timestamp`` is an event timestamp only when the input supplied
            # one. For uptime-only replays it is the gateway processing time;
            # the original node clock is carried separately below.
            "timestamp": timestamp or receive_timestamp,
            "receive_timestamp": receive_timestamp,
            "timestamp_basis": "rfc3339" if timestamp else "gateway_receive",
            "sample_index": self.seen,
            "warmup_complete": warmed_up,
            "anomaly_score": anomaly_score,
            "is_anomaly": is_anomaly,
            "drift_detected": bool(drift_features),
            "drift_features": sorted(drift_features),
            "decision": "anomaly"
            if is_anomaly
            else ("warmup" if not warmed_up else ("drift" if drift_features else "normal")),
        }
        if node_timestamp_ms is not None:
            result["node_timestamp_ms"] = node_timestamp_ms
            result["node_timestamp_basis"] = timestamp_basis or "uptime_ms"
        return result


def build_native_pipeline(
    *,
    feature_names: tuple[str, ...],
    warmup_samples: int = 64,
    anomaly_threshold: float = 0.7,
    z_scale: float = 3.0,
    page_hinkley_delta: float = 0.005,
    page_hinkley_threshold: float = 0.25,
) -> StreamingDetectionPipeline:
    if not feature_names:
        raise ValueError("feature_names must not be empty")
    model = NativeRobustAnomaly(z_scale=z_scale)
    detectors = {
        name: NativePageHinkley(
            delta=page_hinkley_delta,
            threshold=page_hinkley_threshold,
        )
        for name in feature_names
    }
    return StreamingDetectionPipeline(
        model,
        detectors,
        StreamingConfig(warmup_samples=warmup_samples, anomaly_threshold=anomaly_threshold),
    )


def build_river_pipeline(
    *,
    feature_names: tuple[str, ...],
    warmup_samples: int = 64,
    anomaly_threshold: float = 0.7,
    n_trees: int = 25,
    height: int = 8,
    window_size: int = 250,
    seed: int = 42,
    adwin_delta: float = 0.002,
) -> StreamingDetectionPipeline:
    try:
        from river import anomaly, drift
    except ModuleNotFoundError as exc:
        raise RiverDependencyError(
            "River is optional and not installed. Install command: python -m pip install '.[streaming]'"
        ) from exc
    if not feature_names:
        raise ValueError("feature_names must not be empty")
    if n_trees < 1 or height < 1 or window_size < 2:
        raise ValueError("n_trees, height, and window_size must be positive")
    model = anomaly.HalfSpaceTrees(
        n_trees=n_trees,
        height=height,
        window_size=window_size,
        seed=seed,
    )
    detectors = {name: drift.ADWIN(delta=adwin_delta) for name in feature_names}
    return StreamingDetectionPipeline(
        model,
        detectors,
        StreamingConfig(warmup_samples=warmup_samples, anomaly_threshold=anomaly_threshold),
    )


def run_streaming_detection(
    input_jsonl: str | Path,
    output_jsonl: str | Path,
    *,
    feature_names: tuple[str, ...],
    backend: str = "native",
    warmup_samples: int = 64,
    anomaly_threshold: float = 0.7,
    n_trees: int = 25,
    height: int = 8,
    window_size: int = 250,
    seed: int = 42,
    adwin_delta: float = 0.002,
    z_scale: float = 3.0,
    page_hinkley_delta: float = 0.005,
    page_hinkley_threshold: float = 0.25,
) -> dict[str, Any]:
    if backend == "river":
        pipeline = build_river_pipeline(
            feature_names=feature_names,
            warmup_samples=warmup_samples,
            anomaly_threshold=anomaly_threshold,
            n_trees=n_trees,
            height=height,
            window_size=window_size,
            seed=seed,
            adwin_delta=adwin_delta,
        )
        backend_name = "river.HalfSpaceTrees+ADWIN"
    elif backend == "native":
        pipeline = build_native_pipeline(
            feature_names=feature_names,
            warmup_samples=warmup_samples,
            anomaly_threshold=anomaly_threshold,
            z_scale=z_scale,
            page_hinkley_delta=page_hinkley_delta,
            page_hinkley_threshold=page_hinkley_threshold,
        )
        backend_name = "native.RobustZScore+PageHinkley"
    else:
        raise ValueError("backend must be 'native' or 'river'")
    output = Path(output_jsonl)
    output.parent.mkdir(parents=True, exist_ok=True)
    processed = rejected = anomalies = drift_events = 0
    with Path(input_jsonl).open(encoding="utf-8") as source, output.open("w", encoding="utf-8") as target:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                feature_source = record.get("features", record.get("values", record))
                selected = {name: feature_source[name] for name in feature_names}
                result = pipeline.process(
                    selected,
                    timestamp=record.get("timestamp"),
                    node_timestamp_ms=record.get("node_timestamp_ms"),
                    timestamp_basis=record.get("timestamp_basis"),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                rejected += 1
                target.write(
                    json.dumps(
                        {
                            "schema": STREAM_SCHEMA,
                            "line_number": line_number,
                            "decision": "rejected",
                            "error": str(exc),
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                continue
            processed += 1
            anomalies += int(result["is_anomaly"])
            drift_events += int(result["drift_detected"])
            target.write(json.dumps(result, separators=(",", ":")) + "\n")
    summary = {
        "schema": "iiot.ai_sensor.streaming_detection_run.v1",
        "input": str(input_jsonl),
        "output": str(output),
        "processed": processed,
        "rejected": rejected,
        "anomalies": anomalies,
        "drift_events": drift_events,
        "feature_names": list(feature_names),
        "backend": backend_name,
        "status": "EXPERIMENTAL",
    }
    output.with_suffix(output.suffix + ".meta.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary
