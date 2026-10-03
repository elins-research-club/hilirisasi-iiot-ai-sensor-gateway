from __future__ import annotations

import importlib.metadata
import json
import math
import os
import platform
import time
from pathlib import Path
from typing import Any

from .advanced_forecasting import predict_local_forecast_v2
from .forecast_evaluator_v2 import load_forecast_dataset_v2
from .generic_forecast_runtime import GenericLiveForecasterV2
from .model_registry import sha256_file, validate_deployment_manifest

BENCHMARK_SCHEMA = "iiot.ai_sensor.forecast_runtime_benchmark.v2"


def detect_hardware() -> dict[str, Any]:
    override = os.environ.get("IIOT_HARDWARE_LABEL", "").strip()
    model = ""
    try:
        model = Path("/proc/device-tree/model").read_text(
            encoding="utf-8", errors="ignore"
        ).strip("\x00\n")
    except OSError:
        pass
    if override:
        label = override
    elif "raspberry pi 5" in model.lower():
        label = "raspberry_pi_5"
    else:
        system = platform.system().strip().lower().replace("-", "_")
        machine = platform.machine().strip().lower().replace("-", "_")
        label = f"{system}_{machine}" if system and machine else "unknown_host"
    return {
        "hardware_label": label,
        "device_tree_model": model or None,
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "is_raspberry_pi_5": label == "raspberry_pi_5",
    }


def _max_rss_kib() -> int | None:
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except ImportError:  # pragma: no cover - Windows
        return None


def _thermal_c() -> float | None:
    candidates = (
        Path("/sys/class/thermal/thermal_zone0/temp"),
        Path("/sys/devices/virtual/thermal/thermal_zone0/temp"),
    )
    for path in candidates:
        try:
            value = float(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if value > 1000.0:
            value /= 1000.0
        if math.isfinite(value):
            return value
    return None


def _distribution_size_bytes(name: str) -> int | None:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    total = 0
    found = False
    for entry in distribution.files or ():
        try:
            path = Path(distribution.locate_file(entry))
            if path.is_file():
                total += path.stat().st_size
                found = True
        except OSError:
            continue
    return total if found else None


def _percentile(values: Any, percentile: float) -> float:
    import numpy as np

    return float(np.percentile(values, percentile))


def benchmark_forecast_runtime_v2(
    dataset_npz: str | Path,
    manifest_path: str | Path,
    output_json: str | Path | None = None,
    *,
    split: str = "test",
    iterations: int = 200,
    warmup: int = 10,
    device: str = "cpu",
    parity_samples: int = 32,
) -> dict[str, Any]:
    if split not in {"train", "val", "test"}:
        raise ValueError("split must be train, val, or test")
    if iterations < 1 or warmup < 0 or parity_samples < 1:
        raise ValueError("iterations/parity_samples must be positive and warmup non-negative")
    import numpy as np

    data = load_forecast_dataset_v2(dataset_npz)
    validated = validate_deployment_manifest(manifest_path)
    manifest = validated["manifest"]
    if tuple(manifest["feature_names"]) != data["feature_names_tuple"]:
        raise ValueError("benchmark manifest feature schema does not match dataset")
    if tuple(manifest["target_names"]) != data["target_names_tuple"]:
        raise ValueError("benchmark manifest target schema does not match dataset")
    if int(manifest["input_length"]) != int(data[f"X_{split}"].shape[1]):
        raise ValueError("benchmark manifest input length does not match dataset")
    if int(manifest["horizon_steps"]) != int(data[f"Y_{split}"].shape[1]):
        raise ValueError("benchmark manifest horizon does not match dataset")

    hardware = detect_hardware()
    thermal_before = _thermal_c()
    rss_before = _max_rss_kib()
    cold_started = time.perf_counter()
    runtime = GenericLiveForecasterV2(manifest_path, device=device)
    cold_start_ms = (time.perf_counter() - cold_started) * 1000.0
    x = data[f"X_{split}"].astype(np.float32)
    sample_count = min(iterations, len(x))
    if sample_count < 1:
        raise ValueError(f"benchmark split {split} is empty")

    for index in range(min(warmup, len(x))):
        runtime.predict_normalized_history(x[index])

    latencies: list[float] = []
    process_cpu_start = time.process_time()
    wall_start = time.perf_counter()
    for index in range(sample_count):
        started = time.perf_counter()
        prediction = runtime.predict_normalized_history(x[index])
        latency = (time.perf_counter() - started) * 1000.0
        if not np.isfinite(prediction).all():
            raise ValueError("runtime benchmark produced non-finite prediction")
        latencies.append(latency)
    wall_seconds = time.perf_counter() - wall_start
    process_cpu_seconds = time.process_time() - process_cpu_start
    lat = np.asarray(latencies, dtype=np.float64)

    parity_count = min(parity_samples, len(x))
    source_prediction = predict_local_forecast_v2(
        dataset_npz,
        validated["metadata_path"],
        split=split,
        device=device,
        batch_size=max(1, parity_count),
    )[:parity_count]
    runtime_prediction = np.stack(
        [runtime.predict_normalized_history(x[index]) for index in range(parity_count)],
        axis=0,
    )
    absolute_error = np.abs(runtime_prediction - source_prediction)
    parity_max_abs = float(np.max(absolute_error))
    parity_mean_abs = float(np.mean(absolute_error))
    parity_pass = bool(np.allclose(runtime_prediction, source_prediction, rtol=1e-5, atol=1e-6))

    dependency_packages = ["numpy"]
    if manifest["runtime_backend"] == "torch_tsmixer_v2":
        dependency_packages.append("torch")
    dependency_sizes = {
        package: _distribution_size_bytes(package) for package in dependency_packages
    }
    measured_dependency_size = sum(
        size for size in dependency_sizes.values() if size is not None
    )
    all_dependency_sizes_measured = all(
        size is not None for size in dependency_sizes.values()
    )
    rss_after = _max_rss_kib()
    thermal_after = _thermal_c()
    result = {
        "schema": BENCHMARK_SCHEMA,
        "dataset": str(dataset_npz),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "model_type": manifest["model_type"],
        "runtime_backend": manifest["runtime_backend"],
        "split": split,
        "device": device,
        "hardware": hardware,
        "evidence_scope": (
            "RASPBERRY_PI_5_MEASUREMENT"
            if hardware["is_raspberry_pi_5"]
            else "CURRENT_HOST_MEASUREMENT_NOT_PI_EVIDENCE"
        ),
        "cold_start_ms": cold_start_ms,
        "samples_benchmarked": sample_count,
        "warmup_samples": min(warmup, len(x)),
        "latency_ms": {
            "mean": float(np.mean(lat)),
            "median": float(np.median(lat)),
            "p50": _percentile(lat, 50),
            "p95": _percentile(lat, 95),
            "p99": _percentile(lat, 99),
            "min": float(np.min(lat)),
            "max": float(np.max(lat)),
        },
        "throughput_samples_per_sec": sample_count / wall_seconds if wall_seconds > 0 else None,
        "process_cpu_seconds": process_cpu_seconds,
        "wall_seconds": wall_seconds,
        "process_cpu_time_ratio": process_cpu_seconds / wall_seconds if wall_seconds > 0 else None,
        "process_max_rss_kib_before": rss_before,
        "process_max_rss_kib_after": rss_after,
        "thermal_c_before": thermal_before,
        "thermal_c_after": thermal_after,
        "footprint": {
            "manifest_bytes": Path(manifest_path).stat().st_size,
            "model_metadata_bytes": Path(validated["metadata_path"]).stat().st_size,
            "artifact_bytes": Path(validated["artifact_path"]).stat().st_size,
            "dependency_distribution_bytes": dependency_sizes,
            "dependency_size_total_bytes": (
                measured_dependency_size if all_dependency_sizes_measured else None
            ),
            "dependency_size_measurement_complete": all_dependency_sizes_measured,
        },
        "parity": {
            "samples": parity_count,
            "rtol": 1e-5,
            "atol": 1e-6,
            "max_abs_error": parity_max_abs,
            "mean_abs_error": parity_mean_abs,
            "passed": parity_pass,
        },
        "restart_rehydration_tested": False,
        "soak_tested": False,
        "power_measured": False,
        "production_promotion": False,
    }
    if not parity_pass:
        raise ValueError(
            f"runtime/source parity failed: max_abs_error={parity_max_abs:g}"
        )
    if output_json is not None:
        output = Path(output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
