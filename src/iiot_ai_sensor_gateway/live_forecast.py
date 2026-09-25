from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .contracts import FeatureVector
from .edge_forecasting import load_edge_model


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LiveEdgeForecaster:
    """Fail-closed live inference wrapper for a promoted edge forecast artifact."""

    def __init__(
        self,
        manifest_path: str | Path,
        *,
        target_node_id: str = "",
        cadence_tolerance_fraction: float = 0.20,
        device: str = "cpu",
    ) -> None:
        self.manifest_path = Path(manifest_path)
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"forecast manifest not found: {self.manifest_path}")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("schema") != "iiot.ai_sensor.model_manifest.v1":
            raise ValueError("unsupported forecast manifest schema")
        model_ref = Path(str(self.manifest["model_path"]))
        if not model_ref.is_absolute():
            model_ref = (self.manifest_path.parent.parent.parent / model_ref).resolve()
        self.model_path = model_ref
        expected_sha = str(self.manifest.get("model_sha256", "")).lower()
        if not expected_sha or _sha256(self.model_path) != expected_sha:
            raise ValueError("forecast model SHA-256 mismatch")
        self.model, self.checkpoint, self.device = load_edge_model(self.model_path, device)
        self.target_node_id = target_node_id.lower().strip()
        self.cadence_tolerance_fraction = cadence_tolerance_fraction
        self.input_length = int(self.checkpoint["input_length"])
        self.target_names = tuple(str(x) for x in self.checkpoint["target_names"])
        self.normalization_ranges = {
            str(name): (float(bounds[0]), float(bounds[1]))
            for name, bounds in self.checkpoint.get("normalization_ranges", {}).items()
        }
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
                }
            if name not in vector.values or not math.isfinite(float(vector.values[name])):
                return {
                    "status": "abstain_missing_feature",
                    "forecast_status": "unavailable",
                    "missing_feature": name,
                }

        timestamp = vector.timestamp.timestamp()
        previous = self._last_ts.get(vector.node_id)
        if previous is not None:
            delta = timestamp - previous
            tolerance = self.expected_cadence_sec * self.cadence_tolerance_fraction
            if abs(delta - self.expected_cadence_sec) > tolerance:
                self._history[vector.node_id].clear()
                self._last_ts[vector.node_id] = timestamp
                return {
                    "status": "abstain_cadence_mismatch",
                    "forecast_status": "unavailable",
                    "observed_cadence_sec": delta,
                    "expected_cadence_sec": self.expected_cadence_sec,
                }
        self._last_ts[vector.node_id] = timestamp

        row = [self._normalize(name, float(vector.values[name])) for name in self.target_names]
        history = self._history[vector.node_id]
        history.append(row)
        if len(history) < self.input_length:
            return {
                "status": "warming",
                "forecast_status": "warming",
                "samples": len(history),
                "required_samples": self.input_length,
            }

        import torch

        target_history = torch.tensor([list(history)], dtype=torch.float32, device=self.device)
        started = time.perf_counter()
        with torch.no_grad():
            prediction = self.model(target_history).detach().cpu().numpy()[0]
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
            "model_version": self.checkpoint.get("model_version"),
            "model_type": self.checkpoint.get("model_type"),
            "model_readiness": self.manifest.get("readiness", "EXPERIMENTAL"),
            "model_manifest_id": self.manifest.get("id"),
            "inference_latency_ms": latency_ms,
        }
