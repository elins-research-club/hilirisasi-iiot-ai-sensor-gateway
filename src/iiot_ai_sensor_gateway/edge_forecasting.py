from __future__ import annotations

import json
import math
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .dataset_quality import finite_or_raise
from .forecast_baselines import (
    baseline_candidates,
    compose_selected_baseline,
    select_baseline_per_target,
)

try:
    import resource as _resource  # Unix-only; unavailable on Windows
except ImportError:  # pragma: no cover - Windows path
    _resource = None

MODEL_TYPES = ("fits", "fits_official", "dlinear")
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
    finite_or_raise(
        {name: result[name] for name in ("X_train", "y_train", "X_val", "y_val", "X_test", "y_test")}
    )
    result["feature_names_tuple"] = tuple(str(item) for item in result["feature_names"])
    result["target_names_tuple"] = tuple(str(item) for item in result["target_names"])
    result["target_indices_array"] = result["target_indices"].astype("int64")
    result["normalization_ranges"] = {
        key: (float(value[0]), float(value[1]))
        for key, value in _json_array(result, "normalization_ranges_json", {}).items()
    }
    result["dataset_meta"] = _json_array(result, "dataset_meta_json", {})
    result["feature_manifest"] = _json_array(result, "feature_manifest_json", {})
    result["data_quality"] = _json_array(result, "data_quality_json", {})
    result["cadence_diagnostics"] = _json_array(result, "cadence_diagnostics_json", {})
    return result


def _mase_scale(training_actual: Any, *, lag: int = 1) -> Any:
    """Return per-target MASE denominators fitted on training observations only."""

    np = _require_numpy()
    if lag < 1:
        raise ValueError("MASE lag must be >= 1")
    if training_actual.ndim != 2:
        raise ValueError("MASE training_actual must be [samples, targets]")
    if training_actual.shape[0] <= lag:
        return np.full(training_actual.shape[1], np.nan, dtype=np.float64)
    return np.mean(
        np.abs(training_actual[lag:] - training_actual[:-lag]), axis=0
    )


def _regression_metrics(
    actual: Any,
    predicted: Any,
    target_names: tuple[str, ...],
    *,
    mase_scale: Any | None = None,
) -> dict[str, Any]:
    np = _require_numpy()
    error = predicted - actual
    mae = np.mean(np.abs(error), axis=0)
    rmse = np.sqrt(np.mean(error * error, axis=0))
    denominator = (
        np.asarray(mase_scale, dtype=np.float64)
        if mase_scale is not None
        else np.full(actual.shape[1], np.nan, dtype=np.float64)
    )
    if denominator.shape != (actual.shape[1],):
        raise ValueError("MASE scale shape does not match target count")
    mase = np.divide(
        mae,
        denominator,
        out=np.full_like(mae, np.nan, dtype=np.float64),
        where=np.isfinite(denominator) & (denominator > 0),
    )
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


def baseline_predictions(
    x: Any,
    target_indices: Any,
    seasonal_period: int = 0,
    horizon_steps: int = 1,
) -> dict[str, Any]:
    """Compatibility wrapper returning only applicable independent baselines."""

    candidates = baseline_candidates(
        x,
        target_indices,
        horizon_steps=horizon_steps,
        seasonal_period=seasonal_period,
    )
    return {
        name: item["predictions"]
        for name, item in candidates.items()
        if item["applicable"] and item["predictions"] is not None
    }


def build_edge_model(
    model_type: str,
    *,
    sequence_length: int,
    target_count: int,
    frequency_bins: int = 0,
    moving_average_kernel: int = 3,
    pred_len: int = 1,
    individual: bool = False,
):
    torch, nn, _loader, _dataset = _require_torch()
    if model_type not in MODEL_TYPES:
        raise ValueError(f"model_type must be one of {MODEL_TYPES}")
    if sequence_length < 2 or target_count < 1:
        raise ValueError("edge forecast model requires sequence_length >= 2 and target_count >= 1")
    if pred_len < 1:
        raise ValueError("pred_len must be >= 1")

    if model_type == "fits":
        selected_bins = frequency_bins or max(2, min(sequence_length // 2 + 1, 8))
        selected_bins = min(selected_bins, sequence_length // 2 + 1)

        class FitsEdgeForecaster(nn.Module):
            """Small frequency-domain residual forecaster inspired by FITS.

            Project edge variant: predicts the already-defined single-step
            horizon label from low-frequency coefficients. Not bit-for-bit
            official FITS (see model_type='fits_official').
            """

            def __init__(self) -> None:
                super().__init__()
                self.head = nn.Linear(selected_bins * target_count * 2, target_count)
                # Start near LastValue so early epochs are not catastrophic.
                nn.init.zeros_(self.head.weight)
                nn.init.zeros_(self.head.bias)

            def forward(self, target_history):
                mean = target_history.mean(dim=1, keepdim=True)
                scale = target_history.std(dim=1, keepdim=True, unbiased=False).clamp_min(1e-5)
                normalized = (target_history - mean) / scale
                spectrum = torch.fft.rfft(normalized, dim=1)[:, :selected_bins, :]
                features = torch.cat((spectrum.real, spectrum.imag), dim=1).reshape(target_history.shape[0], -1)
                residual = self.head(features)
                return target_history[:, -1, :] + residual * scale[:, 0, :]

        model = FitsEdgeForecaster()
        config = {
            "frequency_bins": selected_bins,
            "prediction_mode": "residual_last_value",
            "architecture": "fits_inspired_edge",
        }
    elif model_type == "fits_official":
        # Official FITS idea (VEWOXIC/FITS, ICLR 2024): RIN -> rFFT -> LPF ->
        # complex linear frequency upsampling -> irFFT -> reverse RIN, then
        # take the final pred_len steps. Adapted to this repo's single-horizon
        # y label (pred_len=1 by default) so it plugs into existing NPZ labels.
        cut_freq = frequency_bins or max(2, min(sequence_length // 4, 8))
        cut_freq = min(cut_freq, sequence_length // 2 + 1)
        length_ratio = float(sequence_length + pred_len) / float(sequence_length)
        upsampled_bins = max(1, int(cut_freq * length_ratio))
        full_freq_bins = (sequence_length + pred_len) // 2 + 1

        class FitsOfficialForecaster(nn.Module):
            """Official-style FITS frequency interpolation forecaster.

            Source idea: https://github.com/VEWOXIC/FITS (MIT paper code).
            Differences from research scripts: single-horizon project labels,
            no external data_provider drop_last bug path, complex Linear when
            torch.cfloat Linear is available, else Real_FITS dual-linear.
            """

            def __init__(self) -> None:
                super().__init__()
                self.seq_len = sequence_length
                self.pred_len = pred_len
                self.cut_freq = cut_freq
                self.length_ratio = length_ratio
                self.individual = individual
                self.channels = target_count
                self.use_complex = hasattr(torch, "cfloat")
                if self.individual:
                    if self.use_complex:
                        self.freq_upsampler = nn.ModuleList(
                            nn.Linear(cut_freq, upsampled_bins).to(torch.cfloat)
                            for _ in range(target_count)
                        )
                    else:
                        self.freq_real = nn.ModuleList(nn.Linear(cut_freq, upsampled_bins) for _ in range(target_count))
                        self.freq_imag = nn.ModuleList(nn.Linear(cut_freq, upsampled_bins) for _ in range(target_count))
                else:
                    if self.use_complex:
                        self.freq_upsampler = nn.Linear(cut_freq, upsampled_bins).to(torch.cfloat)
                    else:
                        self.freq_real = nn.Linear(cut_freq, upsampled_bins)
                        self.freq_imag = nn.Linear(cut_freq, upsampled_bins)

            def _upsample(self, low_specx):
                # low_specx: B, cut_freq, C
                if self.individual:
                    parts = []
                    for index in range(self.channels):
                        channel = low_specx[:, :, index]
                        if self.use_complex:
                            parts.append(self.freq_upsampler[index](channel))
                        else:
                            real = self.freq_real[index](channel.real) - self.freq_imag[index](channel.imag)
                            imag = self.freq_real[index](channel.imag) + self.freq_imag[index](channel.real)
                            parts.append(torch.complex(real, imag))
                    return torch.stack(parts, dim=2)
                if self.use_complex:
                    return self.freq_upsampler(low_specx.permute(0, 2, 1)).permute(0, 2, 1)
                real = self.freq_real(low_specx.real.permute(0, 2, 1)) - self.freq_imag(low_specx.imag.permute(0, 2, 1))
                imag = self.freq_real(low_specx.imag.permute(0, 2, 1)) + self.freq_imag(low_specx.real.permute(0, 2, 1))
                return torch.complex(real, imag).permute(0, 2, 1)

            def forward(self, target_history):
                # RIN
                x_mean = target_history.mean(dim=1, keepdim=True)
                x_var = target_history.var(dim=1, keepdim=True, unbiased=False) + 1e-5
                normalized = (target_history - x_mean) / torch.sqrt(x_var)

                spectrum = torch.fft.rfft(normalized, dim=1)
                low_specx = spectrum[:, : self.cut_freq, :].clone()
                low_specxy_ = self._upsample(low_specx)

                low_specxy = torch.zeros(
                    (
                        low_specxy_.size(0),
                        full_freq_bins,
                        low_specxy_.size(2),
                    ),
                    dtype=low_specxy_.dtype,
                    device=low_specxy_.device,
                )
                low_specxy[:, : low_specxy_.size(1), :] = low_specxy_
                reconstructed = torch.fft.irfft(low_specxy, n=self.seq_len + self.pred_len, dim=1)
                reconstructed = reconstructed * self.length_ratio
                reconstructed = reconstructed * torch.sqrt(x_var) + x_mean
                # Project label is single horizon vector -> take last pred step(s)
                if self.pred_len == 1:
                    return reconstructed[:, -1, :]
                return reconstructed[:, -self.pred_len :, :].mean(dim=1)

        model = FitsOfficialForecaster()
        config = {
            "frequency_bins": cut_freq,
            "cut_freq": cut_freq,
            "pred_len": pred_len,
            "individual": individual,
            "length_ratio": length_ratio,
            "prediction_mode": "frequency_interpolation_rin",
            "architecture": "fits_official_style",
            "paper": "FITS ICLR 2024 / VEWOXIC/FITS",
        }
    else:
        kernel = max(1, min(moving_average_kernel, sequence_length))
        if kernel % 2 == 0:
            kernel = max(1, kernel - 1)

        class DLinearForecaster(nn.Module):
            """DLinear-style trend/seasonal residual forecaster.

            Predicts a residual on top of the last observed value. Absolute
            linear heads on short normalized windows are unstable under short
            smoke training and can explode outside the normalized range.
            """

            def __init__(self) -> None:
                super().__init__()
                self.seasonal = nn.ModuleList(nn.Linear(sequence_length, 1) for _ in range(target_count))
                self.trend = nn.ModuleList(nn.Linear(sequence_length, 1) for _ in range(target_count))
                self.pool = nn.AvgPool1d(kernel_size=kernel, stride=1, padding=kernel // 2)
                for layer in list(self.seasonal) + list(self.trend):
                    nn.init.zeros_(layer.weight)
                    nn.init.zeros_(layer.bias)

            def forward(self, target_history):
                # input B,L,C -> pool B,C,L
                trend = self.pool(target_history.transpose(1, 2)).transpose(1, 2)
                # AvgPool1d padding can extend length by 1 for even kernels; trim.
                if trend.shape[1] != target_history.shape[1]:
                    trend = trend[:, : target_history.shape[1], :]
                seasonal = target_history - trend
                residuals = []
                for index in range(target_count):
                    residuals.append(
                        self.seasonal[index](seasonal[:, :, index])
                        + self.trend[index](trend[:, :, index])
                    )
                residual = torch.cat(residuals, dim=1)
                return target_history[:, -1, :] + residual

        model = DLinearForecaster()
        config = {
            "moving_average_kernel": kernel,
            "prediction_mode": "residual_last_value",
            "architecture": "dlinear_residual",
        }
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
    weight_decay: float = 1e-4,
    grad_clip_norm: float = 1.0,
    pred_len: int = 1,
    individual: bool = False,
) -> dict[str, Any]:
    if epochs < 1 or batch_size < 1 or learning_rate <= 0 or patience < 1:
        raise ValueError("epochs, batch_size, learning_rate, and patience must be positive")
    if weight_decay < 0 or grad_clip_norm < 0:
        raise ValueError("weight_decay and grad_clip_norm must be non-negative")
    np = _require_numpy()
    torch, nn, DataLoader, TensorDataset = _require_torch()
    data = _load_dataset(dataset_npz)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if device in ("auto", "cuda") and torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
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
        pred_len=pred_len,
        individual=individual,
    )
    model = model.to(resolved_device)
    # Prefer slightly higher LR for residual linear heads; FITS stays conservative.
    if model_type in ("fits", "fits_official"):
        resolved_lr = learning_rate
    else:
        resolved_lr = max(learning_rate, 0.01)
    optimizer = torch.optim.AdamW(model.parameters(), lr=resolved_lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=max(2, patience // 2)
    )
    criterion = nn.SmoothL1Loss(beta=0.01)
    train_dataset = TensorDataset(
        torch.from_numpy(_select_targets(X_train, target_indices).astype("float32")),
        torch.from_numpy(y_train),
    )
    val_dataset = TensorDataset(
        torch.from_numpy(_select_targets(X_val, target_indices).astype("float32")),
        torch.from_numpy(y_val),
    )
    train_loader = DataLoader(train_dataset, batch_size=min(batch_size, max(1, len(train_dataset))), shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=min(batch_size, max(1, len(val_dataset))), shuffle=False)

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
            if grad_clip_norm > 0:
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip_norm)
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
        scheduler.step(val_loss)
        current_lr = float(optimizer.param_groups[0]["lr"])
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss,
                "val_loss": val_loss,
                "learning_rate": current_lr,
            }
        )
        if val_loss < best_loss - 1e-8:
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
        "model_version": f"{model_type}_edge_v3" if model_type == "fits_official" else f"{model_type}_edge_v2",
        "status": "EXPERIMENTAL",
        "created_at": _utc_now(),
        "input_length": int(X_train.shape[1]),
        "target_count": int(y_train.shape[1]),
        "target_names": list(data["target_names_tuple"]),
        "target_indices": [int(item) for item in target_indices],
        "feature_names": list(data["feature_names_tuple"]),
        "feature_schema_sha256": str(data["feature_schema_sha256"][0]) if "feature_schema_sha256" in data else None,
        "feature_manifest": data["feature_manifest"],
        "data_quality": data["data_quality"],
        "cadence_diagnostics": data["cadence_diagnostics"],
        "horizon_steps": int(data["horizon_steps"][0]) if "horizon_steps" in data else 1,
        "horizon_duration_seconds": float(data["horizon_duration_seconds"][0]) if "horizon_duration_seconds" in data else None,
        "model_config": {
            **model_config,
            "weight_decay": weight_decay,
            "grad_clip_norm": grad_clip_norm,
            "resolved_learning_rate": resolved_lr,
            "loss": "smooth_l1_beta_0.01",
            "pred_len": pred_len,
            "individual": individual,
        },
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
            "process_max_rss_kib": (
                int(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss)
                if _resource is not None
                else None
            ),
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
    model_config = checkpoint["model_config"] if isinstance(checkpoint["model_config"], dict) else {}
    model, _config = build_edge_model(
        str(checkpoint["model_type"]),
        sequence_length=int(checkpoint["input_length"]),
        target_count=int(checkpoint["target_count"]),
        frequency_bins=int(model_config.get("frequency_bins", model_config.get("cut_freq", 0))),
        moving_average_kernel=int(model_config.get("moving_average_kernel", 3)),
        pred_len=int(model_config.get("pred_len", 1)),
        individual=bool(model_config.get("individual", False)),
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
    if tuple(checkpoint.get("feature_names", ())) != data["feature_names_tuple"]:
        raise ValueError("edge checkpoint feature schema does not match dataset")
    stored_schema_hash = checkpoint.get("feature_schema_sha256")
    dataset_schema_hash = (
        str(data["feature_schema_sha256"][0]) if "feature_schema_sha256" in data else None
    )
    if stored_schema_hash and dataset_schema_hash and stored_schema_hash != dataset_schema_hash:
        raise ValueError("edge checkpoint feature schema hash does not match dataset")
    if tuple(checkpoint.get("target_names", ())) != target_names:
        raise ValueError("edge checkpoint target schema does not match dataset")
    horizon_steps = int(data["horizon_steps"][0]) if "horizon_steps" in data else 1
    result: dict[str, Any] = {
        "schema": METRICS_SCHEMA,
        "created_at": _utc_now(),
        "model_path": str(model_path),
        "model_type": checkpoint["model_type"],
        "model_version": checkpoint["model_version"],
        "device": resolved_device,
        "target_names": list(target_names),
        "feature_names": list(data["feature_names_tuple"]),
        "feature_manifest": data["feature_manifest"],
        "data_quality": data["data_quality"],
        "cadence_diagnostics": data["cadence_diagnostics"],
        "horizon_steps": horizon_steps,
        "horizon_duration_seconds": float(data["horizon_duration_seconds"][0])
        if "horizon_duration_seconds" in data
        else None,
        "seasonal_period": seasonal_period,
        "baseline_selection_split": "val",
        "splits": {},
    }
    mase_lag = seasonal_period if seasonal_period > 0 else 1
    train_actual = data["y_train"].astype("float32")
    train_actual_denorm = _denormalize(
        train_actual, target_names, data["normalization_ranges"]
    )
    normalized_mase_scale = _mase_scale(train_actual, lag=mase_lag)
    denormalized_mase_scale = _mase_scale(train_actual_denorm, lag=mase_lag)
    result["mase_scale"] = {
        "source_split": "train",
        "lag": mase_lag,
        "normalized": [
            None if not math.isfinite(float(value)) else float(value)
            for value in normalized_mase_scale
        ],
        "denormalized": [
            None if not math.isfinite(float(value)) else float(value)
            for value in denormalized_mase_scale
        ],
    }
    split_candidates: dict[str, dict[str, dict[str, Any]]] = {}
    split_actual: dict[str, Any] = {}
    split_predictions: dict[str, Any] = {}
    for split in ("train", "val", "test"):
        x = data[f"X_{split}"].astype("float32")
        y = data[f"y_{split}"].astype("float32")
        started = time.perf_counter()
        prediction = _predict_batches(model, x, target_indices, resolved_device, batch_size)
        inference_ms = (time.perf_counter() - started) * 1000.0
        if prediction.shape[0] != x.shape[0]:
            raise RuntimeError("edge evaluator dropped or duplicated samples")
        candidates = baseline_candidates(
            x,
            target_indices,
            horizon_steps=horizon_steps,
            seasonal_period=seasonal_period,
        )
        split_candidates[split] = candidates
        split_actual[split] = y
        split_predictions[split] = prediction
        metrics = _regression_metrics(
            y, prediction, target_names, mase_scale=normalized_mase_scale
        )
        y_denorm = _denormalize(y, target_names, data["normalization_ranges"])
        pred_denorm = _denormalize(prediction, target_names, data["normalization_ranges"])
        metrics["denormalized"] = _regression_metrics(
            y_denorm,
            pred_denorm,
            target_names,
            mase_scale=denormalized_mase_scale,
        )
        baseline_metrics: dict[str, Any] = {}
        for name, item in candidates.items():
            if not item["applicable"] or item["predictions"] is None:
                baseline_metrics[name] = {
                    "applicable": False,
                    "reason": item["reason"],
                }
                continue
            values = item["predictions"]
            candidate_metrics = _regression_metrics(
                y, values, target_names, mase_scale=normalized_mase_scale
            )
            candidate_metrics["denormalized"] = _regression_metrics(
                y_denorm,
                _denormalize(values, target_names, data["normalization_ranges"]),
                target_names,
                mase_scale=denormalized_mase_scale,
            )
            baseline_metrics[name] = {
                "applicable": True,
                "reason": None,
                **candidate_metrics,
            }
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

    selection = select_baseline_per_target(
        split_actual["val"],
        split_candidates["val"],
        target_names,
    )
    result["baseline_selection"] = selection
    for split in ("train", "val", "test"):
        selected_prediction = compose_selected_baseline(
            split_candidates[split], selection, target_names
        )
        selected_metrics = _regression_metrics(
            split_actual[split],
            selected_prediction,
            target_names,
            mase_scale=normalized_mase_scale,
        )
        selected_metrics["denormalized"] = _regression_metrics(
            _denormalize(
                split_actual[split], target_names, data["normalization_ranges"]
            ),
            _denormalize(
                selected_prediction, target_names, data["normalization_ranges"]
            ),
            target_names,
            mase_scale=denormalized_mase_scale,
        )
        result["splits"][split]["validation_selected_baseline"] = selected_metrics

    test = result["splits"]["test"]
    baseline_rmse = test["validation_selected_baseline"]["overall_rmse"]
    model_rmse = test["model"]["overall_rmse"]
    data_quality = data["data_quality"] or data["dataset_meta"].get("data_quality", {})
    effective_targets = tuple(data_quality.get("effective_target_names", target_names))
    per_target_wins = sum(
        test["model"]["per_target"][name]["rmse"]
        < test["validation_selected_baseline"]["per_target"][name]["rmse"]
        for name in effective_targets
    )
    quality_passed = bool(
        data_quality.get("status") == "PASS"
        and len(effective_targets) == len(target_names)
    )
    baseline_passed = bool(
        baseline_rmse > 0
        and model_rmse < baseline_rmse
        and per_target_wins >= math.ceil(max(1, len(effective_targets)) / 2)
    )
    result["baseline_gate"] = {
        "best_baseline": "validation_selected_per_target",
        "selected_by_target": selection["selected_by_target"],
        "model_rmse": model_rmse,
        "baseline_rmse": baseline_rmse,
        "rmse_skill_score": None
        if baseline_rmse == 0
        else 1.0 - model_rmse / baseline_rmse,
        "per_target_wins": per_target_wins,
        "effective_target_count": len(effective_targets),
        "target_count": len(target_names),
        "baseline_passed": baseline_passed,
        "data_quality_passed": quality_passed,
        "passed": bool(baseline_passed and quality_passed),
    }
    # Compatibility aliases for older bake-off consumers / CLI greps.
    data_status = str(data_quality.get("status", "UNKNOWN"))
    if baseline_passed:
        baseline_status = "BEATS_BASELINE"
    elif model_rmse < baseline_rmse:
        baseline_status = "MIXED"
    else:
        baseline_status = "UNDER_BASELINE"
    result["data_status"] = data_status
    result["baseline_comparison_status"] = baseline_status
    result["status"] = (
        "PASS" if result["baseline_gate"]["passed"] else "FAIL_OR_EXPERIMENTAL"
    )
    result["model_readiness"] = (
        "PROMISING" if result["baseline_gate"]["passed"] else "EXPERIMENTAL"
    )
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
    if tuple(checkpoint.get("feature_names", ())) != data["feature_names_tuple"]:
        raise ValueError("edge checkpoint feature schema does not match dataset")
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
                "model_readiness": str(checkpoint.get("status", "EXPERIMENTAL")),
                "prediction_values": {
                    name: float(values[target_index])
                    for target_index, name in enumerate(data["target_names_tuple"])
                },
            }
            file.write(json.dumps(record, separators=(",", ":")) + "\n")
    return output
