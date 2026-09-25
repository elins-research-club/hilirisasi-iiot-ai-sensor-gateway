from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .contracts import FeatureVector


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LiveEdgeForecaster:
    """Fail-closed NumPy runtime for the promoted FITS edge artifact.

    Training/evaluation still uses the original PyTorch checkpoint. Deployment
    uses an exported, checksummed linear-head artifact so a CPU Raspberry Pi
    does not need PyTorch or any CUDA dependency.
    """

    def __init__(
        self,
        manifest_path: str | Path,
        *,
        target_node_id: str = "",
        cadence_tolerance_fraction: float = 0.20,
        device: str = "cpu",
    ) -> None:
        del device  # kept for backwards call compatibility; runtime is NumPy/CPU only.
        self.manifest_path = Path(manifest_path)
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"forecast manifest not found: {self.manifest_path}")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("schema") != "iiot.ai_sensor.model_manifest.v1":
            raise ValueError("unsupported forecast manifest schema")
        artifact_ref = Path(str(self.manifest["runtime_artifact_path"]))
        if not artifact_ref.is_absolute():
            artifact_ref = (self.manifest_path.parent.parent.parent / artifact_ref).resolve()
        self.runtime_artifact_path = artifact_ref
        expected_sha = str(self.manifest.get("runtime_artifact_sha256", "")).lower()
        if not expected_sha or _sha256(self.runtime_artifact_path) != expected_sha:
            raise ValueError("forecast runtime artifact SHA-256 mismatch")
        self.runtime_artifact = json.loads(
            self.runtime_artifact_path.read_text(encoding="utf-8")
        )
        if (
            self.runtime_artifact.get("schema")
            != "iiot.ai_sensor.fits_numpy_runtime.v1"
        ):
            raise ValueError("unsupported forecast runtime artifact schema")
        if self.runtime_artifact.get("model_type") != "fits":
            raise ValueError("only FITS NumPy runtime artifact is supported")
        if (
            self.runtime_artifact.get("source_checkpoint_sha256")
            != self.manifest.get("source_checkpoint_sha256")
        ):
            raise ValueError("runtime artifact source checkpoint provenance mismatch")
        self.target_node_id = target_node_id.lower().strip()
        self.cadence_tolerance_fraction = cadence_tolerance_fraction
        self.input_length = int(self.runtime_artifact["input_length"])
        self.frequency_bins = int(self.runtime_artifact["frequency_bins"])
        self.target_names = tuple(
            str(x) for x in self.runtime_artifact["target_names"]
        )
        self.normalization_ranges = {
            str(name): (float(bounds[0]), float(bounds[1]))
            for name, bounds in self.runtime_artifact.get(
                "normalization_ranges", {}
            ).items()
        }
        self.head_weight = self.runtime_artifact["head_weight"]
        self.head_bias = self.runtime_artifact["head_bias"]
        manifest_targets = tuple(str(x) for x in self.manifest.get("target_names", []))
        if manifest_targets and manifest_targets != self.target_names:
            raise ValueError("manifest target_names mismatch checkpoint")
        if int(self.manifest.get("input_length", self.input_length)) != self.input_length:
            raise ValueError("manifest input_length mismatch checkpoint")
        self.expected_cadence_sec = float(self.manifest["expected_cadence_sec"])
        if self.expected_cadence_sec <= 0:
            raise ValueError("expected_cadence_sec must be positive")
        self._history: dict[str, deque[list[float]]] = defaultdict(
            lambda: deque(maxlen=self.input_length)
        )
        self._last_ts: dict[str, float] = {}

    def _provenance(self) -> dict[str, Any]:
        return {
            "model_version": self.runtime_artifact.get("model_version"),
            "model_type": self.runtime_artifact.get("model_type"),
            "runtime_backend": self.manifest.get("runtime_backend"),
            "model_readiness": self.manifest.get("readiness", "EXPERIMENTAL"),
            "model_manifest_id": self.manifest.get("id"),
        }

    def _predict_normalized(self, history: list[list[float]]) -> list[float]:
        """Replicate edge_forecasting.FitsEdgeForecaster with NumPy."""

        try:
            import numpy as np
        except ModuleNotFoundError as exc:  # pragma: no cover - deployment preflight
            raise RuntimeError(
                "NumPy edge runtime is required; install project extra 'edge'"
            ) from exc

        values = np.asarray(history, dtype=np.float32)
        if values.shape != (self.input_length, len(self.target_names)):
            raise ValueError(
                "forecast history shape mismatch: "
                f"{values.shape} != {(self.input_length, len(self.target_names))}"
            )
        mean = values.mean(axis=0, keepdims=True, dtype=np.float32)
        scale = values.std(axis=0, keepdims=True, dtype=np.float32)
        scale = np.maximum(scale, np.float32(1e-5))
        normalized = (values - mean) / scale
        spectrum = np.fft.rfft(normalized, axis=0)[: self.frequency_bins, :]
        features = np.concatenate((spectrum.real, spectrum.imag), axis=0)
        features = np.asarray(features.reshape(-1), dtype=np.float32)
        weight = np.asarray(self.head_weight, dtype=np.float32)
        bias = np.asarray(self.head_bias, dtype=np.float32)
        if weight.shape != (len(self.target_names), features.shape[0]):
            raise ValueError("runtime head weight shape does not match feature shape")
        residual = weight @ features + bias
        prediction = values[-1, :] + residual * scale[0, :]
        return [float(item) for item in prediction]

    def _normalize(self, name: str, value: float) -> float:
        if name not in self.normalization_ranges:
            raise ValueError(f"model missing normalization range for {name}")
        low, high = self.normalization_ranges[name]
        if high <= low:
            raise ValueError(f"invalid model normalization range for {name}")
        return max(0.0, min(1.0, (float(value) - low) / (high - low)))

    def _denormalize(self, name: str, value: float) -> float:
        low, high = self.normalization_ranges[name]
        return low + float(value) * (high - low)

    def process(self, vector: FeatureVector) -> dict[str, Any]:
        if self.target_node_id and vector.node_id.lower() != self.target_node_id:
            return {"status": "not_target_node", "forecast_status": "unavailable"}

        for name in self.target_names:
            presence_name = f"has_{name}"
            if presence_name in vector.values and vector.values[presence_name] < 0.5:
                return {
                    "status": "abstain_missing_feature",
                    "forecast_status": "unavailable",
                    "missing_feature": name,
                    **self._provenance(),
                }
            if name not in vector.values or not math.isfinite(float(vector.values[name])):
                return {
                    "status": "abstain_missing_feature",
                    "forecast_status": "unavailable",
                    "missing_feature": name,
                    **self._provenance(),
                }

        row = [self._normalize(name, float(vector.values[name])) for name in self.target_names]
        history = self._history[vector.node_id]
        timestamp = vector.timestamp.timestamp()
        previous = self._last_ts.get(vector.node_id)
        if previous is not None:
            delta = timestamp - previous
            if abs(delta) <= 1e-6:
                if history:
                    history[-1] = row
                else:
                    history.append(row)
            else:
                tolerance = self.expected_cadence_sec * self.cadence_tolerance_fraction
                if abs(delta - self.expected_cadence_sec) > tolerance:
                    history.clear()
                    self._last_ts[vector.node_id] = timestamp
                    return {
                        "status": "abstain_cadence_mismatch",
                        "forecast_status": "unavailable",
                        "observed_cadence_sec": delta,
                        "expected_cadence_sec": self.expected_cadence_sec,
                        **self._provenance(),
                    }
                history.append(row)
                self._last_ts[vector.node_id] = timestamp
        else:
            history.append(row)
            self._last_ts[vector.node_id] = timestamp
        if len(history) < self.input_length:
            return {
                "status": "warming",
                "forecast_status": "warming",
                "samples": len(history),
                "required_samples": self.input_length,
                **self._provenance(),
            }

        started = time.perf_counter()
        prediction = self._predict_normalized(list(history))
        latency_ms = (time.perf_counter() - started) * 1000.0
        predicted = {
            name: self._denormalize(name, float(prediction[index]))
            for index, name in enumerate(self.target_names)
        }
        return {
            "status": "ok",
            "forecast_status": "available_shadow",
            "predicted": predicted,
            "horizon_steps": int(self.manifest.get("horizon_steps", 1)),
            "horizon_duration_seconds": self.manifest.get("horizon_duration_seconds"),
            **self._provenance(),
            "inference_latency_ms": latency_ms,
        }
