from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES

TARGET_NAMES = ("temperature_c", "humidity_pct", "pressure_hpa", "bme_gas_raw", "co_raw")


def _require_numpy():
    import numpy as np

    return np


def _require_torch():
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    return torch, nn, DataLoader, TensorDataset


@dataclass(frozen=True)
class ForecastDatasetStats:
    source_windows: str
    output_npz: str
    output_meta: str
    horizon_steps: int
    feature_names: tuple[str, ...]
    target_names: tuple[str, ...]
    target_indices: tuple[int, ...]
    total_samples: int
    train_samples: int
    val_samples: int
    test_samples: int
    input_shape: tuple[int, int, int]
    target_shape: tuple[int, int]
    nodes: tuple[str, ...]
    start_timestamp: str | None
    end_timestamp: str | None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "feature_names",
            "target_names",
            "target_indices",
            "input_shape",
            "target_shape",
            "nodes",
        ):
            data[key] = list(data[key])
        return data


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _read_window_records(windows_jsonl: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in Path(windows_jsonl).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        shape = record.get("shape") or []
        if len(shape) != 2 or int(shape[0]) <= 0 or int(shape[1]) <= 0:
            raise ValueError(f"invalid window shape: {shape}")
        records.append(record)
    if not records:
        raise ValueError(f"no windows found in {windows_jsonl}")
    return records


def _split_counts(total: int, train_ratio: float, val_ratio: float) -> tuple[int, int, int]:
    if total < 3:
        raise ValueError("at least 3 samples per node are required")
    train = max(1, int(total * train_ratio))
    val = max(1, int(total * val_ratio))
    if train + val >= total:
        train = max(1, total - 2)
        val = 1
    return train, val, total - train - val


def prepare_forecast_dataset(
    windows_jsonl: str | Path,
    output_npz: str | Path = "data/modeling/lstm_forecast_dataset.npz",
    output_meta: str | Path = "data/modeling/lstm_forecast_dataset_meta.json",
    horizon_steps: int = 5,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    target_names: tuple[str, ...] = TARGET_NAMES,
) -> ForecastDatasetStats:
    if horizon_steps < 1:
        raise ValueError("horizon_steps must be >= 1")
    np = _require_numpy()
    records = _read_window_records(windows_jsonl)
    feature_names = tuple(records[0]["feature_names"])
    if feature_names != FEATURE_NAMES:
        raise ValueError("window feature_names do not match pipeline FEATURE_NAMES")
    target_indices = tuple(feature_names.index(name) for name in target_names)

    by_node: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if tuple(record["feature_names"]) != feature_names:
            raise ValueError("mixed feature_names in window dataset")
        by_node.setdefault(record["node_id"], []).append(record)

    arrays: dict[str, list[Any]] = {
        "X_train": [],
        "y_train": [],
        "X_val": [],
        "y_val": [],
        "X_test": [],
        "y_test": [],
    }
    starts: list[str] = []
    ends: list[str] = []
    for node_id in sorted(by_node):
        node_records = sorted(by_node[node_id], key=lambda item: _parse_dt(item["end_timestamp"]))
        sample_count = len(node_records) - horizon_steps
        if sample_count < 3:
            continue
        train_count, val_count, _ = _split_counts(sample_count, train_ratio, val_ratio)
        for index in range(sample_count):
            current = node_records[index]
            future = node_records[index + horizon_steps]
            split = "train" if index < train_count else "val" if index < train_count + val_count else "test"
            arrays[f"X_{split}"].append(current["x"])
            arrays[f"y_{split}"].append([future["x"][-1][target_index] for target_index in target_indices])
            starts.append(current["start_timestamp"])
            ends.append(future["end_timestamp"])

    if not arrays["X_train"] or not arrays["X_val"] or not arrays["X_test"]:
        raise ValueError("not enough forecast samples to build all splits")

    output_path = Path(output_npz)
    meta_path = Path(output_meta)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    np_arrays = {name: np.asarray(value, dtype=np.float32) for name, value in arrays.items()}
    np.savez_compressed(
        output_path,
        **np_arrays,
        feature_names=np.asarray(feature_names),
        target_names=np.asarray(target_names),
        target_indices=np.asarray(target_indices, dtype=np.int64),
        horizon_steps=np.asarray([horizon_steps], dtype=np.int64),
    )

    total_samples = sum(int(np_arrays[name].shape[0]) for name in ("X_train", "X_val", "X_test"))
    input_shape = tuple(int(item) for item in np_arrays["X_train"].shape[1:])
    target_shape = tuple(int(item) for item in np_arrays["y_train"].shape[1:])
    stats = ForecastDatasetStats(
        str(windows_jsonl),
        str(output_path),
        str(meta_path),
        horizon_steps,
        feature_names,
        target_names,
        target_indices,
        total_samples,
        int(np_arrays["X_train"].shape[0]),
        int(np_arrays["X_val"].shape[0]),
        int(np_arrays["X_test"].shape[0]),
        (total_samples, input_shape[0], input_shape[1]),
        (total_samples, target_shape[0]),
        tuple(sorted(by_node)),
        min(starts) if starts else None,
        max(ends) if ends else None,
    )
    meta_path.write_text(json.dumps(stats.as_dict(), indent=2), encoding="utf-8")
    return stats


def _device_name(requested: str) -> str:
    torch, _nn, _loader, _dataset = _require_torch()
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return requested


def build_lstm_forecaster(input_size: int, output_size: int, hidden_size: int = 64, num_layers: int = 1):
    _torch, nn, _loader, _dataset = _require_torch()

    class LSTMForecaster(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.lstm = nn.LSTM(input_size, hidden_size, num_layers=num_layers, batch_first=True)
            self.head = nn.Linear(hidden_size, output_size)

        def forward(self, x):
            output, _hidden = self.lstm(x)
            return self.head(output[:, -1, :])

    return LSTMForecaster()


def train_lstm_forecast(
    dataset_npz: str | Path = "data/modeling/lstm_forecast_dataset.npz",
    output_dir: str | Path = "models/lstm_forecast/latest",
    epochs: int = 30,
    batch_size: int = 64,
    learning_rate: float = 0.001,
    hidden_size: int = 64,
    num_layers: int = 1,
    patience: int = 5,
    seed: int = 42,
    device: str = "auto",
) -> dict[str, Any]:
    np = _require_numpy()
    torch, nn, DataLoader, TensorDataset = _require_torch()
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    data = np.load(dataset_npz, allow_pickle=False)
    X_train = data["X_train"].astype("float32")
    y_train = data["y_train"].astype("float32")
    X_val = data["X_val"].astype("float32")
    y_val = data["y_val"].astype("float32")
    feature_names = tuple(str(item) for item in data["feature_names"])
    target_names = tuple(str(item) for item in data["target_names"])
    resolved_device = _device_name(device)

    model = build_lstm_forecaster(X_train.shape[2], y_train.shape[1], hidden_size, num_layers).to(resolved_device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    criterion = nn.MSELoss()
    loader = DataLoader(TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train)), batch_size=batch_size, shuffle=True)
    val_x = torch.from_numpy(X_val).to(resolved_device)
    val_y = torch.from_numpy(y_val).to(resolved_device)

    best_loss = math.inf
    best_epoch = 0
    best_state = copy.deepcopy(model.state_dict())
    wait = 0
    history: list[dict[str, float]] = []
    for epoch in range(1, epochs + 1):
        model.train()
        losses: list[float] = []
        for batch_x, batch_y in loader:
            batch_x = batch_x.to(resolved_device)
            batch_y = batch_y.to(resolved_device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach().cpu().item()))
        model.eval()
        with torch.no_grad():
            val_loss = float(criterion(model(val_x), val_y).detach().cpu().item())
        train_loss = float(sum(losses) / max(1, len(losses)))
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        if val_loss < best_loss:
            best_loss = val_loss
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                break

    model.load_state_dict(best_state)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "input_size": int(X_train.shape[2]),
            "output_size": int(y_train.shape[1]),
            "hidden_size": hidden_size,
            "num_layers": num_layers,
            "feature_names": feature_names,
            "target_names": target_names,
        },
        model_path,
    )
    result = {
        "model_path": str(model_path),
        "dataset_npz": str(dataset_npz),
        "device": resolved_device,
        "epochs_requested": epochs,
        "epochs_ran": len(history),
        "best_epoch": best_epoch,
        "best_val_loss": best_loss,
        "input_shape": list(X_train.shape[1:]),
        "target_shape": list(y_train.shape[1:]),
        "feature_names": list(feature_names),
        "target_names": list(target_names),
        "history": history,
    }
    (out_dir / "training.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _load_model(model_path: str | Path, device: str = "auto"):
    torch, _nn, _loader, _dataset = _require_torch()
    resolved_device = _device_name(device)
    checkpoint = torch.load(model_path, map_location=resolved_device, weights_only=False)
    model = build_lstm_forecaster(
        int(checkpoint["input_size"]),
        int(checkpoint["output_size"]),
        int(checkpoint["hidden_size"]),
        int(checkpoint["num_layers"]),
    ).to(resolved_device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model, checkpoint, resolved_device


def _regression_metrics(actual, predicted, target_names: tuple[str, ...]) -> dict[str, Any]:
    np = _require_numpy()
    error = predicted - actual
    mae = np.mean(np.abs(error), axis=0)
    rmse = np.sqrt(np.mean(error * error, axis=0))
    return {
        "overall_mae": float(np.mean(mae)),
        "overall_rmse": float(np.mean(rmse)),
        "per_target": {
            name: {"mae": float(mae[index]), "rmse": float(rmse[index])}
            for index, name in enumerate(target_names)
        },
    }


def evaluate_lstm_forecast(
    dataset_npz: str | Path = "data/modeling/lstm_forecast_dataset.npz",
    model_path: str | Path = "models/lstm_forecast/latest/model.pt",
    output_dir: str | Path | None = None,
    device: str = "auto",
) -> dict[str, Any]:
    np = _require_numpy()
    torch, _nn, _loader, _dataset = _require_torch()
    data = np.load(dataset_npz, allow_pickle=False)
    model, _checkpoint, resolved_device = _load_model(model_path, device)
    target_names = tuple(str(item) for item in data["target_names"])
    target_indices = data["target_indices"].astype("int64")
    result: dict[str, Any] = {
        "model_path": str(model_path),
        "dataset_npz": str(dataset_npz),
        "device": resolved_device,
        "target_names": list(target_names),
        "splits": {},
    }
    for split in ("train", "val", "test"):
        x = data[f"X_{split}"].astype("float32")
        y = data[f"y_{split}"].astype("float32")
        with torch.no_grad():
            pred = model(torch.from_numpy(x).to(resolved_device)).detach().cpu().numpy()
        baseline = x[:, -1, target_indices]
        result["splits"][split] = {
            "samples": int(x.shape[0]),
            "lstm": _regression_metrics(y, pred, target_names),
            "last_value_baseline": _regression_metrics(y, baseline, target_names),
        }
    result["nan_count"] = int(sum(np.isnan(data[name]).sum() for name in ("X_train", "y_train", "X_val", "y_val", "X_test", "y_test")))
    result["inf_count"] = int(sum(np.isinf(data[name]).sum() for name in ("X_train", "y_train", "X_val", "y_val", "X_test", "y_test")))
    result["status"] = "PASS" if result["nan_count"] == 0 and result["inf_count"] == 0 else "FAIL"
    output_path = Path(model_path).parent if output_dir is None else Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def predict_lstm_forecast(
    windows_jsonl: str | Path,
    model_path: str | Path,
    output_jsonl: str | Path,
    max_windows: int = 0,
    device: str = "auto",
) -> Path:
    np = _require_numpy()
    torch, _nn, _loader, _dataset = _require_torch()
    model, checkpoint, resolved_device = _load_model(model_path, device)
    feature_names = tuple(checkpoint["feature_names"])
    target_names = tuple(checkpoint["target_names"])
    output_path = Path(output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as out:
        for record in _read_window_records(windows_jsonl):
            if tuple(record["feature_names"]) != feature_names:
                raise ValueError("window feature_names do not match trained model")
            x = np.asarray([record["x"]], dtype=np.float32)
            with torch.no_grad():
                pred = model(torch.from_numpy(x).to(resolved_device)).detach().cpu().numpy()[0]
            out.write(json.dumps({
                "gateway_id": record["gateway_id"],
                "node_id": record["node_id"],
                "room_id": record["room_id"],
                "input_start_timestamp": record["start_timestamp"],
                "input_end_timestamp": record["end_timestamp"],
                "target_names": list(target_names),
                "prediction_normalized": [float(value) for value in pred],
            }, separators=(",", ":")) + "\n")
            count += 1
            if max_windows and count >= max_windows:
                break
    return output_path
