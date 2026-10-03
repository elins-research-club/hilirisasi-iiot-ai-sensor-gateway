from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .advanced_forecasting import build_tsmixer_lite
from .contracts import FeatureVector
from .model_registry import validate_deployment_manifest


class GenericLiveForecasterV2:
    """Fail-closed shadow runtime for local forecast model manifests v2.

    NumPy backends are intended for edge deployment. The TSMixer backend is
    supported for parity/shadow testing when PyTorch is available, but a host
    success is not treated as Raspberry Pi evidence.
    """

    def __init__(
        self,
        manifest_path: str | Path,
        *,
        target_node_id: str = "",
        cadence_tolerance_fraction: float = 0.20,
        device: str = "cpu",
    ) -> None:
        validated = validate_deployment_manifest(manifest_path)
        self.manifest_path = Path(validated["manifest_path"])
        self.manifest = validated["manifest"]
        self.metadata = validated["metadata"]
        self.metadata_path = Path(validated["metadata_path"])
        self.artifact_path = Path(validated["artifact_path"])
        self.runtime_backend = str(self.manifest["runtime_backend"])
        self.model_type = str(self.manifest["model_type"])
        self.feature_names = tuple(str(item) for item in self.manifest["feature_names"])
        self.target_names = tuple(str(item) for item in self.manifest["target_names"])
        self.target_indices = tuple(int(item) for item in self.manifest["target_indices"])
        self.input_length = int(self.manifest["input_length"])
        self.horizon_steps = int(self.manifest["horizon_steps"])
        self.expected_cadence_sec = float(self.manifest["expected_cadence_sec"])
        self.cadence_tolerance_fraction = float(cadence_tolerance_fraction)
        if not 0.0 <= self.cadence_tolerance_fraction <= 1.0:
            raise ValueError("cadence_tolerance_fraction must be in [0,1]")
        manifest_target = str(self.manifest.get("target_node_id", "")).lower().strip()
        requested_target = target_node_id.lower().strip()
        if manifest_target and requested_target and manifest_target != requested_target:
            raise ValueError("target_node_id conflicts with deployment manifest")
        self.target_node_id = requested_target or manifest_target
        self.normalization_ranges = {
            str(name): (float(bounds[0]), float(bounds[1]))
            for name, bounds in self.manifest.get("normalization_ranges", {}).items()
        }
        self._history: dict[str, deque[list[float]]] = defaultdict(
            lambda: deque(maxlen=self.input_length)
        )
        self._last_ts: dict[str, float] = {}
        self._device = device
        self._model = self._load_backend(device)

    def _load_backend(self, device: str) -> Any:
        if self.runtime_backend in {"numpy_linear_v2", "numpy_nlinear_v2"}:
            try:
                import numpy as np
            except ModuleNotFoundError as exc:  # pragma: no cover
                raise RuntimeError("NumPy is required for local edge forecast runtime") from exc
            raw = np.load(self.artifact_path, allow_pickle=False)
            return {name: raw[name] for name in raw.files}
        if self.runtime_backend != "torch_tsmixer_v2":
            raise ValueError(f"unsupported v2 runtime backend: {self.runtime_backend}")
        try:
            import torch
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("PyTorch is required for TSMixer shadow runtime") from exc
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise ValueError("CUDA requested but unavailable")
        resolved_device = "cuda" if device == "auto" and torch.cuda.is_available() else (
            "cpu" if device == "auto" else device
        )
        try:
            checkpoint = torch.load(
                self.artifact_path,
                map_location=resolved_device,
                weights_only=True,
            )
        except Exception as exc:
            raise ValueError("unsafe or invalid TSMixer runtime checkpoint rejected") from exc
        model = build_tsmixer_lite(
            sequence_length=int(checkpoint["sequence_length"]),
            feature_count=int(checkpoint["feature_count"]),
            horizon_steps=int(checkpoint["horizon_steps"]),
            target_count=int(checkpoint["target_count"]),
            target_indices=tuple(int(item) for item in checkpoint["target_indices"]),
            blocks=int(checkpoint["blocks"]),
            time_hidden=int(checkpoint["time_hidden"]),
            feature_hidden=int(checkpoint["feature_hidden"]),
            dropout=float(checkpoint["dropout"]),
        ).to(resolved_device)
        model.load_state_dict(checkpoint["state_dict"], strict=True)
        model.eval()
        self._device = resolved_device
        return model

    def _normalize_row(self, vector: FeatureVector) -> tuple[list[float] | None, str | None]:
        row: list[float] = []
        for target in self.target_names:
            presence = vector.values.get(f"has_{target}")
            if presence is not None and float(presence) < 0.5:
                return None, target
        for name in self.feature_names:
            value = vector.values.get(name)
            if value is None or not math.isfinite(float(value)):
                return None, name
            bounds = self.normalization_ranges.get(name)
            if bounds is None:
                return None, f"normalization_range:{name}"
            low, high = bounds
            if high <= low:
                raise ValueError(f"invalid normalization range for {name}")
            normalized = (float(value) - low) / (high - low)
            row.append(max(0.0, min(1.0, normalized)))
        return row, None

    def _denormalize(self, name: str, value: float) -> float:
        if name not in self.normalization_ranges:
            raise ValueError(f"target normalization range missing for {name}")
        low, high = self.normalization_ranges[name]
        return low + float(value) * (high - low)

    def _predict(self, history: list[list[float]]) -> Any:
        import numpy as np

        values = np.asarray(history, dtype=np.float64)
        if values.shape != (self.input_length, len(self.feature_names)):
            raise ValueError("local forecast runtime history shape mismatch")
        if not np.isfinite(values).all():
            raise ValueError("local forecast runtime history contains non-finite values")
        state = self._model
        if self.runtime_backend == "numpy_linear_v2":
            flat = values.reshape(1, -1)
            xs = (flat - state["x_mean"]) / state["x_std"]
            prediction = xs @ state["weights"] + state["y_mean"]
            return prediction.reshape(self.horizon_steps, len(self.target_names))
        if self.runtime_backend == "numpy_nlinear_v2":
            output = np.empty((self.horizon_steps, len(self.target_names)), dtype=np.float64)
            for target in range(len(self.target_names)):
                target_history = values[:, self.target_indices[target]]
                last = target_history[-1]
                normalized = target_history - last
                residual = normalized @ state["weights"][target] + state["bias"][target]
                output[:, target] = residual + last
            return output
        import torch

        with torch.no_grad():
            tensor = torch.from_numpy(values.astype("float32")).unsqueeze(0).to(self._device)
            return self._model(tensor)[0].detach().cpu().numpy()

    def predict_normalized_history(self, history: Any) -> Any:
        """Run one already-normalized history for parity/resource benchmarking.

        This method does not mutate live cadence/history state. Production live
        ingestion must still use :meth:`process`, which enforces node identity,
        raw-feature normalization, missingness, cadence, and warm-up gates.
        """

        try:
            import numpy as np
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("NumPy is required for local edge forecast runtime") from exc
        values = np.asarray(history, dtype=np.float64)
        return self._predict(values.tolist())

    def _provenance(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "runtime_backend": self.runtime_backend,
            "model_readiness": self.manifest.get("readiness", "EXPERIMENTAL"),
            "model_manifest_id": self.manifest.get("id"),
        }

    def process(self, vector: FeatureVector) -> dict[str, Any]:
        if self.target_node_id and vector.node_id.lower() != self.target_node_id:
            return {"status": "not_target_node", "forecast_status": "unavailable"}
        row, missing = self._normalize_row(vector)
        if row is None:
            return {
                "status": "abstain_missing_feature",
                "forecast_status": "unavailable",
                "missing_feature": missing,
                **self._provenance(),
            }
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
        normalized = self.predict_normalized_history(list(history))
        latency_ms = (time.perf_counter() - started) * 1000.0
        trajectory = [
            {
                name: self._denormalize(name, float(normalized[horizon, index]))
                for index, name in enumerate(self.target_names)
            }
            for horizon in range(self.horizon_steps)
        ]
        return {
            "status": "ok",
            "forecast_status": "available_shadow",
            # Existing sensor_ai.v2 contract expects one predicted mapping;
            # expose the terminal horizon there and keep the full trajectory
            # for shadow diagnostics/runtime evidence.
            "predicted": trajectory[-1],
            "trajectory": trajectory,
            "horizon_steps": self.horizon_steps,
            "horizon_duration_seconds": self.manifest.get("horizon_duration_seconds"),
            **self._provenance(),
            "inference_latency_ms": latency_ms,
        }


def load_generic_forecaster_manifest(path: str | Path) -> dict[str, Any]:
    """Small helper for preflight tools without loading model dependencies."""

    validated = validate_deployment_manifest(path)
    return json.loads(Path(validated["manifest_path"]).read_text(encoding="utf-8"))
