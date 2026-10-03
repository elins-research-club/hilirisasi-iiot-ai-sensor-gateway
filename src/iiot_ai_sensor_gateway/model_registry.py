from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

LOCAL_MODEL_SCHEMA = "iiot.ai_sensor.local_forecast_model.v2"
DEPLOYMENT_MANIFEST_SCHEMA = "iiot.ai_sensor.model_manifest.v2"
REGISTRY_SCHEMA = "iiot.ai_sensor.model_registry.v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_ref(target: Path, base_dir: Path) -> str:
    return os.path.relpath(target.resolve(), base_dir.resolve())


def _resolve_ref(base_file: Path, ref: str) -> Path:
    path = Path(ref)
    return path if path.is_absolute() else (base_file.parent / path).resolve()


def _load_json(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return raw


def validate_deployment_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = _load_json(manifest_path)
    if manifest.get("schema") != DEPLOYMENT_MANIFEST_SCHEMA:
        raise ValueError(f"unsupported deployment manifest schema: {manifest.get('schema')}")
    model_id = str(manifest.get("id", ""))
    if not _SAFE_ID.fullmatch(model_id):
        raise ValueError("deployment manifest id is invalid")
    if manifest.get("deployment_mode") not in {"shadow_only", "disabled"}:
        raise ValueError("v2 deployment manifests are fail-closed to shadow_only/disabled")
    expected_cadence = float(manifest.get("expected_cadence_sec", 0.0))
    if expected_cadence <= 0:
        raise ValueError("expected_cadence_sec must be positive")
    if int(manifest.get("input_length", 0)) < 2 or int(manifest.get("horizon_steps", 0)) < 1:
        raise ValueError("deployment manifest input_length/horizon_steps are invalid")
    metadata_path = _resolve_ref(manifest_path, str(manifest.get("model_metadata_path", "")))
    if not metadata_path.is_file():
        raise FileNotFoundError(f"model metadata not found: {metadata_path}")
    expected_metadata_sha = str(manifest.get("model_metadata_sha256", "")).lower()
    if not expected_metadata_sha or sha256_file(metadata_path) != expected_metadata_sha:
        raise ValueError("model metadata SHA-256 mismatch")
    metadata = _load_json(metadata_path)
    if metadata.get("schema") != LOCAL_MODEL_SCHEMA:
        raise ValueError("deployment manifest does not reference a local forecast v2 model")
    artifact_path = _resolve_ref(metadata_path, str(metadata.get("artifact", "")))
    if not artifact_path.is_file():
        raise FileNotFoundError(f"model artifact not found: {artifact_path}")
    if sha256_file(artifact_path) != str(metadata.get("artifact_sha256", "")).lower():
        raise ValueError("model artifact SHA-256 mismatch")
    if manifest.get("model_type") != metadata.get("model_type"):
        raise ValueError("manifest model_type does not match model metadata")
    if tuple(manifest.get("feature_names", ())) != tuple(metadata.get("feature_names", ())):
        raise ValueError("manifest feature_names do not match model metadata")
    if tuple(manifest.get("target_names", ())) != tuple(metadata.get("target_names", ())):
        raise ValueError("manifest target_names do not match model metadata")
    if str(manifest.get("feature_schema_sha256", "")) != str(
        metadata.get("feature_schema_sha256", "")
    ):
        raise ValueError("manifest feature schema hash does not match model metadata")
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "metadata": metadata,
        "metadata_path": metadata_path,
        "artifact_path": artifact_path,
    }


def export_local_deployment_manifest(
    model_metadata_path: str | Path,
    output_path: str | Path,
    *,
    model_id: str,
    target_node_id: str = "",
    readiness: str = "EXPERIMENTAL",
    deployment_mode: str = "shadow_only",
) -> dict[str, Any]:
    if not _SAFE_ID.fullmatch(model_id):
        raise ValueError("model_id must contain only safe alphanumeric/._- characters")
    if deployment_mode not in {"shadow_only", "disabled"}:
        raise ValueError("local model manifests may only be shadow_only or disabled")
    metadata_path = Path(model_metadata_path).resolve()
    metadata = _load_json(metadata_path)
    if metadata.get("schema") != LOCAL_MODEL_SCHEMA:
        raise ValueError(f"unsupported local model schema: {metadata.get('schema')}")
    artifact_path = _resolve_ref(metadata_path, str(metadata.get("artifact", "")))
    if not artifact_path.is_file():
        raise FileNotFoundError(f"model artifact not found: {artifact_path}")
    if sha256_file(artifact_path) != str(metadata.get("artifact_sha256", "")).lower():
        raise ValueError("model artifact SHA-256 mismatch before manifest export")
    model_type = str(metadata.get("model_type", ""))
    backend = {
        "ridge": "numpy_linear_v2",
        "elasticnet": "numpy_linear_v2",
        "nlinear": "numpy_nlinear_v2",
        "tsmixer_lite": "torch_tsmixer_v2",
    }.get(model_type)
    if backend is None:
        raise ValueError(f"unsupported local runtime model_type: {model_type}")
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    normalization_ranges = metadata.get("normalization_ranges") or {}
    missing_target_ranges = [
        name for name in metadata.get("target_names", ()) if name not in normalization_ranges
    ]
    if missing_target_ranges:
        raise ValueError(
            f"model metadata lacks normalization ranges for targets: {missing_target_ranges}"
        )
    manifest = {
        "schema": DEPLOYMENT_MANIFEST_SCHEMA,
        "id": model_id,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "model_type": model_type,
        "runtime_backend": backend,
        "model_metadata_path": _relative_ref(metadata_path, output.parent),
        "model_metadata_sha256": sha256_file(metadata_path),
        "artifact_sha256": sha256_file(artifact_path),
        "feature_schema_sha256": metadata.get("feature_schema_sha256"),
        "feature_names": list(metadata.get("feature_names", ())),
        "target_names": list(metadata.get("target_names", ())),
        "target_indices": list(metadata.get("target_indices", ())),
        "normalization_ranges": normalization_ranges,
        "input_length": int(metadata.get("sequence_length", 0)),
        "expected_cadence_sec": float(metadata.get("cadence_seconds", 0.0)),
        "horizon_steps": int(metadata.get("horizon_steps", 0)),
        "horizon_duration_seconds": float(metadata.get("cadence_seconds", 0.0))
        * int(metadata.get("horizon_steps", 0)),
        "target_node_id": target_node_id.lower().strip(),
        "readiness": readiness,
        "deployment_mode": deployment_mode,
        "activation_policy": "explicit_shadow_only_until_field_and_pi_evidence",
        "field_accuracy_claim": None,
    }
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    validate_deployment_manifest(output)
    return manifest


def build_model_registry(
    manifest_paths: Iterable[str | Path],
    output_path: str | Path,
) -> dict[str, Any]:
    output = Path(output_path).resolve()
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in manifest_paths:
        validated = validate_deployment_manifest(path)
        manifest = validated["manifest"]
        model_id = str(manifest["id"])
        if model_id in seen:
            raise ValueError(f"duplicate model id in registry: {model_id}")
        seen.add(model_id)
        manifest_path = Path(validated["manifest_path"])
        entries.append(
            {
                "id": model_id,
                "manifest_path": _relative_ref(manifest_path, output.parent),
                "manifest_sha256": sha256_file(manifest_path),
                "model_type": manifest["model_type"],
                "runtime_backend": manifest["runtime_backend"],
                "readiness": manifest["readiness"],
                "deployment_mode": manifest["deployment_mode"],
                "target_names": manifest["target_names"],
                "expected_cadence_sec": manifest["expected_cadence_sec"],
                "horizon_steps": manifest["horizon_steps"],
            }
        )
    registry = {
        "schema": REGISTRY_SCHEMA,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "models": sorted(entries, key=lambda item: item["id"]),
        "activation_policy": "registry_is_inventory_not_automatic_promotion",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return registry


class ModelRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        raw = _load_json(self.path)
        if raw.get("schema") != REGISTRY_SCHEMA:
            raise ValueError(f"unsupported model registry schema: {raw.get('schema')}")
        entries = raw.get("models")
        if not isinstance(entries, list):
            raise ValueError("model registry must contain a models list")
        self._entries: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not _SAFE_ID.fullmatch(str(entry.get("id", ""))):
                raise ValueError("invalid model registry entry")
            model_id = str(entry["id"])
            if model_id in self._entries:
                raise ValueError(f"duplicate model id in registry: {model_id}")
            manifest_path = _resolve_ref(self.path, str(entry.get("manifest_path", "")))
            if sha256_file(manifest_path) != str(entry.get("manifest_sha256", "")).lower():
                raise ValueError(f"registry manifest SHA-256 mismatch for {model_id}")
            validate_deployment_manifest(manifest_path)
            self._entries[model_id] = {**entry, "manifest_path_resolved": str(manifest_path)}

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def get(self, model_id: str) -> dict[str, Any]:
        if model_id not in self._entries:
            raise KeyError(model_id)
        return dict(self._entries[model_id])
