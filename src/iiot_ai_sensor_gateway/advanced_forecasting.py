from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .forecast_evaluator_v2 import (
    METRICS_SCHEMA,
    calibrate_conformal_radius_v2,
    compose_selected_baseline_v2,
    evaluate_baselines_v2,
    interval_metrics_v2,
    load_forecast_dataset_v2,
    regression_metrics_v2,
)

MODEL_TYPES = ("ridge", "elasticnet", "nlinear", "tsmixer_lite")
MODEL_SCHEMA = "iiot.ai_sensor.local_forecast_model.v2"


def _np():
    import numpy as np

    return np


def _torch():
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    return torch, nn, DataLoader, TensorDataset


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _device_name(requested: str) -> str:
    torch, _nn, _loader, _dataset = _torch()
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("CUDA requested but torch.cuda.is_available() is false")
    return requested


def _flatten_y(y: Any) -> Any:
    return y.reshape(y.shape[0], -1)


def _soft_threshold(value: Any, threshold: float) -> Any:
    np = _np()
    return np.sign(value) * np.maximum(np.abs(value) - threshold, 0.0)


def _ridge_fit(x: Any, y: Any, *, alpha: float) -> dict[str, Any]:
    np = _np()
    if alpha < 0:
        raise ValueError("ridge alpha must be non-negative")
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    x_mean = np.mean(x, axis=0)
    x_std = np.std(x, axis=0)
    x_std = np.where(x_std > 1e-12, x_std, 1.0)
    y_mean = np.mean(y, axis=0)
    xs = (x - x_mean) / x_std
    yc = y - y_mean
    gram = (xs.T @ xs) / max(1, xs.shape[0])
    rhs = (xs.T @ yc) / max(1, xs.shape[0])
    regularized = gram + alpha * np.eye(gram.shape[0], dtype=np.float64)
    try:
        weights = np.linalg.solve(regularized, rhs)
    except np.linalg.LinAlgError:
        weights = np.linalg.lstsq(regularized, rhs, rcond=None)[0]
    return {
        "weights": weights,
        "x_mean": x_mean,
        "x_std": x_std,
        "y_mean": y_mean,
    }


def _linear_predict(state: dict[str, Any], x: Any) -> Any:
    np = _np()
    xs = (np.asarray(x, dtype=np.float64) - state["x_mean"]) / state["x_std"]
    return xs @ state["weights"] + state["y_mean"]


def _spectral_lipschitz(x: Any, *, iterations: int = 25) -> float:
    np = _np()
    if x.shape[1] == 0:
        return 1.0
    vector = np.ones((x.shape[1],), dtype=np.float64)
    vector /= max(float(np.linalg.norm(vector)), 1e-12)
    for _ in range(iterations):
        projected = x.T @ (x @ vector)
        norm = float(np.linalg.norm(projected))
        if norm <= 1e-12:
            return 1.0
        vector = projected / norm
    singular_sq = float(np.linalg.norm(x @ vector) ** 2)
    return max(singular_sq / max(1, x.shape[0]), 1e-12)


def _elasticnet_fit(
    x: Any,
    y: Any,
    *,
    alpha: float,
    l1_ratio: float,
    max_iter: int,
    tolerance: float,
) -> dict[str, Any]:
    np = _np()
    if alpha <= 0:
        raise ValueError("elasticnet alpha must be positive")
    if not 0.0 <= l1_ratio <= 1.0:
        raise ValueError("elasticnet l1_ratio must be in [0,1]")
    if max_iter < 1 or tolerance <= 0:
        raise ValueError("elasticnet max_iter and tolerance must be positive")
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    x_mean = np.mean(x, axis=0)
    x_std = np.std(x, axis=0)
    x_std = np.where(x_std > 1e-12, x_std, 1.0)
    y_mean = np.mean(y, axis=0)
    xs = (x - x_mean) / x_std
    yc = y - y_mean
    weights = np.zeros((xs.shape[1], yc.shape[1]), dtype=np.float64)
    accelerated = weights.copy()
    momentum = 1.0
    l2 = alpha * (1.0 - l1_ratio)
    l1 = alpha * l1_ratio
    lipschitz = _spectral_lipschitz(xs) + l2
    step = 1.0 / lipschitz
    iterations_ran = 0
    for iteration in range(1, max_iter + 1):
        gradient = (xs.T @ (xs @ accelerated - yc)) / max(1, xs.shape[0]) + l2 * accelerated
        updated = _soft_threshold(accelerated - step * gradient, step * l1)
        delta = float(np.max(np.abs(updated - weights))) if updated.size else 0.0
        next_momentum = (1.0 + math.sqrt(1.0 + 4.0 * momentum * momentum)) / 2.0
        accelerated = updated + ((momentum - 1.0) / next_momentum) * (updated - weights)
        weights = updated
        momentum = next_momentum
        iterations_ran = iteration
        if delta <= tolerance:
            break
    return {
        "weights": weights,
        "x_mean": x_mean,
        "x_std": x_std,
        "y_mean": y_mean,
        "iterations_ran": np.asarray([iterations_ran], dtype=np.int64),
        "lipschitz": np.asarray([lipschitz], dtype=np.float64),
    }


def _nlinear_fit(
    x: Any,
    y: Any,
    target_indices: Any,
    *,
    alpha: float,
) -> dict[str, Any]:
    np = _np()
    if alpha < 0:
        raise ValueError("NLinear alpha must be non-negative")
    target_indices = np.asarray(target_indices, dtype=np.int64)
    horizon = y.shape[1]
    target_count = y.shape[2]
    sequence_length = x.shape[1]
    weights = np.zeros((target_count, sequence_length, horizon), dtype=np.float64)
    bias = np.zeros((target_count, horizon), dtype=np.float64)
    for target in range(target_count):
        history = np.asarray(x[:, :, target_indices[target]], dtype=np.float64)
        last = history[:, -1:]
        features = history - last
        residual_target = np.asarray(y[:, :, target], dtype=np.float64) - last
        feature_mean = np.mean(features, axis=0)
        target_mean = np.mean(residual_target, axis=0)
        centered_x = features - feature_mean
        centered_y = residual_target - target_mean
        gram = (centered_x.T @ centered_x) / max(1, centered_x.shape[0])
        rhs = (centered_x.T @ centered_y) / max(1, centered_x.shape[0])
        regularized = gram + alpha * np.eye(sequence_length, dtype=np.float64)
        try:
            weight = np.linalg.solve(regularized, rhs)
        except np.linalg.LinAlgError:
            weight = np.linalg.lstsq(regularized, rhs, rcond=None)[0]
        weights[target] = weight
        bias[target] = target_mean - feature_mean @ weight
    return {"weights": weights, "bias": bias}


def _nlinear_predict(state: dict[str, Any], x: Any, target_indices: Any) -> Any:
    np = _np()
    target_indices = np.asarray(target_indices, dtype=np.int64)
    sample_count = x.shape[0]
    target_count, _sequence_length, horizon = state["weights"].shape
    output = np.empty((sample_count, horizon, target_count), dtype=np.float64)
    for target in range(target_count):
        history = np.asarray(x[:, :, target_indices[target]], dtype=np.float64)
        last = history[:, -1:]
        normalized = history - last
        residual = normalized @ state["weights"][target] + state["bias"][target]
        output[:, :, target] = residual + last
    return output


def build_tsmixer_lite(
    *,
    sequence_length: int,
    feature_count: int,
    horizon_steps: int,
    target_count: int,
    target_indices: tuple[int, ...],
    blocks: int = 2,
    time_hidden: int = 32,
    feature_hidden: int = 32,
    dropout: float = 0.1,
):
    torch, nn, _loader, _dataset = _torch()
    if sequence_length < 2 or feature_count < 1 or horizon_steps < 1 or target_count < 1:
        raise ValueError("invalid TSMixer-lite dimensions")
    if blocks < 1 or time_hidden < 1 or feature_hidden < 1:
        raise ValueError("TSMixer-lite hidden dimensions and blocks must be positive")
    if not 0.0 <= dropout < 1.0:
        raise ValueError("TSMixer-lite dropout must be in [0,1)")

    class MixerBlock(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.time_norm = nn.LayerNorm(sequence_length)
            self.time_mlp = nn.Sequential(
                nn.Linear(sequence_length, time_hidden),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(time_hidden, sequence_length),
            )
            self.feature_norm = nn.LayerNorm(feature_count)
            self.feature_mlp = nn.Sequential(
                nn.Linear(feature_count, feature_hidden),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(feature_hidden, feature_count),
            )

        def forward(self, values):
            time_view = values.transpose(1, 2)
            time_view = time_view + self.time_mlp(self.time_norm(time_view))
            values = time_view.transpose(1, 2)
            values = values + self.feature_mlp(self.feature_norm(values))
            return values

    class TSMixerLite(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.blocks = nn.ModuleList(MixerBlock() for _ in range(blocks))
            self.final_norm = nn.LayerNorm(feature_count)
            self.head = nn.Linear(sequence_length * feature_count, horizon_steps * target_count)
            nn.init.zeros_(self.head.weight)
            nn.init.zeros_(self.head.bias)
            self.register_buffer(
                "target_indices_tensor",
                torch.tensor(target_indices, dtype=torch.long),
                persistent=False,
            )

        def forward(self, values):
            residual_last = values[:, -1, :].index_select(1, self.target_indices_tensor)
            encoded = values
            for block in self.blocks:
                encoded = block(encoded)
            encoded = self.final_norm(encoded)
            residual = self.head(encoded.reshape(encoded.shape[0], -1)).reshape(
                encoded.shape[0], horizon_steps, target_count
            )
            return residual + residual_last[:, None, :]

    return TSMixerLite()


def _predict_tsmixer(model: Any, x: Any, *, device: str, batch_size: int) -> Any:
    np = _np()
    torch, _nn, _loader, _dataset = _torch()
    model.eval()
    output = []
    with torch.no_grad():
        for start in range(0, x.shape[0], batch_size):
            batch = torch.from_numpy(x[start : start + batch_size].astype("float32")).to(device)
            output.append(model(batch).detach().cpu().numpy())
    if not output:
        return np.empty((0, 0, 0), dtype=np.float32)
    return np.concatenate(output, axis=0)


def _fit_tsmixer(
    data: dict[str, Any],
    *,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    patience: int,
    seed: int,
    device: str,
    blocks: int,
    time_hidden: int,
    feature_hidden: int,
    dropout: float,
    weight_decay: float,
    grad_clip_norm: float,
) -> tuple[Any, dict[str, Any]]:
    if epochs < 1 or batch_size < 1 or learning_rate <= 0 or patience < 1:
        raise ValueError("TSMixer epochs, batch_size, learning_rate, and patience must be positive")
    if weight_decay < 0 or grad_clip_norm < 0:
        raise ValueError("TSMixer weight_decay and grad_clip_norm must be non-negative")
    np = _np()
    torch, nn, DataLoader, TensorDataset = _torch()
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    resolved_device = _device_name(device)
    target_indices = tuple(int(value) for value in data["target_indices"])
    model = build_tsmixer_lite(
        sequence_length=int(data["X_train"].shape[1]),
        feature_count=int(data["X_train"].shape[2]),
        horizon_steps=int(data["Y_train"].shape[1]),
        target_count=int(data["Y_train"].shape[2]),
        target_indices=target_indices,
        blocks=blocks,
        time_hidden=time_hidden,
        feature_hidden=feature_hidden,
        dropout=dropout,
    ).to(resolved_device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=max(1, patience // 2)
    )
    criterion = nn.SmoothL1Loss(beta=0.01)
    train_loader = DataLoader(
        TensorDataset(
            torch.from_numpy(data["X_train"].astype("float32")),
            torch.from_numpy(data["Y_train"].astype("float32")),
        ),
        batch_size=min(batch_size, max(1, len(data["X_train"]))),
        shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(
            torch.from_numpy(data["X_val"].astype("float32")),
            torch.from_numpy(data["Y_val"].astype("float32")),
        ),
        batch_size=min(batch_size, max(1, len(data["X_val"]))),
        shuffle=False,
    )
    best_state = copy.deepcopy(model.state_dict())
    best_loss = math.inf
    best_epoch = 0
    wait = 0
    history: list[dict[str, float]] = []
    for epoch in range(1, epochs + 1):
        model.train()
        losses = []
        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(resolved_device)
            batch_y = batch_y.to(resolved_device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(batch_x), batch_y)
            loss.backward()
            if grad_clip_norm > 0:
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip_norm)
            optimizer.step()
            losses.append(float(loss.detach().cpu().item()))
        model.eval()
        val_losses = []
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                batch_x = batch_x.to(resolved_device)
                batch_y = batch_y.to(resolved_device)
                val_losses.append(float(criterion(model(batch_x), batch_y).detach().cpu().item()))
        train_loss = float(sum(losses) / max(1, len(losses)))
        val_loss = float(sum(val_losses) / max(1, len(val_losses)))
        scheduler.step(val_loss)
        history.append(
            {
                "epoch": float(epoch),
                "train_loss": train_loss,
                "val_loss": val_loss,
                "learning_rate": float(optimizer.param_groups[0]["lr"]),
            }
        )
        if val_loss < best_loss - 1e-8:
            best_loss = val_loss
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                break
    model.load_state_dict(best_state)
    return model, {
        "device": resolved_device,
        "best_epoch": best_epoch,
        "best_val_loss": best_loss,
        "epochs_ran": len(history),
        "history": history,
        "param_count": sum(int(parameter.numel()) for parameter in model.parameters()),
        "config": {
            "blocks": blocks,
            "time_hidden": time_hidden,
            "feature_hidden": feature_hidden,
            "dropout": dropout,
            "weight_decay": weight_decay,
            "grad_clip_norm": grad_clip_norm,
        },
    }


def train_local_forecast_v2(
    dataset_npz: str | Path,
    output_dir: str | Path,
    *,
    model_type: str,
    alpha: float = 1e-3,
    l1_ratio: float = 0.5,
    elasticnet_max_iter: int = 1000,
    elasticnet_tolerance: float = 1e-7,
    epochs: int = 50,
    batch_size: int = 64,
    learning_rate: float = 1e-3,
    patience: int = 8,
    seed: int = 42,
    device: str = "auto",
    blocks: int = 2,
    time_hidden: int = 32,
    feature_hidden: int = 32,
    dropout: float = 0.1,
    weight_decay: float = 1e-4,
    grad_clip_norm: float = 1.0,
) -> dict[str, Any]:
    if model_type not in MODEL_TYPES:
        raise ValueError(f"model_type must be one of {MODEL_TYPES}")
    data = load_forecast_dataset_v2(dataset_npz)
    np = _np()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    train_x = data["X_train"].astype(np.float64)
    train_y = data["Y_train"].astype(np.float64)
    val_x = data["X_val"].astype(np.float64)
    val_y = data["Y_val"].astype(np.float64)
    model_config: dict[str, Any]
    artifact: Path
    training_details: dict[str, Any] = {}

    if model_type in ("ridge", "elasticnet"):
        flat_x = train_x.reshape(train_x.shape[0], -1)
        flat_y = _flatten_y(train_y)
        if model_type == "ridge":
            state = _ridge_fit(flat_x, flat_y, alpha=alpha)
            model_config = {"alpha": alpha, "solver": "closed_form_ridge"}
        else:
            state = _elasticnet_fit(
                flat_x,
                flat_y,
                alpha=alpha,
                l1_ratio=l1_ratio,
                max_iter=elasticnet_max_iter,
                tolerance=elasticnet_tolerance,
            )
            model_config = {
                "alpha": alpha,
                "l1_ratio": l1_ratio,
                "max_iter": elasticnet_max_iter,
                "tolerance": elasticnet_tolerance,
                "iterations_ran": int(state["iterations_ran"][0]),
                "solver": "fista_proximal_gradient",
            }
        artifact = output / "model.npz"
        np.savez_compressed(artifact, **state)
        val_prediction = _linear_predict(
            state, val_x.reshape(val_x.shape[0], -1)
        ).reshape(val_y.shape)
        param_count = int(state["weights"].size + state["y_mean"].size)
    elif model_type == "nlinear":
        state = _nlinear_fit(
            train_x,
            train_y,
            data["target_indices"],
            alpha=alpha,
        )
        artifact = output / "model.npz"
        np.savez_compressed(artifact, **state)
        val_prediction = _nlinear_predict(state, val_x, data["target_indices"])
        model_config = {
            "alpha": alpha,
            "architecture": "NLinear last-value-normalized per-target linear",
        }
        param_count = int(state["weights"].size + state["bias"].size)
    else:
        model, details = _fit_tsmixer(
            data,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            patience=patience,
            seed=seed,
            device=device,
            blocks=blocks,
            time_hidden=time_hidden,
            feature_hidden=feature_hidden,
            dropout=dropout,
            weight_decay=weight_decay,
            grad_clip_norm=grad_clip_norm,
        )
        artifact = output / "model.pt"
        torch, _nn, _loader, _dataset = _torch()
        torch.save(
            {
                "state_dict": model.state_dict(),
                "sequence_length": int(data["X_train"].shape[1]),
                "feature_count": int(data["X_train"].shape[2]),
                "horizon_steps": int(data["Y_train"].shape[1]),
                "target_count": int(data["Y_train"].shape[2]),
                "target_indices": [int(value) for value in data["target_indices"]],
                **details["config"],
            },
            artifact,
        )
        val_prediction = _predict_tsmixer(
            model,
            val_x.astype(np.float32),
            device=details["device"],
            batch_size=batch_size,
        )
        model_config = {"architecture": "TSMixer-lite", **details["config"]}
        param_count = int(details["param_count"])
        training_details = {key: value for key, value in details.items() if key != "config"}

    if not np.isfinite(val_prediction).all():
        raise ValueError("trained model produced non-finite validation predictions")
    val_metrics = regression_metrics_v2(
        val_y,
        val_prediction,
        data["target_names_tuple"],
        data["mase_scale"],
        data["meta"].get("normalization_ranges", {}),
    )
    metadata = {
        "schema": MODEL_SCHEMA,
        "model_type": model_type,
        "created_at": _utc_now(),
        "dataset_ref": str(dataset_npz),
        "dataset_sha256": _sha256(dataset_npz),
        "dataset_schema": data["meta"]["schema"],
        "feature_names": list(data["feature_names_tuple"]),
        "feature_schema_sha256": str(data["feature_schema_sha256"][0]),
        "target_names": list(data["target_names_tuple"]),
        "target_indices": [int(value) for value in data["target_indices"]],
        "normalization_ranges": data["meta"].get("normalization_ranges", {}),
        "sequence_length": int(data["X_train"].shape[1]),
        "horizon_steps": int(data["Y_train"].shape[1]),
        "cadence_seconds": float(data["cadence_seconds"][0]),
        "seed": seed,
        "model_config": model_config,
        "param_count": param_count,
        # Keep the artifact reference relocatable with model.json. The loader
        # resolves this path relative to the metadata file, not the process CWD.
        "artifact": artifact.name,
        "artifact_sha256": _sha256(artifact),
        "validation_metrics": val_metrics,
        "training_details": training_details,
        "training_wall_ms": (time.perf_counter() - started) * 1000.0,
        "readiness": "EXPERIMENTAL",
        "deployment_mode": "shadow_only",
        "field_accuracy_claim": None,
    }
    (output / "model.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def _load_local_model(metadata_path: str | Path, data: dict[str, Any], device: str) -> tuple[Any, dict[str, Any], str]:
    np = _np()
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if metadata.get("schema") != MODEL_SCHEMA:
        raise ValueError(f"unsupported local model schema: {metadata.get('schema')}")
    if metadata.get("feature_schema_sha256") != str(data["feature_schema_sha256"][0]):
        raise ValueError("model feature schema does not match dataset")
    if tuple(metadata.get("target_names", ())) != data["target_names_tuple"]:
        raise ValueError("model target schema does not match dataset")
    if int(metadata.get("horizon_steps", -1)) != int(data["Y_train"].shape[1]):
        raise ValueError("model horizon does not match dataset")
    artifact_ref = Path(str(metadata["artifact"]))
    artifact = (
        artifact_ref
        if artifact_ref.is_absolute()
        else Path(metadata_path).resolve().parent / artifact_ref
    )
    if _sha256(artifact) != metadata.get("artifact_sha256"):
        raise ValueError("model artifact sha256 mismatch")
    model_type = str(metadata["model_type"])
    if model_type in ("ridge", "elasticnet", "nlinear"):
        raw = np.load(artifact, allow_pickle=False)
        state = {name: raw[name] for name in raw.files}
        return state, metadata, "numpy"
    if model_type != "tsmixer_lite":
        raise ValueError(f"unsupported local model_type: {model_type}")
    torch, _nn, _loader, _dataset = _torch()
    resolved_device = _device_name(device)
    try:
        checkpoint = torch.load(artifact, map_location=resolved_device, weights_only=True)
    except Exception as exc:
        raise ValueError("unsafe or invalid TSMixer checkpoint rejected") from exc
    model = build_tsmixer_lite(
        sequence_length=int(checkpoint["sequence_length"]),
        feature_count=int(checkpoint["feature_count"]),
        horizon_steps=int(checkpoint["horizon_steps"]),
        target_count=int(checkpoint["target_count"]),
        target_indices=tuple(int(value) for value in checkpoint["target_indices"]),
        blocks=int(checkpoint["blocks"]),
        time_hidden=int(checkpoint["time_hidden"]),
        feature_hidden=int(checkpoint["feature_hidden"]),
        dropout=float(checkpoint["dropout"]),
    ).to(resolved_device)
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    model.eval()
    return model, metadata, resolved_device


def predict_local_forecast_v2(
    dataset_npz: str | Path,
    metadata_path: str | Path,
    *,
    split: str,
    device: str = "auto",
    batch_size: int = 1024,
) -> Any:
    if split not in ("train", "val", "test"):
        raise ValueError("split must be train, val, or test")
    data = load_forecast_dataset_v2(dataset_npz)
    model, metadata, backend = _load_local_model(metadata_path, data, device)
    x = data[f"X_{split}"]
    model_type = metadata["model_type"]
    if model_type in ("ridge", "elasticnet"):
        predicted = _linear_predict(model, x.reshape(x.shape[0], -1)).reshape(data[f"Y_{split}"].shape)
    elif model_type == "nlinear":
        predicted = _nlinear_predict(model, x, data["target_indices"])
    else:
        predicted = _predict_tsmixer(model, x, device=backend, batch_size=batch_size)
    return predicted


def _baseline_delta(model_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> dict[str, Any]:
    per_target: dict[str, Any] = {}
    for name, metrics in model_metrics["per_target"].items():
        baseline = baseline_metrics["per_target"][name]
        baseline_rmse = float(baseline["rmse"])
        model_rmse = float(metrics["rmse"])
        per_target[name] = {
            "rmse_delta": model_rmse - baseline_rmse,
            "rmse_skill_score": None
            if baseline_rmse == 0
            else 1.0 - model_rmse / baseline_rmse,
            "beats_baseline": model_rmse < baseline_rmse,
        }
    baseline_rmse = float(baseline_metrics["overall_rmse"])
    model_rmse = float(model_metrics["overall_rmse"])
    return {
        "overall_rmse_delta": model_rmse - baseline_rmse,
        "overall_rmse_skill_score": None
        if baseline_rmse == 0
        else 1.0 - model_rmse / baseline_rmse,
        "per_target": per_target,
    }


def evaluate_local_forecast_v2(
    dataset_npz: str | Path,
    metadata_path: str | Path,
    output_json: str | Path | None = None,
    *,
    device: str = "auto",
    batch_size: int = 1024,
) -> dict[str, Any]:
    data = load_forecast_dataset_v2(dataset_npz)
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    baseline_report = evaluate_baselines_v2(dataset_npz)
    selection = baseline_report["selection"]
    result: dict[str, Any] = {
        "schema": METRICS_SCHEMA,
        "model_type": metadata["model_type"],
        "model_metadata": str(metadata_path),
        "dataset": str(dataset_npz),
        "baseline_selection": selection,
        "splits": {},
    }
    predictions_by_split: dict[str, Any] = {}
    for split in ("train", "val", "test"):
        predicted = predict_local_forecast_v2(
            dataset_npz,
            metadata_path,
            split=split,
            device=device,
            batch_size=batch_size,
        )
        predictions_by_split[split] = predicted
        metrics = regression_metrics_v2(
            data[f"Y_{split}"],
            predicted,
            data["target_names_tuple"],
            data["mase_scale"],
            data["meta"].get("normalization_ranges", {}),
        )
        baseline_prediction = compose_selected_baseline_v2(data, split, selection)
        baseline_metrics = regression_metrics_v2(
            data[f"Y_{split}"],
            baseline_prediction,
            data["target_names_tuple"],
            data["mase_scale"],
            data["meta"].get("normalization_ranges", {}),
        )
        metrics["baseline"] = baseline_metrics
        metrics["baseline_delta"] = _baseline_delta(metrics, baseline_metrics)
        result["splits"][split] = metrics

    conformal_radius = calibrate_conformal_radius_v2(
        data["Y_val"], predictions_by_split["val"], coverage=0.90
    )
    result["uncertainty"] = {
        "method": "split_conformal_absolute_residual",
        "calibration_split": "validation",
        "target_coverage": 0.90,
        "radius_normalized": conformal_radius.tolist(),
        "validation": interval_metrics_v2(
            data["Y_val"],
            predictions_by_split["val"],
            conformal_radius,
            data["target_names_tuple"],
            normalization_ranges=data["meta"].get("normalization_ranges", {}),
        ),
        "test": interval_metrics_v2(
            data["Y_test"],
            predictions_by_split["test"],
            conformal_radius,
            data["target_names_tuple"],
            normalization_ranges=data["meta"].get("normalization_ranges", {}),
        ),
    }

    test_delta = result["splits"]["test"]["baseline_delta"]
    target_results = test_delta["per_target"]
    wins = sum(int(item["beats_baseline"]) for item in target_results.values())
    required_wins = math.ceil(len(target_results) / 2)
    baseline_gate = bool(test_delta["overall_rmse_delta"] < 0 and wins >= required_wins)
    data_status = str(data["meta"].get("data_quality", {}).get("status", "UNKNOWN"))
    result["promotion_gate"] = {
        "data_quality_status": data_status,
        "beats_selected_baseline": baseline_gate,
        "target_wins": wins,
        "target_wins_required": required_wins,
        "field_generalization_verified": False,
        "pi_shadow_verified": False,
        "status": "PROMISING_HOST_ONLY" if data_status == "PASS" and baseline_gate else "EXPERIMENTAL",
    }
    if output_json is not None:
        output = Path(output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def run_local_bakeoff_v2(
    dataset_npz: str | Path,
    output_dir: str | Path,
    *,
    model_types: tuple[str, ...] = MODEL_TYPES,
    seeds: tuple[int, ...] = (17, 42, 73),
    tsmixer_epochs: int = 30,
    device: str = "auto",
) -> dict[str, Any]:
    """Repeated-seed host bakeoff; never promotes a model to production."""

    np = _np()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for model_type in model_types:
        if model_type not in MODEL_TYPES:
            raise ValueError(f"unknown model_type in bakeoff: {model_type}")
        effective_seeds = seeds if model_type == "tsmixer_lite" else (seeds[0],)
        for seed in effective_seeds:
            run_dir = output / model_type / f"seed_{seed}"
            metadata = train_local_forecast_v2(
                dataset_npz,
                run_dir,
                model_type=model_type,
                seed=seed,
                epochs=tsmixer_epochs,
                device=device,
            )
            metrics = evaluate_local_forecast_v2(
                dataset_npz,
                run_dir / "model.json",
                run_dir / "metrics.json",
                device=device,
            )
            runs.append(
                {
                    "model_type": model_type,
                    "seed": seed,
                    "metadata": str(run_dir / "model.json"),
                    "metrics": str(run_dir / "metrics.json"),
                    "test_rmse": metrics["splits"]["test"]["overall_rmse"],
                    "test_mase": metrics["splits"]["test"]["overall_mase"],
                    "test_rmse_skill_score": metrics["splits"]["test"]["baseline_delta"][
                        "overall_rmse_skill_score"
                    ],
                    "promotion_status": metrics["promotion_gate"]["status"],
                    "param_count": metadata["param_count"],
                }
            )
    grouped: dict[str, Any] = {}
    for model_type in model_types:
        items = [item for item in runs if item["model_type"] == model_type]
        rmse = np.asarray([item["test_rmse"] for item in items], dtype=np.float64)
        grouped[model_type] = {
            "runs": len(items),
            "test_rmse_mean": float(np.mean(rmse)),
            "test_rmse_std": float(np.std(rmse)),
            "all_runs_promising_host_only": all(
                item["promotion_status"] == "PROMISING_HOST_ONLY" for item in items
            ),
        }
    summary = {
        "schema": "iiot.ai_sensor.local_forecast_bakeoff.v2",
        "dataset": str(dataset_npz),
        "seeds": list(seeds),
        "runs": runs,
        "models": grouped,
        "selection_policy": "no production winner; compare host evidence only",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
