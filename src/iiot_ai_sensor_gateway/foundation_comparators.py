from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

from .forecast_evaluator_v2 import (
    compose_selected_baseline_v2,
    evaluate_baselines_v2,
    load_forecast_dataset_v2,
    regression_metrics_v2,
)

CATALOG_SCHEMA = "iiot.ai_sensor.foundation_model_catalog.v1"
COMPARATOR_SCHEMA = "iiot.ai_sensor.foundation_forecast_comparator.v1"


def foundation_model_catalog() -> dict[str, Any]:
    """Curated research catalog; entries are not automatic deployment choices."""

    return {
        "schema": CATALOG_SCHEMA,
        "models": [
            {
                "id": "granite_ttm_r3",
                "model_path": "ibm-granite/granite-timeseries-ttm-r3",
                "task": "forecasting",
                "role": "primary_zero_shot_few_shot_comparator",
                "license": "Apache-2.0",
                "package": "granite-tsfm",
                "import_name": "tsfm_public",
                "edge_default": False,
                "production_default": False,
                "notes": "Use host comparator first; Pi promotion requires separate latency/RAM evidence.",
            },
            {
                "id": "granite_tspulse",
                "model_path": "ibm-granite/granite-timeseries-tspulse-r1",
                "task": "anomaly_detection",
                "role": "foundation_anomaly_comparator",
                "license": "Apache-2.0",
                "package": "granite-tsfm",
                "import_name": "tsfm_public",
                "edge_default": False,
                "production_default": False,
                "notes": "Research comparator; long stable context requirements must be met before use.",
            },
            {
                "id": "chronos_bolt_tiny",
                "model_path": "amazon/chronos-bolt-tiny",
                "task": "forecasting",
                "role": "secondary_zero_shot_comparator",
                "license": "Apache-2.0",
                "package": "chronos-forecasting",
                "import_name": "chronos",
                "edge_default": False,
                "production_default": False,
                "notes": "Keep optional; compare only under the same evaluator and final holdout.",
            },
            {
                "id": "granite_flowstate_r1",
                "model_path": "ibm-granite/granite-timeseries-flowstate-r1",
                "task": "forecasting",
                "role": "research_only_comparator",
                "license": "Apache-2.0",
                "package": "granite-tsfm",
                "import_name": "tsfm_public",
                "edge_default": False,
                "production_default": False,
                "notes": "Larger research comparator; not prioritized over TTM for Raspberry Pi.",
            },
            {
                "id": "timesfm_3_0",
                "model_path": "google/timesfm-3.0-pytorch",
                "task": "forecasting",
                "role": "research_only_license_boundary",
                "license": "source Apache-2.0; current pretrained weights restricted/non-commercial",
                "package": "timesfm",
                "import_name": "timesfm",
                "edge_default": False,
                "production_default": False,
                "production_blocked": True,
                "notes": "Do not make a production dependency while pretrained-weight terms remain restrictive.",
            },
        ],
        "selection_policy": (
            "No global winner. Foundation models are optional comparators under the same "
            "dataset/evaluator and cannot bypass baseline, field, resource, or license gates."
        ),
    }


def probe_foundation_environment() -> dict[str, Any]:
    catalog = foundation_model_catalog()
    packages: dict[str, Any] = {}
    for model in catalog["models"]:
        package = str(model["package"])
        import_name = str(model["import_name"])
        if package in packages:
            continue
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
        "schema": "iiot.ai_sensor.foundation_environment.v1",
        "packages": packages,
        "automatic_install": False,
    }


def _baseline_delta(model_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> dict[str, Any]:
    model_rmse = float(model_metrics["overall_rmse"])
    baseline_rmse = float(baseline_metrics["overall_rmse"])
    return {
        "overall_rmse_delta": model_rmse - baseline_rmse,
        "overall_rmse_skill_score": (
            None if baseline_rmse == 0 else 1.0 - model_rmse / baseline_rmse
        ),
        "per_target": {
            name: {
                "rmse_delta": float(model_metrics["per_target"][name]["rmse"])
                - float(baseline_metrics["per_target"][name]["rmse"]),
                "beats_baseline": float(model_metrics["per_target"][name]["rmse"])
                < float(baseline_metrics["per_target"][name]["rmse"]),
            }
            for name in model_metrics["per_target"]
        },
    }


def run_granite_ttm_zero_shot_v2(
    dataset_npz: str | Path,
    output_json: str | Path,
    *,
    split: str = "test",
    model_path: str = "ibm-granite/granite-timeseries-ttm-r3",
    device: str = "cpu",
    batch_size: int = 64,
    local_files_only: bool = True,
    use_lite: bool = True,
) -> dict[str, Any]:
    """Run Granite TTM R3 zero-shot under the exact forecast-v2 evaluator.

    No package installation or model download is performed implicitly. By
    default Hugging Face loading is local-cache-only; callers must explicitly
    set local_files_only=False to permit a download.
    """

    if split not in {"val", "test"}:
        raise ValueError("foundation comparator split must be val or test")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    try:
        import torch
        from tsfm_public.toolkit.get_model import get_model
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Granite TTM comparator is optional and not installed. "
            "Use an isolated research environment with the official granite-tsfm package; "
            "do not add it to the Raspberry Pi runtime by default."
        ) from exc

    data = load_forecast_dataset_v2(dataset_npz)
    x = data[f"X_{split}"].astype("float32")
    y = data[f"Y_{split}"].astype("float32")
    context_length = int(x.shape[1])
    horizon_steps = int(y.shape[1])
    target_indices = [int(item) for item in data["target_indices"]]
    model = get_model(
        model_path=model_path,
        context_length=context_length,
        prediction_length=horizon_steps,
        force_return="zeropad",
        use_lite=use_lite,
        local_files_only=local_files_only,
    )
    if model is None:
        raise RuntimeError("Granite TTM selector returned no compatible model")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable")
    resolved_device = "cuda" if device == "auto" and torch.cuda.is_available() else (
        "cpu" if device == "auto" else device
    )
    model = model.to(resolved_device)
    model.eval()
    predictions = []
    started = time.perf_counter()
    with torch.no_grad():
        for start in range(0, x.shape[0], batch_size):
            values = torch.from_numpy(x[start : start + batch_size]).to(resolved_device)
            observed = torch.ones_like(values)
            output = model(
                past_values=values,
                past_observed_mask=observed,
                return_loss=False,
            )
            forecast = output.prediction_outputs[:, :horizon_steps, target_indices]
            predictions.append(forecast.detach().cpu().numpy())
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    import numpy as np

    predicted = np.concatenate(predictions, axis=0)
    if predicted.shape != y.shape or not np.isfinite(predicted).all():
        raise ValueError(
            f"Granite TTM prediction shape/finite check failed: {predicted.shape} != {y.shape}"
        )
    metrics = regression_metrics_v2(
        y,
        predicted,
        data["target_names_tuple"],
        data["mase_scale"],
        data["meta"].get("normalization_ranges", {}),
    )
    baseline_report = evaluate_baselines_v2(dataset_npz)
    baseline_prediction = compose_selected_baseline_v2(
        data, split, baseline_report["selection"]
    )
    baseline_metrics = regression_metrics_v2(
        y,
        baseline_prediction,
        data["target_names_tuple"],
        data["mase_scale"],
        data["meta"].get("normalization_ranges", {}),
    )
    config = getattr(model, "config", None)
    result = {
        "schema": COMPARATOR_SCHEMA,
        "model_id": "granite_ttm_r3",
        "model_path": model_path,
        "split": split,
        "dataset": str(dataset_npz),
        "local_files_only": local_files_only,
        "use_lite": use_lite,
        "device": resolved_device,
        "selected_context_length": getattr(config, "context_length", None),
        "selected_prediction_length": getattr(config, "prediction_length", None),
        "parameter_count": sum(int(parameter.numel()) for parameter in model.parameters()),
        "inference_total_ms": elapsed_ms,
        "inference_ms_per_sample": elapsed_ms / max(1, x.shape[0]),
        "metrics": metrics,
        "baseline": baseline_metrics,
        "baseline_delta": _baseline_delta(metrics, baseline_metrics),
        "status": "RESEARCH_COMPARATOR_HOST_ONLY",
        "production_promotion": False,
        "pi_shadow_verified": False,
        "field_generalization_verified": False,
    }
    output = Path(output_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def run_chronos_bolt_zero_shot_v2(
    dataset_npz: str | Path,
    output_json: str | Path,
    *,
    split: str = "test",
    model_path: str = "amazon/chronos-bolt-tiny",
    device: str = "cpu",
    local_files_only: bool = True,
    batch_size: int = 64,
) -> dict[str, Any]:
    """Run Chronos-Bolt Tiny as a target-history-only probabilistic comparator.

    Chronos-Bolt is intentionally evaluated one target series at a time. It is
    therefore not described as a covariate-aware multivariate model in this
    project. Downloads remain opt-in through ``local_files_only=False``.
    """

    if split not in {"val", "test"}:
        raise ValueError("foundation comparator split must be val or test")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    try:
        import numpy as np
        import torch
        from chronos import BaseChronosPipeline
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Chronos-Bolt comparator is optional and not installed. Use an isolated "
            "research environment with chronos-forecasting; do not add it to the "
            "Raspberry Pi runtime by default."
        ) from exc

    data = load_forecast_dataset_v2(dataset_npz)
    x = data[f"X_{split}"].astype("float32")
    y = data[f"Y_{split}"].astype("float32")
    target_indices = [int(item) for item in data["target_indices"]]
    horizon_steps = int(y.shape[1])
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable")
    resolved_device = "cuda" if device == "auto" and torch.cuda.is_available() else (
        "cpu" if device == "auto" else device
    )
    pipeline = BaseChronosPipeline.from_pretrained(
        model_path,
        device_map=resolved_device,
        local_files_only=local_files_only,
    )
    context = np.stack(
        [x[:, :, target_index] for target_index in target_indices], axis=1
    )
    flattened = torch.from_numpy(
        context.reshape(context.shape[0] * context.shape[1], context.shape[2])
    )
    means = []
    lower_quantiles = []
    upper_quantiles = []
    started = time.perf_counter()
    for start in range(0, flattened.shape[0], batch_size):
        quantiles, mean = pipeline.predict_quantiles(
            flattened[start : start + batch_size],
            prediction_length=horizon_steps,
            quantile_levels=[0.1, 0.5, 0.9],
        )
        if isinstance(quantiles, list):
            quantiles = torch.stack(quantiles)
        if isinstance(mean, list):
            mean = torch.stack(mean)
        quantiles = quantiles.detach().cpu()
        mean = mean.detach().cpu()
        means.append(mean.numpy())
        lower_quantiles.append(quantiles[..., 0].numpy())
        upper_quantiles.append(quantiles[..., -1].numpy())
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    mean_flat = np.concatenate(means, axis=0)
    lower_flat = np.concatenate(lower_quantiles, axis=0)
    upper_flat = np.concatenate(upper_quantiles, axis=0)
    sample_count = x.shape[0]
    target_count = len(target_indices)
    predicted = mean_flat.reshape(sample_count, target_count, horizon_steps).transpose(0, 2, 1)
    lower = lower_flat.reshape(sample_count, target_count, horizon_steps).transpose(0, 2, 1)
    upper = upper_flat.reshape(sample_count, target_count, horizon_steps).transpose(0, 2, 1)
    if predicted.shape != y.shape or not np.isfinite(predicted).all():
        raise ValueError(
            f"Chronos-Bolt prediction shape/finite check failed: {predicted.shape} != {y.shape}"
        )
    metrics = regression_metrics_v2(
        y,
        predicted,
        data["target_names_tuple"],
        data["mase_scale"],
        data["meta"].get("normalization_ranges", {}),
    )
    baseline_report = evaluate_baselines_v2(dataset_npz)
    baseline_prediction = compose_selected_baseline_v2(
        data, split, baseline_report["selection"]
    )
    baseline_metrics = regression_metrics_v2(
        y,
        baseline_prediction,
        data["target_names_tuple"],
        data["mase_scale"],
        data["meta"].get("normalization_ranges", {}),
    )
    covered = (y >= lower) & (y <= upper)
    result = {
        "schema": COMPARATOR_SCHEMA,
        "model_id": "chronos_bolt_tiny",
        "model_path": model_path,
        "split": split,
        "dataset": str(dataset_npz),
        "local_files_only": local_files_only,
        "device": resolved_device,
        "input_role": "target_history_only_univariate_batched",
        "inference_total_ms": elapsed_ms,
        "inference_ms_per_sample": elapsed_ms / max(1, sample_count),
        "metrics": metrics,
        "native_uncertainty": {
            "quantile_interval": [0.1, 0.9],
            "overall_coverage": float(np.mean(covered)),
            "mean_normalized_width": float(np.mean(upper - lower)),
        },
        "baseline": baseline_metrics,
        "baseline_delta": _baseline_delta(metrics, baseline_metrics),
        "status": "RESEARCH_COMPARATOR_HOST_ONLY",
        "production_promotion": False,
        "pi_shadow_verified": False,
        "field_generalization_verified": False,
    }
    output = Path(output_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def run_flowstate_zero_shot_v2(
    dataset_npz: str | Path,
    output_json: str | Path,
    *,
    split: str = "test",
    model_path: str = "ibm-granite/granite-timeseries-flowstate-r1",
    revision: str = "r1.1",
    device: str = "cpu",
    local_files_only: bool = True,
    scale_factor: float | None = None,
) -> dict[str, Any]:
    """Run FlowState as an explicit research comparator.

    A scale factor is required instead of guessing a 60-second mapping from
    documentation that currently publishes recommendations only for coarser
    common cadences. Short-context datasets fail before inference.
    """

    if split not in {"val", "test"}:
        raise ValueError("foundation comparator split must be val or test")
    if scale_factor is None or scale_factor <= 0:
        raise ValueError("FlowState comparator requires an explicit positive scale_factor")
    try:
        import numpy as np
        import torch
        from tsfm_public import FlowStateForPrediction
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "FlowState comparator is optional and not installed. Use an isolated "
            "research environment with the official granite-tsfm package."
        ) from exc
    data = load_forecast_dataset_v2(dataset_npz)
    x = data[f"X_{split}"].astype("float32")
    y = data[f"Y_{split}"].astype("float32")
    target_indices = [int(item) for item in data["target_indices"]]
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable")
    resolved_device = "cuda" if device == "auto" and torch.cuda.is_available() else (
        "cpu" if device == "auto" else device
    )
    predictor = FlowStateForPrediction.from_pretrained(
        model_path,
        revision=revision,
        local_files_only=local_files_only,
    ).to(resolved_device)
    predictor.eval()
    minimum_context = int(getattr(predictor.config, "min_context", 2048))
    if x.shape[1] < minimum_context:
        raise ValueError(
            f"FlowState requires at least {minimum_context} context steps; dataset has {x.shape[1]}"
        )
    context = torch.from_numpy(x).to(resolved_device).transpose(0, 1)
    started = time.perf_counter()
    with torch.no_grad():
        forecast = predictor(
            context,
            scale_factor=float(scale_factor),
            prediction_length=int(y.shape[1]),
            batch_first=False,
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    outputs = forecast.prediction_outputs.detach().cpu().numpy()
    if outputs.ndim != 4:
        raise ValueError(f"unexpected FlowState output shape: {outputs.shape}")
    quantiles = list(getattr(predictor.config, "quantiles", ()))
    median_index = min(
        range(outputs.shape[1]),
        key=lambda index: abs(float(quantiles[index]) - 0.5) if index < len(quantiles) else index,
    )
    predicted_all = outputs[:, median_index, : y.shape[1], :]
    predicted = predicted_all[:, :, target_indices]
    if predicted.shape != y.shape or not np.isfinite(predicted).all():
        raise ValueError(
            f"FlowState prediction shape/finite check failed: {predicted.shape} != {y.shape}"
        )
    metrics = regression_metrics_v2(
        y,
        predicted,
        data["target_names_tuple"],
        data["mase_scale"],
        data["meta"].get("normalization_ranges", {}),
    )
    baseline_report = evaluate_baselines_v2(dataset_npz)
    baseline_prediction = compose_selected_baseline_v2(
        data, split, baseline_report["selection"]
    )
    baseline_metrics = regression_metrics_v2(
        y,
        baseline_prediction,
        data["target_names_tuple"],
        data["mase_scale"],
        data["meta"].get("normalization_ranges", {}),
    )
    result = {
        "schema": COMPARATOR_SCHEMA,
        "model_id": "granite_flowstate_r1",
        "model_path": model_path,
        "revision": revision,
        "split": split,
        "dataset": str(dataset_npz),
        "local_files_only": local_files_only,
        "device": resolved_device,
        "scale_factor": float(scale_factor),
        "minimum_context": minimum_context,
        "parameter_count": sum(int(parameter.numel()) for parameter in predictor.parameters()),
        "inference_total_ms": elapsed_ms,
        "inference_ms_per_sample": elapsed_ms / max(1, x.shape[0]),
        "metrics": metrics,
        "baseline": baseline_metrics,
        "baseline_delta": _baseline_delta(metrics, baseline_metrics),
        "status": "RESEARCH_COMPARATOR_HOST_ONLY",
        "production_promotion": False,
        "pi_shadow_verified": False,
        "field_generalization_verified": False,
    }
    output = Path(output_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
