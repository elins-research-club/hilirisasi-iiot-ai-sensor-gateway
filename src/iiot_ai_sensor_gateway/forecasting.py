from __future__ import annotations

import copy
import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES
from .normalization import DEFAULT_RANGES

TARGET_NAMES = ("temperature_c", "humidity_pct", "pressure_hpa", "bme_gas_raw", "co_raw")
MODEL_VERSION = "lstm_forecast_v1"
SELECTION_SCHEMA = "iiot.ai_sensor.forecast_model_selection.v1"
DEFAULT_SELECTION_WEIGHTS = {
    "co_raw": 0.25,
    "bme_gas_raw": 0.25,
    "temperature_c": 0.15,
    "humidity_pct": 0.15,
    "pressure_hpa": 0.10,
    "overall": 0.10,
}


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
    resample_interval_sec: int
    window_size: int
    purge_gap_steps: int
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
    split_time_range: dict[str, Any]
    normalization_ranges: dict[str, tuple[float, float]]
    created_at: str

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
        data["normalization_ranges"] = {
            key: list(value) for key, value in self.normalization_ranges.items()
        }
        return data


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


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


def _merged_ranges(ranges: dict[str, tuple[float, float]] | None = None) -> dict[str, tuple[float, float]]:
    return {**DEFAULT_RANGES, **(ranges or {})}


def _json_ranges(ranges: dict[str, tuple[float, float]]) -> str:
    return json.dumps({key: list(value) for key, value in ranges.items()}, sort_keys=True)


def _loads_json_array_value(value: Any, default: Any) -> Any:
    try:
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return json.loads(str(value))
    except Exception:
        return default


def _split_counts(total: int, train_ratio: float, val_ratio: float) -> tuple[int, int, int]:
    if total < 3:
        raise ValueError("at least 3 samples per node are required")
    train = max(1, int(total * train_ratio))
    val = max(1, int(total * val_ratio))
    if train + val >= total:
        train = max(1, total - 2)
        val = 1
    return train, val, total - train - val


def _ensure_feature_names(records: list[dict[str, Any]]) -> tuple[str, ...]:
    feature_names = tuple(records[0]["feature_names"])
    if feature_names != FEATURE_NAMES:
        raise ValueError("window feature_names do not match pipeline FEATURE_NAMES")
    for record in records:
        if tuple(record["feature_names"]) != feature_names:
            raise ValueError("mixed feature_names in window dataset")
    return feature_names


def _rebuild_windows(records: list[dict[str, Any]], window_size: int) -> list[dict[str, Any]]:
    if window_size < 1:
        raise ValueError("window_size must be >= 1")
    feature_names = _ensure_feature_names(records)
    if int(records[0]["shape"][0]) == window_size:
        return records

    by_node: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        by_node.setdefault(record["node_id"], {})[record["end_timestamp"]] = record

    rebuilt: list[dict[str, Any]] = []
    for node_id in sorted(by_node):
        node_records = sorted(by_node[node_id].values(), key=lambda item: _parse_dt(item["end_timestamp"]))
        points = [
            {
                "gateway_id": record["gateway_id"],
                "node_id": node_id,
                "room_id": record["room_id"],
                "timestamp": record["end_timestamp"],
                "x": record["x"][-1],
            }
            for record in node_records
        ]
        if len(points) < window_size:
            continue
        for index in range(window_size - 1, len(points)):
            items = points[index - window_size + 1 : index + 1]
            rebuilt.append(
                {
                    "gateway_id": items[-1]["gateway_id"],
                    "node_id": node_id,
                    "room_id": items[-1]["room_id"],
                    "start_timestamp": items[0]["timestamp"],
                    "end_timestamp": items[-1]["timestamp"],
                    "feature_names": list(feature_names),
                    "shape": [window_size, len(feature_names)],
                    "x": [item["x"] for item in items],
                }
            )
    if not rebuilt:
        raise ValueError(f"not enough timeline points to rebuild window_size={window_size}")
    return rebuilt


def _make_samples(
    node_records: list[dict[str, Any]],
    horizon_steps: int,
    target_indices: tuple[int, ...],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    sample_count = len(node_records) - horizon_steps
    for index in range(max(0, sample_count)):
        current = node_records[index]
        future = node_records[index + horizon_steps]
        samples.append(
            {
                "x": current["x"],
                "y": [future["x"][-1][target_index] for target_index in target_indices],
                "input_start_timestamp": current["start_timestamp"],
                "input_end_timestamp": current["end_timestamp"],
                "label_timestamp": future["end_timestamp"],
                "node_id": current["node_id"],
            }
        )
    return samples


def _filter_non_overlapping(
    samples: list[dict[str, Any]],
    previous_label_end: datetime | None,
) -> list[dict[str, Any]]:
    if previous_label_end is None:
        return samples
    return [sample for sample in samples if _parse_dt(sample["input_start_timestamp"]) > previous_label_end]


def _assign_temporal_splits(
    samples: list[dict[str, Any]],
    train_ratio: float,
    val_ratio: float,
    purge_gap_steps: int,
) -> dict[str, list[dict[str, Any]]]:
    total = len(samples)
    if total < 3:
        return {"train": [], "val": [], "test": []}
    train_count, val_count, _test_count = _split_counts(total, train_ratio, val_ratio)
    val_start = min(total, train_count + purge_gap_steps)
    val_end = min(total, val_start + val_count)
    test_start = min(total, val_end + purge_gap_steps)

    train = samples[:train_count]
    train_label_end = max((_parse_dt(item["label_timestamp"]) for item in train), default=None)
    val = _filter_non_overlapping(samples[val_start:val_end], train_label_end)
    val_label_end = max((_parse_dt(item["label_timestamp"]) for item in val), default=train_label_end)
    test = _filter_non_overlapping(samples[test_start:], val_label_end)
    return {"train": train, "val": val, "test": test}


def _update_split_range(target: dict[str, Any], split: str, sample: dict[str, Any]) -> None:
    item = target.setdefault(
        split,
        {
            "input_start": None,
            "input_end": None,
            "label_start": None,
            "label_end": None,
            "nodes": {},
        },
    )
    node_item = item["nodes"].setdefault(
        sample["node_id"],
        {"input_start": None, "input_end": None, "label_start": None, "label_end": None},
    )
    for bucket in (item, node_item):
        bucket["input_start"] = min(
            [value for value in (bucket["input_start"], sample["input_start_timestamp"]) if value]
        )
        bucket["input_end"] = max(
            [value for value in (bucket["input_end"], sample["input_end_timestamp"]) if value]
        )
        bucket["label_start"] = min(
            [value for value in (bucket["label_start"], sample["label_timestamp"]) if value]
        )
        bucket["label_end"] = max(
            [value for value in (bucket["label_end"], sample["label_timestamp"]) if value]
        )


def _assert_split_ranges_do_not_overlap(split_time_range: dict[str, Any]) -> None:
    for node_id in {
        node
        for split in split_time_range.values()
        for node in split.get("nodes", {})
    }:
        previous_label_end: datetime | None = None
        for split in ("train", "val", "test"):
            node_range = split_time_range.get(split, {}).get("nodes", {}).get(node_id)
            if not node_range:
                continue
            input_start = _parse_dt(node_range["input_start"])
            if previous_label_end is not None and input_start <= previous_label_end:
                raise ValueError(f"temporal split overlap detected for {node_id} before {split}")
            previous_label_end = _parse_dt(node_range["label_end"])


def prepare_forecast_dataset(
    windows_jsonl: str | Path,
    output_npz: str | Path = "data/modeling/lstm_forecast_dataset.npz",
    output_meta: str | Path = "data/modeling/lstm_forecast_dataset_meta.json",
    horizon_steps: int = 5,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    target_names: tuple[str, ...] = TARGET_NAMES,
    resample_interval_sec: int = 60,
    window_size: int | None = None,
    purge_gap_steps: int | None = None,
    normalization_ranges: dict[str, tuple[float, float]] | None = None,
) -> ForecastDatasetStats:
    if horizon_steps < 1:
        raise ValueError("horizon_steps must be >= 1")
    np = _require_numpy()
    records = _read_window_records(windows_jsonl)
    selected_window_size = window_size or int(records[0]["shape"][0])
    records = _rebuild_windows(records, selected_window_size)
    feature_names = _ensure_feature_names(records)
    target_indices = tuple(feature_names.index(name) for name in target_names)
    selected_purge_gap = horizon_steps if purge_gap_steps is None else purge_gap_steps
    if selected_purge_gap < 0:
        raise ValueError("purge_gap_steps must be >= 0")

    by_node: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_node.setdefault(record["node_id"], []).append(record)

    arrays: dict[str, list[Any]] = {
        "X_train": [],
        "y_train": [],
        "X_val": [],
        "y_val": [],
        "X_test": [],
        "y_test": [],
    }
    split_time_range: dict[str, Any] = {}
    starts: list[str] = []
    ends: list[str] = []
    for node_id in sorted(by_node):
        node_records = sorted(by_node[node_id], key=lambda item: _parse_dt(item["end_timestamp"]))
        samples = _make_samples(node_records, horizon_steps, target_indices)
        split_samples = _assign_temporal_splits(samples, train_ratio, val_ratio, selected_purge_gap)
        for split, items in split_samples.items():
            for sample in items:
                arrays[f"X_{split}"].append(sample["x"])
                arrays[f"y_{split}"].append(sample["y"])
                _update_split_range(split_time_range, split, sample)
                starts.append(sample["input_start_timestamp"])
                ends.append(sample["label_timestamp"])

    if not arrays["X_train"] or not arrays["X_val"] or not arrays["X_test"]:
        raise ValueError("not enough forecast samples to build non-overlapping splits")
    _assert_split_ranges_do_not_overlap(split_time_range)

    output_path = Path(output_npz)
    meta_path = Path(output_meta)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    merged_ranges = _merged_ranges(normalization_ranges)
    np_arrays = {name: np.asarray(value, dtype=np.float32) for name, value in arrays.items()}
    created_at = _utc_now()
    total_samples = sum(int(np_arrays[name].shape[0]) for name in ("X_train", "X_val", "X_test"))
    input_shape = tuple(int(item) for item in np_arrays["X_train"].shape[1:])
    target_shape = tuple(int(item) for item in np_arrays["y_train"].shape[1:])
    stats = ForecastDatasetStats(
        str(windows_jsonl),
        str(output_path),
        str(meta_path),
        horizon_steps,
        resample_interval_sec,
        selected_window_size,
        selected_purge_gap,
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
        split_time_range,
        merged_ranges,
        created_at,
    )
    dataset_meta = stats.as_dict()
    np.savez_compressed(
        output_path,
        **np_arrays,
        feature_names=np.asarray(feature_names),
        target_names=np.asarray(target_names),
        target_indices=np.asarray(target_indices, dtype=np.int64),
        horizon_steps=np.asarray([horizon_steps], dtype=np.int64),
        resample_interval_sec=np.asarray([resample_interval_sec], dtype=np.int64),
        window_size=np.asarray([selected_window_size], dtype=np.int64),
        purge_gap_steps=np.asarray([selected_purge_gap], dtype=np.int64),
        normalization_ranges_json=np.asarray([_json_ranges(merged_ranges)]),
        dataset_meta_json=np.asarray([json.dumps(dataset_meta, sort_keys=True)]),
    )
    meta_path.write_text(json.dumps(dataset_meta, indent=2), encoding="utf-8")
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


def _load_dataset_metadata(data: Any) -> dict[str, Any]:
    return _loads_json_array_value(data.get("dataset_meta_json"), {})


def _load_normalization_ranges(data: Any) -> dict[str, tuple[float, float]]:
    raw = _loads_json_array_value(data.get("normalization_ranges_json"), {})
    return {key: (float(value[0]), float(value[1])) for key, value in raw.items()}


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
    model_version: str = MODEL_VERSION,
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
    dataset_meta = _load_dataset_metadata(data)
    normalization_ranges = _load_normalization_ranges(data)
    resolved_device = _device_name(device)

    model = build_lstm_forecaster(X_train.shape[2], y_train.shape[1], hidden_size, num_layers).to(resolved_device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    criterion = nn.MSELoss()
    loader = DataLoader(TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train)), batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val)), batch_size=batch_size, shuffle=False)

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
        val_losses: list[float] = []
        with torch.no_grad():
            for val_x, val_y in val_loader:
                val_x = val_x.to(resolved_device)
                val_y = val_y.to(resolved_device)
                val_losses.append(float(criterion(model(val_x), val_y).detach().cpu().item()))
            val_loss = float(sum(val_losses) / max(1, len(val_losses)))
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
    created_at = _utc_now()
    model_metadata = {
        "model_version": model_version,
        "created_at": created_at,
        "horizon_steps": int(data["horizon_steps"][0]) if "horizon_steps" in data else None,
        "resample_interval_sec": int(data["resample_interval_sec"][0]) if "resample_interval_sec" in data else 60,
        "window_size": int(data["window_size"][0]) if "window_size" in data else int(X_train.shape[1]),
        "feature_names": list(feature_names),
        "target_names": list(target_names),
        "normalization_ranges": {key: list(value) for key, value in normalization_ranges.items()},
        "dataset_meta": dataset_meta,
        "metrics_ref": str(out_dir / "metrics.json"),
    }
    torch.save(
        {
            "state_dict": model.state_dict(),
            "input_size": int(X_train.shape[2]),
            "output_size": int(y_train.shape[1]),
            "hidden_size": hidden_size,
            "num_layers": num_layers,
            **model_metadata,
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
        "history": history,
        **model_metadata,
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


def _denormalize_matrix(values, target_names: tuple[str, ...], ranges: dict[str, tuple[float, float]]):
    np = _require_numpy()
    output = values.astype("float32").copy()
    for index, name in enumerate(target_names):
        low, high = ranges.get(name, (0.0, 1.0))
        output[:, index] = low + (output[:, index] * (high - low))
    return output


def _skill_score(model_rmse: float, baseline_rmse: float) -> float | None:
    if baseline_rmse == 0:
        return 1.0 if model_rmse == 0 else None
    return float(1.0 - (model_rmse / baseline_rmse))


def _metric_deltas(lstm: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    per_target = {}
    for name, metrics in lstm["per_target"].items():
        base = baseline["per_target"][name]
        per_target[name] = {
            "mae_delta": float(metrics["mae"] - base["mae"]),
            "rmse_delta": float(metrics["rmse"] - base["rmse"]),
            "rmse_skill_score": _skill_score(float(metrics["rmse"]), float(base["rmse"])),
            "beats_baseline": bool(metrics["rmse"] < base["rmse"]),
        }
    return {
        "overall_mae_delta": float(lstm["overall_mae"] - baseline["overall_mae"]),
        "overall_rmse_delta": float(lstm["overall_rmse"] - baseline["overall_rmse"]),
        "overall_rmse_skill_score": _skill_score(float(lstm["overall_rmse"]), float(baseline["overall_rmse"])),
        "per_target": per_target,
    }


def _baseline_status(test_metrics: dict[str, Any]) -> str:
    deltas = test_metrics["baseline_delta"]["per_target"].values()
    wins = sum(1 for item in deltas if item["beats_baseline"])
    total = len(test_metrics["baseline_delta"]["per_target"])
    if wins == total and test_metrics["baseline_delta"]["overall_rmse_delta"] < 0:
        return "BEATS_BASELINE"
    if wins == 0 and test_metrics["baseline_delta"]["overall_rmse_delta"] >= 0:
        return "UNDER_BASELINE"
    return "MIXED"


def _model_readiness(data_status: str, baseline_status: str) -> str:
    if data_status != "PASS":
        return "NOT_READY"
    if baseline_status == "BEATS_BASELINE":
        return "PROMISING"
    return "EXPERIMENTAL"


def evaluate_lstm_forecast(
    dataset_npz: str | Path = "data/modeling/lstm_forecast_dataset.npz",
    model_path: str | Path = "models/lstm_forecast/latest/model.pt",
    output_dir: str | Path | None = None,
    device: str = "auto",
    normalization_ranges: dict[str, tuple[float, float]] | None = None,
    eval_batch_size: int = 1024,
) -> dict[str, Any]:
    np = _require_numpy()
    torch, _nn, _loader, _dataset = _require_torch()
    data = np.load(dataset_npz, allow_pickle=False)
    model, checkpoint, resolved_device = _load_model(model_path, device)
    target_names = tuple(str(item) for item in data["target_names"])
    target_indices = data["target_indices"].astype("int64")
    dataset_meta = _load_dataset_metadata(data)
    stored_ranges = _load_normalization_ranges(data)
    ranges = _merged_ranges(normalization_ranges or stored_ranges)
    horizon_steps = int(data["horizon_steps"][0]) if "horizon_steps" in data else None
    resample_interval_sec = int(data["resample_interval_sec"][0]) if "resample_interval_sec" in data else 60
    result: dict[str, Any] = {
        "model_path": str(model_path),
        "dataset_npz": str(dataset_npz),
        "device": resolved_device,
        "model_version": checkpoint.get("model_version", MODEL_VERSION),
        "created_at": _utc_now(),
        "target_names": list(target_names),
        "feature_names": list(str(item) for item in data["feature_names"]),
        "horizon_steps": horizon_steps,
        "horizon_minutes_assuming_60s_resample": horizon_steps,
        "forecast_horizon_minutes": (horizon_steps or 0) * resample_interval_sec / 60,
        "resample_interval_sec": resample_interval_sec,
        "window_size": int(data["window_size"][0]) if "window_size" in data else None,
        "normalization_ranges": {key: list(value) for key, value in ranges.items()},
        "dataset_meta": dataset_meta,
        "splits": {},
    }
    if eval_batch_size < 1:
        raise ValueError("eval_batch_size must be a positive integer")

    def predict_batches(x: Any) -> Any:
        preds = []
        with torch.no_grad():
            for start in range(0, x.shape[0], eval_batch_size):
                batch = torch.from_numpy(x[start:start + eval_batch_size]).to(resolved_device)
                preds.append(model(batch).detach().cpu().numpy())
        return np.concatenate(preds, axis=0) if preds else np.empty((0, len(target_names)), dtype=np.float32)

    result["eval_batch_size"] = eval_batch_size
    for split in ("train", "val", "test"):
        x = data[f"X_{split}"].astype("float32")
        y = data[f"y_{split}"].astype("float32")
        pred = predict_batches(x)
        baseline = x[:, -1, target_indices]
        lstm_metrics = _regression_metrics(y, pred, target_names)
        baseline_metrics = _regression_metrics(y, baseline, target_names)
        y_denorm = _denormalize_matrix(y, target_names, ranges)
        pred_denorm = _denormalize_matrix(pred, target_names, ranges)
        baseline_denorm = _denormalize_matrix(baseline, target_names, ranges)
        lstm_metrics["denormalized"] = _regression_metrics(y_denorm, pred_denorm, target_names)
        baseline_metrics["denormalized"] = _regression_metrics(y_denorm, baseline_denorm, target_names)
        result["splits"][split] = {
            "samples": int(x.shape[0]),
            "lstm": lstm_metrics,
            "last_value_baseline": baseline_metrics,
            "baseline_delta": _metric_deltas(lstm_metrics, baseline_metrics),
            "denormalized_baseline_delta": _metric_deltas(
                lstm_metrics["denormalized"], baseline_metrics["denormalized"]
            ),
        }
    result["nan_count"] = int(sum(np.isnan(data[name]).sum() for name in ("X_train", "y_train", "X_val", "y_val", "X_test", "y_test")))
    result["inf_count"] = int(sum(np.isinf(data[name]).sum() for name in ("X_train", "y_train", "X_val", "y_val", "X_test", "y_test")))
    result["data_status"] = "PASS" if result["nan_count"] == 0 and result["inf_count"] == 0 else "FAIL"
    result["baseline_comparison_status"] = _baseline_status(result["splits"]["test"])
    result["model_readiness"] = _model_readiness(result["data_status"], result["baseline_comparison_status"])
    result["status"] = result["data_status"]
    output_path = Path(model_path).parent if output_dir is None else Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    metrics_path = output_path / "metrics.json"
    result["metrics_ref"] = str(metrics_path)
    metrics_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _denormalize_values(
    target_names: tuple[str, ...],
    values: list[float],
    ranges: dict[str, tuple[float, float]] | None = None,
) -> dict[str, float]:
    configured = _merged_ranges(ranges)
    result: dict[str, float] = {}
    for name, value in zip(target_names, values, strict=True):
        low, high = configured.get(name, (0.0, 1.0))
        result[name] = float(low + (float(value) * (high - low)))
    return result


def predict_lstm_forecast(
    windows_jsonl: str | Path,
    model_path: str | Path,
    output_jsonl: str | Path,
    max_windows: int = 0,
    device: str = "auto",
    normalization_ranges: dict[str, tuple[float, float]] | None = None,
) -> Path:
    np = _require_numpy()
    torch, _nn, _loader, _dataset = _require_torch()
    model, checkpoint, resolved_device = _load_model(model_path, device)
    feature_names = tuple(checkpoint["feature_names"])
    target_names = tuple(checkpoint["target_names"])
    ranges = _merged_ranges(normalization_ranges or {
        key: (float(value[0]), float(value[1]))
        for key, value in checkpoint.get("normalization_ranges", {}).items()
    })
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
            prediction_normalized = [float(value) for value in pred]
            horizon_steps = checkpoint.get("horizon_steps")
            resample_interval_sec = checkpoint.get("resample_interval_sec", 60)
            out.write(json.dumps({
                "gateway_id": record["gateway_id"],
                "node_id": record["node_id"],
                "room_id": record["room_id"],
                "input_start_timestamp": record["start_timestamp"],
                "input_end_timestamp": record["end_timestamp"],
                "target_names": list(target_names),
                "prediction_normalized": prediction_normalized,
                "prediction_values": _denormalize_values(target_names, prediction_normalized, ranges),
                "model_version": checkpoint.get("model_version", MODEL_VERSION),
                "forecast_horizon_steps": horizon_steps,
                "forecast_horizon_minutes": (horizon_steps or 0) * resample_interval_sec / 60,
                "metrics_ref": checkpoint.get("metrics_ref"),
            }, separators=(",", ":")) + "\n")
            count += 1
            if max_windows and count >= max_windows:
                break
    return output_path


def _summary_columns(rows: list[dict[str, Any]]) -> list[str]:
    columns = [
        "run_id",
        "horizon_steps",
        "forecast_horizon_minutes",
        "window_size",
        "hidden_size",
        "epochs_ran",
        "data_status",
        "baseline_comparison_status",
        "model_readiness",
        "test_samples",
        "test_lstm_rmse",
        "test_baseline_rmse",
        "test_rmse_skill_score",
    ]
    for target in TARGET_NAMES:
        columns.extend([
            f"{target}_rmse",
            f"{target}_baseline_rmse",
            f"{target}_skill_score",
            f"{target}_denorm_rmse",
            f"{target}_baseline_denorm_rmse",
        ])
    columns.extend([key for key in rows[0] if key not in columns])
    return columns


def _write_experiment_summary(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    (output_dir / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if not rows:
        return
    with (output_dir / "summary.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=_summary_columns(rows), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bounded_skill(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _selection_score(row: dict[str, Any], weights: dict[str, float], prefer_readiness: bool) -> float:
    score = weights["overall"] * _bounded_skill(_safe_float(row.get("test_rmse_skill_score")))
    for target in TARGET_NAMES:
        score += weights[target] * _bounded_skill(_safe_float(row.get(f"{target}_skill_score")))
    status = row.get("baseline_comparison_status")
    if status == "BEATS_BASELINE":
        score += 0.10
    elif status == "MIXED":
        score += 0.03
    elif status == "UNDER_BASELINE":
        score -= 0.05
    if prefer_readiness and row.get("model_readiness") == "PROMISING":
        score += 0.05
    return float(score)


def _selection_row(row: dict[str, Any], score: float) -> dict[str, Any]:
    targets = {
        target: {
            "skill_score": _safe_float(row.get(f"{target}_skill_score")),
            "rmse": _safe_float(row.get(f"{target}_rmse")),
            "baseline_rmse": _safe_float(row.get(f"{target}_baseline_rmse")),
            "denorm_rmse": _safe_float(row.get(f"{target}_denorm_rmse")),
            "baseline_denorm_rmse": _safe_float(row.get(f"{target}_baseline_denorm_rmse")),
        }
        for target in TARGET_NAMES
    }
    return {
        "run_id": row.get("run_id"),
        "run_dir": row.get("run_dir"),
        "horizon_steps": int(_safe_float(row.get("horizon_steps"))),
        "forecast_horizon_minutes": _safe_float(row.get("forecast_horizon_minutes")),
        "window_size": int(_safe_float(row.get("window_size"))),
        "hidden_size": int(_safe_float(row.get("hidden_size"))),
        "epochs_ran": int(_safe_float(row.get("epochs_ran"))),
        "data_status": row.get("data_status"),
        "baseline_comparison_status": row.get("baseline_comparison_status"),
        "model_readiness": row.get("model_readiness"),
        "test_lstm_rmse": _safe_float(row.get("test_lstm_rmse")),
        "test_baseline_rmse": _safe_float(row.get("test_baseline_rmse")),
        "test_rmse_skill_score": _safe_float(row.get("test_rmse_skill_score")),
        "selection_score": score,
        "target_metrics": targets,
    }


def _target_notes(row: dict[str, Any]) -> dict[str, Any]:
    skills = {target: _safe_float(row.get(f"{target}_skill_score")) for target in TARGET_NAMES}
    strongest = max(skills, key=skills.get)
    weakest = min(skills, key=skills.get)
    return {
        "strongest_target": {"name": strongest, "skill_score": skills[strongest]},
        "weakest_target": {"name": weakest, "skill_score": skills[weakest]},
        "pressure_note": "pressure_hpa in Gary derived workflow is synthetic and should not be treated as real BME688 pressure validation",
        "air_quality_note": "co_raw and bme_gas_raw remain important for environmental risk, but all target sensors are included in selection",
    }


def select_best_forecast_model(
    summary_csv: str | Path,
    output_json: str | Path,
    top_k: int = 5,
    gas_weight: float = 0.25,
    co_weight: float = 0.25,
    temperature_weight: float = 0.15,
    humidity_weight: float = 0.15,
    pressure_weight: float = 0.10,
    overall_weight: float = 0.10,
    require_data_status: str = "PASS",
    prefer_readiness: bool = True,
) -> dict[str, Any]:
    summary_path = Path(summary_csv)
    with summary_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError(f"no experiment rows found in {summary_csv}")
    candidates = [row for row in rows if row.get("data_status") == require_data_status]
    if not candidates:
        raise ValueError(f"no rows with data_status={require_data_status}")
    weights = {
        **DEFAULT_SELECTION_WEIGHTS,
        "co_raw": co_weight,
        "bme_gas_raw": gas_weight,
        "temperature_c": temperature_weight,
        "humidity_pct": humidity_weight,
        "pressure_hpa": pressure_weight,
        "overall": overall_weight,
    }
    scored = [(row, _selection_score(row, weights, prefer_readiness)) for row in candidates]
    critical_scores_positive = any(
        _safe_float(row.get("test_rmse_skill_score")) > 0
        or _safe_float(row.get("co_raw_skill_score")) > 0
        or _safe_float(row.get("bme_gas_raw_skill_score")) > 0
        for row, _score in scored
    )
    if critical_scores_positive:
        selected_row, selected_score = max(scored, key=lambda item: item[1])
        selection_reason = "selected by weighted skill score across all target sensors"
    else:
        selected_row, selected_score = min(scored, key=lambda item: _safe_float(item[0].get("test_lstm_rmse"), math.inf))
        selection_reason = "all critical skill scores are below baseline; selected lowest normalized LSTM RMSE as tuning candidate"
    if selected_row.get("data_status") != "PASS":
        candidate_status = "NOT_READY"
    elif selected_row.get("baseline_comparison_status") == "BEATS_BASELINE" and selected_score > 0:
        candidate_status = "PROMISING"
    else:
        candidate_status = "NEEDS_TUNING"
    top_rows = sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]
    result = {
        "schema": SELECTION_SCHEMA,
        "created_at": _utc_now(),
        "source_summary": str(summary_path),
        "candidate_status": candidate_status,
        "selection_reason": selection_reason,
        "ranking_criteria": {
            "weights": weights,
            "require_data_status": require_data_status,
            "prefer_readiness": prefer_readiness,
            "status_adjustments": {
                "BEATS_BASELINE": 0.10,
                "MIXED": 0.03,
                "UNDER_BASELINE": -0.05,
                "PROMISING_READINESS": 0.05,
            },
            "fallback": "lowest test_lstm_rmse when overall/co_raw/bme_gas_raw skill scores are all below baseline",
        },
        "selected_run": _selection_row(selected_row, selected_score),
        "top_runs": [_selection_row(row, score) for row, score in top_rows],
        "target_notes": _target_notes(selected_row),
    }
    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _experiment_row(run_id: str, stats: ForecastDatasetStats, training: dict[str, Any], metrics: dict[str, Any], hidden_size: int) -> dict[str, Any]:
    test = metrics["splits"]["test"]
    row = {
        "run_id": run_id,
        "horizon_steps": stats.horizon_steps,
        "forecast_horizon_minutes": stats.horizon_steps * stats.resample_interval_sec / 60,
        "window_size": stats.window_size,
        "hidden_size": hidden_size,
        "epochs_ran": training["epochs_ran"],
        "data_status": metrics["data_status"],
        "baseline_comparison_status": metrics["baseline_comparison_status"],
        "model_readiness": metrics["model_readiness"],
        "test_samples": test["samples"],
        "test_lstm_mae": test["lstm"]["overall_mae"],
        "test_lstm_rmse": test["lstm"]["overall_rmse"],
        "test_baseline_mae": test["last_value_baseline"]["overall_mae"],
        "test_baseline_rmse": test["last_value_baseline"]["overall_rmse"],
        "test_rmse_skill_score": test["baseline_delta"]["overall_rmse_skill_score"],
        "input_shape": list(stats.input_shape),
        "target_shape": list(stats.target_shape),
        "run_dir": str(Path(training["model_path"]).parent),
    }
    for target in stats.target_names:
        row[f"{target}_rmse"] = test["lstm"]["per_target"][target]["rmse"]
        row[f"{target}_baseline_rmse"] = test["last_value_baseline"]["per_target"][target]["rmse"]
        row[f"{target}_skill_score"] = test["baseline_delta"]["per_target"][target]["rmse_skill_score"]
        row[f"{target}_denorm_rmse"] = test["lstm"]["denormalized"]["per_target"][target]["rmse"]
        row[f"{target}_baseline_denorm_rmse"] = test["last_value_baseline"]["denormalized"]["per_target"][target]["rmse"]
    return row


def run_forecast_experiments(
    windows_jsonl: str | Path = "data/processed/lstm_windows.jsonl",
    output_dir: str | Path = "models/forecast_experiments/latest",
    horizons: tuple[int, ...] = (5, 15, 30),
    hidden_sizes: tuple[int, ...] = (32, 64),
    epochs: int = 30,
    batch_size: int = 64,
    learning_rate: float = 0.001,
    num_layers: int = 1,
    patience: int = 5,
    seed: int = 42,
    device: str = "auto",
    window_sizes: tuple[int, ...] = (12,),
    resample_interval_sec: int = 60,
    normalization_ranges: dict[str, tuple[float, float]] | None = None,
    model_version: str = MODEL_VERSION,
    eval_batch_size: int = 1024,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    runs_path = output_path / "runs"
    runs_path.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, Any]] = []
    for window_size in window_sizes:
        if window_size < 1:
            raise ValueError("window_sizes must be positive integers")
        for horizon in horizons:
            if horizon < 1:
                raise ValueError("horizons must be positive integers")
            for hidden_size in hidden_sizes:
                if hidden_size < 1:
                    raise ValueError("hidden_sizes must be positive integers")
                run_id = f"w{window_size}_h{horizon}_hidden{hidden_size}"
                run_dir = runs_path / run_id
                dataset_path = run_dir / "dataset.npz"
                meta_path = run_dir / "dataset_meta.json"
                stats = prepare_forecast_dataset(
                    windows_jsonl,
                    dataset_path,
                    meta_path,
                    horizon,
                    resample_interval_sec=resample_interval_sec,
                    window_size=window_size,
                    normalization_ranges=normalization_ranges,
                )
                training = train_lstm_forecast(
                    dataset_path,
                    run_dir,
                    epochs,
                    batch_size,
                    learning_rate,
                    hidden_size,
                    num_layers,
                    patience,
                    seed,
                    device,
                    model_version,
                )
                metrics = evaluate_lstm_forecast(
                    dataset_path,
                    training["model_path"],
                    run_dir,
                    device,
                    normalization_ranges,
                    eval_batch_size,
                )
                summary.append(_experiment_row(run_id, stats, training, metrics, hidden_size))
                _write_experiment_summary(output_path, summary)
    return summary


def build_forecast_payload_v1(
    predictions_jsonl: str | Path,
    metrics_json: str | Path,
    output_jsonl: str | Path,
    metrics_ref: str | None = None,
) -> Path:
    metrics_path = Path(metrics_json)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    output_path = Path(output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ref = metrics_ref or str(metrics_path)
    with Path(predictions_jsonl).open(encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            prediction = json.loads(line)
            payload = {
                "schema": "iiot.ai_sensor.forecast.v1",
                "gateway_id": prediction["gateway_id"],
                "node_id": prediction["node_id"],
                "room_id": prediction["room_id"],
                "input_start_timestamp": prediction["input_start_timestamp"],
                "input_end_timestamp": prediction["input_end_timestamp"],
                "forecast_horizon_steps": prediction.get("forecast_horizon_steps") or metrics.get("horizon_steps"),
                "forecast_horizon_minutes": prediction.get("forecast_horizon_minutes") or metrics.get("forecast_horizon_minutes"),
                "predicted_sensor": prediction.get("prediction_values", {}),
                "model_version": prediction.get("model_version") or metrics.get("model_version", MODEL_VERSION),
                "metrics_ref": ref,
                "model_readiness": metrics.get("model_readiness", "EXPERIMENTAL"),
            }
            dst.write(json.dumps(payload, separators=(",", ":")) + "\n")
    return output_path
