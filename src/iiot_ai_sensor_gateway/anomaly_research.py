from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

CATALOG_SCHEMA = "iiot.ai_sensor.anomaly_research_catalog.v1"
RUN_SCHEMA = "iiot.ai_sensor.static_anomaly_research.v1"


def anomaly_research_catalog() -> dict[str, Any]:
    return {
        "schema": CATALOG_SCHEMA,
        "models": [
            {
                "id": "isolation_forest",
                "package": "scikit-learn",
                "import_name": "sklearn",
                "role": "static_window_baseline",
                "runtime_default": False,
            },
            {
                "id": "ecod",
                "package": "pyod",
                "import_name": "pyod",
                "role": "interpretable_tail_baseline",
                "runtime_default": False,
            },
            {
                "id": "copod",
                "package": "pyod",
                "import_name": "pyod",
                "role": "interpretable_tail_baseline",
                "runtime_default": False,
            },
            {
                "id": "granite_tspulse_r1",
                "package": "granite-tsfm",
                "import_name": "tsfm_public",
                "model_path": "ibm-granite/granite-timeseries-tspulse-r1",
                "role": "foundation_anomaly_comparator",
                "runtime_default": False,
                "minimum_points_guidance": 1536,
                "notes": (
                    "Official model guidance recommends roughly 3-4x the 512-point base "
                    "context for stable anomaly detection; keep host-only until long project-real "
                    "data and Pi resource evidence exist."
                ),
            },
        ],
        "policy": (
            "Fit/calibration data must be distinct from evaluation data. Optional research "
            "packages are never auto-installed and cannot bypass event/point metric gates."
        ),
    }


def probe_anomaly_research_environment() -> dict[str, Any]:
    packages: dict[str, Any] = {}
    for item in anomaly_research_catalog()["models"]:
        package = str(item["package"])
        if package in packages:
            continue
        import_name = str(item["import_name"])
        installed = importlib.util.find_spec(import_name) is not None
        version = None
        if installed:
            try:
                version = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                version = "installed-version-unknown"
        packages[package] = {
            "installed": installed,
            "version": version,
            "import_name": import_name,
        }
    return {
        "schema": "iiot.ai_sensor.anomaly_research_environment.v1",
        "packages": packages,
        "automatic_install": False,
    }


def _load_normalized_features(
    path: str | Path,
    feature_names: tuple[str, ...],
) -> tuple[list[dict[str, Any]], Any]:
    try:
        import numpy as np
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("NumPy is required for anomaly research evaluation") from exc
    if not feature_names:
        raise ValueError("feature_names must not be empty")
    records: list[dict[str, Any]] = []
    rows: list[list[float]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number} must be an object")
            source = record.get("features", record.get("values", record))
            if not isinstance(source, dict):
                raise ValueError(f"line {line_number} feature source must be an object")
            row: list[float] = []
            for name in feature_names:
                if name not in source:
                    raise ValueError(f"line {line_number} missing feature {name}")
                value = float(source[name])
                if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError(
                        f"line {line_number} feature {name} must be finite and normalized [0,1]"
                    )
                row.append(value)
            records.append(record)
            rows.append(row)
    if len(rows) < 2:
        raise ValueError("anomaly research input requires at least two records")
    return records, np.asarray(rows, dtype=np.float64)


def _empirical_anomaly_probability(reference_scores: Any, evaluation_scores: Any) -> Any:
    import numpy as np

    reference = np.sort(np.asarray(reference_scores, dtype=np.float64).reshape(-1))
    evaluation = np.asarray(evaluation_scores, dtype=np.float64).reshape(-1)
    if reference.size < 2 or not np.isfinite(reference).all() or not np.isfinite(evaluation).all():
        raise ValueError("anomaly score calibration requires finite reference/evaluation scores")
    ranks = np.searchsorted(reference, evaluation, side="right")
    return ranks.astype(np.float64) / float(reference.size)


def run_static_anomaly_research(
    fit_input_jsonl: str | Path,
    eval_input_jsonl: str | Path,
    output_jsonl: str | Path,
    *,
    feature_names: tuple[str, ...],
    backend: str,
    contamination: float = 0.01,
    seed: int = 42,
    n_estimators: int = 200,
) -> dict[str, Any]:
    """Fit an optional static anomaly baseline on separate normal-reference data.

    Scores are converted to empirical reference-tail probabilities in [0,1],
    allowing the existing event/point benchmark to consume all backends under
    the same threshold semantics without pretending their raw scores match.
    """

    if Path(fit_input_jsonl).resolve() == Path(eval_input_jsonl).resolve():
        raise ValueError("fit_input_jsonl and eval_input_jsonl must be different to prevent fit/eval leakage")
    if not 0.0 < contamination < 0.5:
        raise ValueError("contamination must be in (0,0.5)")
    if backend not in {"isolation_forest", "ecod", "copod"}:
        raise ValueError("backend must be isolation_forest, ecod, or copod")
    fit_records, fit_x = _load_normalized_features(fit_input_jsonl, feature_names)
    eval_records, eval_x = _load_normalized_features(eval_input_jsonl, feature_names)

    if backend == "isolation_forest":
        try:
            from sklearn.ensemble import IsolationForest
        except ModuleNotFoundError as exc:
            raise RuntimeError("IsolationForest research backend requires scikit-learn") from exc
        if n_estimators < 1:
            raise ValueError("n_estimators must be positive")
        model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=seed,
            n_jobs=1,
        )
        model.fit(fit_x)
        fit_raw = -model.score_samples(fit_x)
        eval_raw = -model.score_samples(eval_x)
        backend_name = "sklearn.IsolationForest"
    else:
        try:
            if backend == "ecod":
                from pyod.models.ecod import ECOD

                model = ECOD(contamination=contamination)
            else:
                from pyod.models.copod import COPOD

                model = COPOD(contamination=contamination)
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"{backend.upper()} research backend requires optional package pyod") from exc
        model.fit(fit_x)
        fit_raw = model.decision_scores_
        eval_raw = model.decision_function(eval_x)
        backend_name = f"pyod.{backend.upper()}"

    probabilities = _empirical_anomaly_probability(fit_raw, eval_raw)
    threshold = 1.0 - contamination
    output = Path(output_jsonl)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record, probability, raw_score in zip(eval_records, probabilities, eval_raw):
            handle.write(
                json.dumps(
                    {
                        "schema": "iiot.ai_sensor.static_anomaly_detection.v1",
                        "timestamp": record.get("timestamp"),
                        "warmup_complete": True,
                        "anomaly_score": float(probability),
                        "raw_backend_score": float(raw_score),
                        "is_anomaly": bool(probability >= threshold),
                        "backend": backend_name,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    summary = {
        "schema": RUN_SCHEMA,
        "backend": backend_name,
        "fit_input": str(fit_input_jsonl),
        "eval_input": str(eval_input_jsonl),
        "output": str(output),
        "feature_names": list(feature_names),
        "fit_samples": len(fit_records),
        "eval_samples": len(eval_records),
        "contamination": contamination,
        "empirical_probability_threshold": threshold,
        "automatic_dependency_install": False,
        "status": "RESEARCH_ONLY",
        "production_accuracy_claim": None,
    }
    output.with_suffix(output.suffix + ".meta.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def tspulse_preflight(
    input_jsonl: str | Path,
    *,
    feature_names: tuple[str, ...],
    minimum_points: int = 1536,
) -> dict[str, Any]:
    """Fail-closed preflight for the official TSPulse anomaly research lane."""

    records, _matrix = _load_normalized_features(input_jsonl, feature_names)
    environment = probe_anomaly_research_environment()["packages"]["granite-tsfm"]
    if len(records) < minimum_points:
        return {
            "status": "BLOCKED_INSUFFICIENT_CONTEXT",
            "points": len(records),
            "minimum_points": minimum_points,
            "package_installed": environment["installed"],
            "automatic_download": False,
        }
    return {
        "status": "READY_FOR_OPTIONAL_OFFICIAL_TSPULSE_HARNESS"
        if environment["installed"]
        else "BLOCKED_DEPENDENCY_NOT_INSTALLED",
        "points": len(records),
        "minimum_points": minimum_points,
        "package_installed": environment["installed"],
        "automatic_download": False,
    }
