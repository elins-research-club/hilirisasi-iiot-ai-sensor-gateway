from __future__ import annotations

import json
import math
import random
import resource
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL_TYPES = ("fits", "dlinear")
MODEL_SCHEMA = "iiot.ai_sensor.edge_forecast_model.v1"
METRICS_SCHEMA = "iiot.ai_sensor.edge_forecast_metrics.v1"


def _require_numpy():
    import numpy as np

    return np


def _require_torch():
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    return torch, nn, DataLoader, TensorDataset


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _device_name(requested: str) -> str:
    torch, _nn, _loader, _dataset = _require_torch()
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return requested


def _json_array(data: Any, name: str, default: Any) -> Any:
    if name not in data:
        return default
    value = data[name]
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _load_dataset(path: str | Path) -> dict[str, Any]:
    np = _require_numpy()
    data = np.load(path, allow_pickle=False)
    required = {
        "X_train",
        "y_train",
        "X_val",
        "y_val",
        "X_test",
        "y_test",
        "feature_names",
        "target_names",
        "target_indices",
    }
    missing = sorted(required.difference(data.files))
    if missing:
        raise ValueError(f"forecast dataset missing arrays: {missing}")
    result = {name: data[name] for name in data.files}
    for name in ("X_train", "y_train", "X_val", "y_val", "X_test", "y_test"):
        array = result[name]
        if not np.isfinite(array).all():
            raise ValueError(f"forecast dataset contains non-finite values in {name}")
    result["feature_names_tuple"] = tuple(str(item) for item in result["feature_names"])
    result["target_names_tuple"] = tuple(str(item) for item in result["target_names"])
    result["target_indices_array"] = result["target_indices"].astype("int64")
    result["normalization_ranges"] = {
        key: (float(value[0]), float(value[1]))
        for key, value in _json_array(result, "normalization_ranges_json", {}).items()
    }
    result["dataset_meta"] = _json_array(result, "dataset_meta_json", {})
    return result


def _regression_metrics(actual: Any, predicted: Any, target_names: tuple[str, ...]) -> dict[str, Any]:
    np = _require_numpy()
    error = predicted - actual
    mae = np.mean(np.abs(error), axis=0)
    rmse = np.sqrt(np.mean(error * error, axis=0))
    denominator = np.mean(np.abs(np.diff(actual, axis=0)), axis=0) if actual.shape[0] > 1 else np.zeros(actual.shape[1])
    mase = np.divide(mae, denominator, out=np.full_like(mae, np.nan), where=denominator > 0)
    return {
        "overall_mae": float(np.mean(mae)),
        "overall_rmse": float(np.mean(rmse)),
        "overall_mase": None if np.isnan(mase).all() else float(np.nanmean(mase)),
        "per_target": {
            name: {
                "mae": float(mae[index]),
                "rmse": float(rmse[index]),
                "mase": None if math.isnan(float(mase[index])) else float(mase[index]),
            }
            for index, name in enumerate(target_names)
        },
    }


def _denormalize(values: Any, target_names: tuple[str, ...], ranges: dict[str, tuple[float, float]]) -> Any:
    output = values.copy()
    for index, name in enumerate(target_names):
        low, high = ranges.get(name, (0.0, 1.0))
        output[:, index] = low + output[:, index] * (high - low)
    return output


def baseline_predictions(x: Any, target_indices: Any, seasonal_period: int = 0) -> dict[str, Any]:
    last = x[:, -1, target_indices]
    if seasonal_period > 0 and x.shape[1] > seasonal_period:
        seasonal = x[:, -(seasonal_period + 1), target_indices]
    else:
        seasonal = last.copy()
    return {"last_value": last, "seasonal_naive": seasonal}


def build_edge_model(
    model_type: str,
    *,
    sequence_length: int,
    target_count: int,
    frequency_bins: int = 0,
    moving_average_kernel: int = 3,
):
    torch, nn, _loader, _dataset = _require_torch()
    if model_type not in MODEL_TYPES:
        raise ValueError(f"model_type must be one of {MODEL_TYPES}")
    if sequence_length < 2 or target_count < 1:
        raise ValueError("edge forecast model requires sequence_length >= 2 and target_count >= 1")

    if model_type == "fits":
        selected_bins = frequency_bins or max(2, min(sequence_length // 2 + 1, 8))
        selected_bins = min(selected_bins, sequence_length // 2 + 1)

        class FitsEdgeForecaster(nn.Module):
            """Small frequency-domain residual forecaster inspired by FITS.

            This project variant predicts the already-defined horizon label from
            low-frequency real/imaginary coefficients. It is intentionally named
            and documented as a FITS-inspired edge variant, not a bit-for-bit
            reproduction of the research repository.
            """

            def __init__(self) -> None:
                super().__init__()
                self.head = nn.Linear(selected_bins * target_count * 2, target_count)

            def forward(self, target_history):
                mean = target_history.mean(dim=1, keepdim=True)
                scale = target_history.std(dim=1, keepdim=True, unbiased=False).clamp_min(1e-5)
                normalized = (target_history - mean) / scale
                spectrum = torch.fft.rfft(normalized, dim=1)[:, :selected_bins, :]
                features = torch.cat((spectrum.real, spectrum.imag), dim=1).reshape(target_history.shape[0], -1)
                residual = self.head(features)
                return target_history[:, -1, :] + residual * scale[:, 0, :]

        model = FitsEdgeForecaster()
        config = {"frequency_bins": selected_bins}
    else:
        kernel = max(1, min(moving_average_kernel, sequence_length))
        if kernel % 2 == 0:
            kernel = max(1, kernel - 1)

        class DLinearForecaster(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.seasonal = nn.ModuleList(nn.Linear(sequence_length, 1) for _ in range(target_count))
                self.trend = nn.ModuleList(nn.Linear(sequence_length, 1) for _ in range(target_count))
                self.pool = nn.AvgPool1d(kernel_size=kernel, stride=1, padding=kernel // 2)

            def forward(self, target_history):
                # input B,L,C -> pool B,C,L
                trend = self.pool(target_history.transpose(1, 2)).transpose(1, 2)
                seasonal = target_history - trend
                outputs = []
                for index in range(target_count):
                    outputs.append(
                        self.seasonal[index](seasonal[:, :, index])
                        + self.trend[index](trend[:, :, index])
                    )
                return torch.cat(outputs, dim=1)

        model = DLinearForecaster()
        config = {"moving_average_kernel": kernel}
    return model, config


def _select_targets(x: Any, target_indices: Any) -> Any:
    return x[:, :, target_indices]


def _predict_batches(model: Any, x: Any, target_indices: Any, device: str, batch_size: int) -> Any:
    np = _require_numpy()
    torch, _nn, _loader, _dataset = _require_torch()
    predictions = []
    model.eval()
    with torch.no_grad():
        for start in range(0, x.shape[0], batch_size):
            history = torch.from_numpy(
                _select_targets(x[start : start + batch_size], target_indices).astype("float32")
            ).to(device)
            predictions.append(model(history).detach().cpu().numpy())
    return np.concatenate(predictions, axis=0) if predictions else np.empty((0, len(target_indices)), dtype=np.float32)


def train_edge_forecast(
    dataset_npz: str | Path,
    output_dir: str | Path,
    *,
    model_type: str = "fits",
    epochs: int = 30,
    batch_size: int = 64,
    learning_rate: float = 0.001,
    patience: int = 5,
    seed: int = 42,
    device: str = "auto",
    frequency_bins: int = 0,
    moving_average_kernel: int = 3,
) -> dict[str, Any]:
    if epochs < 1 or batch_size < 1 or learning_rate <= 0 or patience < 1:
        raise ValueError("epochs, batch_size, learning_rate, and patience must be positive")
    np = _require_numpy()
    torch, nn, DataLoader, TensorDataset = _require_torch()
    data = _load_dataset(dataset_npz)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    resolved_device = _device_name(device)
    target_indices = data["target_indices_array"]
    X_train = data["X_train"].astype("float32")
    X_val = data["X_val"].astype("float32")
    y_train = data["y_train"].astype("float32")
    y_val = data["y_val"].astype("float32")
    model, model_config = build_edge_model(
        model_type,
        sequence_length=int(X_train.shape[1]),
        target_count=int(y_train.shape[1]),
        frequency_bins=frequency_bins,
        moving_average_kernel=moving_average_kernel,
    )
    model = model.to(resolved_device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    criterion = nn.MSELoss()
    train_dataset = TensorDataset(
        torch.from_numpy(_select_targets(X_train, target_indices).astype("float32")),
        torch.from_numpy(y_train),
    )
    val_dataset = TensorDataset(
        torch.from_numpy(_select_targets(X_val, target_indices).astype("float32")),
        torch.from_numpy(y_val),
    )
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    best_loss = math.inf
    best_state = None
    best_epoch = 0
    wait = 0
    history: list[dict[str, float]] = []
    started = time.perf_counter()
    for epoch in range(1, epochs + 1):
        model.train()
        train_losses = []
        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(resolved_device)
            batch_y = batch_y.to(resolved_device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()
            train_losses.append(float(loss.detach().cpu().item()))
        model.eval()
        val_losses = []
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                val_losses.append(
                    float(
                        criterion(model(batch_x.to(resolved_device)), batch_y.to(resolved_device))
                        .detach()
                        .cpu()
                        .item()
                    )
                )
        train_loss = float(sum(train_losses) / max(1, len(train_losses)))
        val_loss = float(sum(val_losses) / max(1, len(val_losses)))
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        if val_loss < best_loss:
            best_loss = val_loss
            best_epoch = epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                break
    if best_state is None:
        raise RuntimeError("edge model training did not produce a checkpoint")
    model.load_state_dict(best_state, strict=True)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / "model.pt"
    metadata = {
        "schema": MODEL_SCHEMA,
        "model_type": model_type,
        "model_version": f"{model_type}_edge_v1",
        "status": "EXPERIMENTAL",
        "created_at": _utc_now(),
        "input_length": int(X_train.shape[1]),
        "target_count": int(y_train.shape[1]),
        "target_names": list(data["target_names_tuple"]),
        "target_indices": [int(item) for item in target_indices],
        "feature_names": list(data["feature_names_tuple"]),
        "model_config": model_config,
        "parameter_count": int(parameter_count),
        "dataset_ref": str(dataset_npz),
        "dataset_meta": data["dataset_meta"],
        "normalization_ranges": {
            key: list(value) for key, value in data["normalization_ranges"].items()
        },
    }
    torch.save({"state_dict": best_state, **metadata}, model_path)
    result = {
        **metadata,
        "model_path": str(model_path),
        "device": resolved_device,
        "epochs_requested": epochs,
        "epochs_ran": len(history),
        "best_epoch": best_epoch,
        "best_val_loss": best_loss,
        "history": history,
        "resource_measurement": {
            "hardware_label": "current_host",
            "training_wall_ms": elapsed_ms,
            "process_max_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "raspberry_pi_claim": None,
        },
    }
    (output / "training.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def load_edge_model(model_path: str | Path, device: str = "auto"):
    torch, _nn, _loader, _dataset = _require_torch()
    resolved_device = _device_name(device)
    try:
        checkpoint = torch.load(model_path, map_location=resolved_device, weights_only=True)
    except Exception as exc:
        raise ValueError(f"unsafe or invalid edge checkpoint rejected: {model_path}") from exc
    required = {
        "schema",
        "state_dict",
        "model_type",
        "input_length",
        "target_count",
        "target_names",
        "target_indices",
        "model_config",
    }
    if not isinstance(checkpoint, dict) or not required.issubset(checkpoint):
        missing = sorted(required.difference(checkpoint if isinstance(checkpoint, dict) else {}))
        raise ValueError(f"edge checkpoint missing required keys: {missing}")
    if checkpoint["schema"] != MODEL_SCHEMA:
        raise ValueError("unsupported edge checkpoint schema")
    model, _config = build_edge_model(
        str(checkpoint["model_type"]),
        sequence_length=int(checkpoint["input_length"]),
        target_count=int(checkpoint["target_count"]),
        frequency_bins=int(checkpoint["model_config"].get("frequency_bins", 0)),
        moving_average_kernel=int(checkpoint["model_config"].get("moving_average_kernel", 3)),
    )
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    model = model.to(resolved_device).eval()
    return model, checkpoint, resolved_device


def evaluate_edge_forecast(
    dataset_npz: str | Path,
    model_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    seasonal_period: int = 0,
    batch_size: int = 1024,
    device: str = "auto",
) -> dict[str, Any]:
    if batch_size < 1 or seasonal_period < 0:
        raise ValueError("batch_size must be positive and seasonal_period non-negative")
    data = _load_dataset(dataset_npz)
    model, checkpoint, resolved_device = load_edge_model(model_path, device)
    target_names = data["target_names_tuple"]
    target_indices = data["target_indices_array"]
    result: dict[str, Any] = {
        "schema": METRICS_SCHEMA,
        "created_at": _utc_now(),
        "model_path": str(model_path),
        "model_type": checkpoint["model_type"],
        "model_version": checkpoint["model_version"],
        "device": resolved_device,
        "target_names": list(target_names),
        "seasonal_period": seasonal_period,
        "splits": {},
    }
    for split in ("train", "val", "test"):
        x = data[f"X_{split}"].astype("float32")
        y = data[f"y_{split}"].astype("float32")
        started = time.perf_counter()
        prediction = _predict_batches(model, x, target_indices, resolved_device, batch_size)
        inference_ms = (time.perf_counter() - started) * 1000.0
        baselines = baseline_predictions(x, target_indices, seasonal_period)
        metrics = _regression_metrics(y, prediction, target_names)
        baseline_metrics = {
            name: _regression_metrics(y, values, target_names) for name, values in baselines.items()
        }
        y_denorm = _denormalize(y, target_names, data["normalization_ranges"])
        pred_denorm = _denormalize(prediction, target_names, data["normalization_ranges"])
        metrics["denormalized"] = _regression_metrics(y_denorm, pred_denorm, target_names)
        for name, values in baselines.items():
            baseline_metrics[name]["denormalized"] = _regression_metrics(
                y_denorm,
                _denormalize(values, target_names, data["normalization_ranges"]),
                target_names,
            )
        result["splits"][split] = {
            "samples": int(x.shape[0]),
            "model": metrics,
            "baselines": baseline_metrics,
            "resource_measurement": {
                "hardware_label": "current_host",
                "total_inference_ms": inference_ms,
                "mean_inference_ms_per_sample": inference_ms / max(1, int(x.shape[0])),
                "raspberry_pi_claim": None,
            },
        }
    test = result["splits"]["test"]
    best_baseline_name = min(
        test["baselines"], key=lambda name: test["baselines"][name]["overall_rmse"]
    )
    best_baseline_rmse = test["baselines"][best_baseline_name]["overall_rmse"]
    model_rmse = test["model"]["overall_rmse"]
    per_target_wins = sum(
        test["model"]["per_target"][name]["rmse"]
        < test["baselines"][best_baseline_name]["per_target"][name]["rmse"]
        for name in target_names
    )
    result["baseline_gate"] = {
        "best_baseline": best_baseline_name,
        "model_rmse": model_rmse,
        "baseline_rmse": best_baseline_rmse,
        "rmse_skill_score": None
        if best_baseline_rmse == 0
        else 1.0 - model_rmse / best_baseline_rmse,
        "per_target_wins": per_target_wins,
        "target_count": len(target_names),
        "passed": bool(model_rmse < best_baseline_rmse and per_target_wins >= math.ceil(len(target_names) / 2)),
    }
    result["model_readiness"] = "PROMISING" if result["baseline_gate"]["passed"] else "EXPERIMENTAL"
    output = Path(output_dir) if output_dir else Path(model_path).parent
    output.mkdir(parents=True, exist_ok=True)
    metrics_path = output / "metrics.json"
    result["metrics_ref"] = str(metrics_path)
    metrics_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def predict_edge_forecast(
    dataset_npz: str | Path,
    model_path: str | Path,
    output_jsonl: str | Path,
    *,
    split: str = "test",
    max_samples: int = 0,
    device: str = "auto",
) -> Path:
    if split not in {"train", "val", "test"}:
        raise ValueError("split must be train, val, or test")
    if max_samples < 0:
        raise ValueError("max_samples must be non-negative")
    data = _load_dataset(dataset_npz)
    model, checkpoint, resolved_device = load_edge_model(model_path, device)
    x = data[f"X_{split}"].astype("float32")
    if max_samples:
        x = x[:max_samples]
    prediction = _predict_batches(model, x, data["target_indices_array"], resolved_device, 1024)
    denormalized = _denormalize(prediction, data["target_names_tuple"], data["normalization_ranges"])
    output = Path(output_jsonl)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        for index, values in enumerate(denormalized):
            record = {
                "schema": "iiot.ai_sensor.edge_forecast_prediction.v1",
                "sample_index": index,
                "split": split,
                "model_type": checkpoint["model_type"],
                "model_version": checkpoint["model_version"],
                "model_readiness": "EXPERIMENTAL",
                "prediction_values": {
                    name: float(values[target_index])
                    for target_index, name in enumerate(data["target_names_tuple"])
                },
            }
            file.write(json.dumps(record, separators=(",", ":")) + "\n")
    return output
