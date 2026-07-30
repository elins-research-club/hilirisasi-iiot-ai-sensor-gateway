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

from .dataset_quality import (
    array_quality_report,
    finite_or_raise,
    infer_cadence,
    select_active_features,
)
from .features import FEATURE_NAMES
from .forecast_baselines import (
    baseline_candidates,
    compose_selected_baseline,
    select_baseline_per_target,
)
from .normalization import DEFAULT_RANGES
from .window_paths import resolve_windows_path

TARGET_NAMES = (
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "co_ppm",
    "o3_ppm",
    "co2_ppm",
    "pm25_ug_m3",
)
MODEL_VERSION = "lstm_forecast_v1"
SELECTION_SCHEMA = "iiot.ai_sensor.forecast_model_selection.v1"
FORECAST_DECISION_SCHEMA = "iiot.ai_sensor.forecast_decision.v1"
FORECAST_STRATEGIES = ("absolute", "residual")
DEFAULT_SELECTION_WEIGHTS = {
    "temperature_c": 0.10,
    "humidity_pct": 0.08,
    "pressure_hpa": 0.07,
    "co_ppm": 0.20,
    "o3_ppm": 0.15,
    "co2_ppm": 0.15,
    "pm25_ug_m3": 0.15,
    "overall": 0.10,
}
SEVERITY_RANK = {"normal": 0, "warning": 1, "critical": 2}
DECISION_FACTOR_PRIORITY = {
    "co_ppm": 0,
    "o3_ppm": 1,
    "pm25_ug_m3": 2,
    "co2_ppm": 3,
    "temperature_c": 4,
    "humidity_pct": 5,
    "pressure_hpa": 6,
    "model_readiness": 7,
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
    cadence_seconds: float
    horizon_duration_seconds: float
    cadence_diagnostics: dict[str, Any]
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
    feature_manifest: dict[str, Any]
    data_quality: dict[str, Any]
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
    windows_path = resolve_windows_path(windows_jsonl)
    records: list[dict[str, Any]] = []
    for line in windows_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        shape = record.get("shape") or []
        if len(shape) != 2 or int(shape[0]) <= 0 or int(shape[1]) <= 0:
            raise ValueError(f"invalid window shape: {shape}")
        records.append(record)
    if not records:
        raise ValueError(f"no windows found in {windows_path}")
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
    # Allow lane-local feature schemas (e.g. legacy Gary 16-feature windows) as
    # long as every record is consistent. Target selection still requires the
    # requested target names to exist inside the chosen feature schema.
    if not feature_names:
        raise ValueError("window feature_names must be non-empty")
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


def _read_window_records_capped(
    path: str | Path,
    *,
    max_window_records: int = 0,
) -> list[dict[str, Any]]:
    """Load window JSONL with an optional hard cap for huge public lanes.

    Cap strategy: keep the first and last temporal slices overall after a cheap
    line count pass when needed. For uncapped paths this streams once.
    """

    path = resolve_windows_path(path)
    if max_window_records < 0:
        raise ValueError("max_window_records must be >= 0")

    if not max_window_records:
        records = _read_window_records(path)
        if not records:
            raise ValueError("window dataset is empty")
        return records

    # Two-pass only when capped: count lines, then read selected indices.
    total = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                total += 1
    if total == 0:
        raise ValueError("window dataset is empty")
    if total <= max_window_records:
        records = _read_window_records(path)
        if not records:
            raise ValueError("window dataset is empty")
        return records

    head = max_window_records // 2
    tail = max_window_records - head
    keep_head = set(range(head))
    keep_tail = set(range(total - tail, total))
    keep = keep_head | keep_tail
    records: list[dict[str, Any]] = []
    index = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if index in keep:
                records.append(json.loads(line))
            index += 1
    if not records:
        raise ValueError("window dataset is empty after cap")
    # Sort by end timestamp so temporal splits remain ordered.
    records.sort(key=lambda item: (_parse_dt(item["end_timestamp"]), item.get("node_id", "")))
    return records


def prepare_forecast_dataset(
    windows_jsonl: str | Path,
    output_npz: str | Path = "data/modeling/lstm_forecast_dataset.npz",
    output_meta: str | Path = "data/modeling/lstm_forecast_dataset_meta.json",
    horizon_steps: int = 5,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    target_names: tuple[str, ...] = TARGET_NAMES,
    resample_interval_sec: int | None = None,
    window_size: int | None = None,
    purge_gap_steps: int | None = None,
    normalization_ranges: dict[str, tuple[float, float]] | None = None,
    cadence_relative_tolerance: float = 0.10,
    max_irregular_fraction: float = 0.05,
    near_constant_epsilon: float = 1e-8,
    max_samples_per_split: int = 0,
    max_window_records: int = 0,
) -> ForecastDatasetStats:
    windows_jsonl = resolve_windows_path(windows_jsonl)
    if horizon_steps < 1:
        raise ValueError("horizon_steps must be >= 1")
    if max_window_records < 0:
        raise ValueError("max_window_records must be >= 0")
    np = _require_numpy()
    # Prefer native window size to avoid rebuilding huge public lanes in RAM.
    # When a larger window is requested, rebuild only after a hard record cap so
    # laptop-class machines never materialize hundreds of thousands of long windows.
    selected_window_size = window_size
    if selected_window_size is None or selected_window_size < 1:
        # peek first record for native size without loading the whole file
        with Path(windows_jsonl).open(encoding="utf-8") as handle:
            first_line = next((line for line in handle if line.strip()), "")
        if not first_line:
            raise ValueError("window dataset is empty")
        first = json.loads(first_line)
        selected_window_size = int(first["shape"][0])
    records = _read_window_records_capped(
        windows_jsonl,
        max_window_records=max_window_records,
    )
    native_size = int(records[0]["shape"][0])
    if selected_window_size != native_size:
        # Rebuild is O(N * W) in RAM; force a safe default cap when caller forgot.
        effective_cap = max_window_records or 60000
        if len(records) > effective_cap:
            # keep temporally uniform head/tail mix already applied by reader
            records = records[:effective_cap]
        records = _rebuild_windows(records, selected_window_size)
    source_feature_names = _ensure_feature_names(records)
    missing_targets = [name for name in target_names if name not in source_feature_names]
    if missing_targets:
        raise ValueError(f"forecast targets missing from feature schema: {missing_targets}")
    source_target_indices = tuple(source_feature_names.index(name) for name in target_names)
    cadence = infer_cadence(
        records,
        declared_interval_sec=(
            float(resample_interval_sec) if resample_interval_sec is not None else None
        ),
        relative_tolerance=cadence_relative_tolerance,
        max_irregular_fraction=max_irregular_fraction,
    )
    cadence_seconds = float(cadence["cadence_seconds"])
    selected_purge_gap = horizon_steps if purge_gap_steps is None else purge_gap_steps
    if selected_purge_gap < 0:
        raise ValueError("purge_gap_steps must be >= 0")

    by_node: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_node.setdefault(record["node_id"], []).append(record)

    if max_samples_per_split < 0:
        raise ValueError("max_samples_per_split must be >= 0")
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
        samples = _make_samples(node_records, horizon_steps, source_target_indices)
        split_samples = _assign_temporal_splits(samples, train_ratio, val_ratio, selected_purge_gap)
        for split, items in split_samples.items():
            for sample in items:
                # Cap during collection so huge public lanes never materialize
                # hundreds of thousands of windows before np.asarray.
                if max_samples_per_split and len(arrays[f"X_{split}"]) >= max_samples_per_split:
                    continue
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
    finite_or_raise(np_arrays)

    feature_manifest = select_active_features(
        np_arrays["X_train"],
        source_feature_names,
        target_names,
        near_constant_epsilon=near_constant_epsilon,
    )
    active_indices = np.asarray(feature_manifest.pop("active_indices"), dtype=np.int64)
    feature_names = tuple(feature_manifest["ordered_features"])
    for split in ("train", "val", "test"):
        np_arrays[f"X_{split}"] = np_arrays[f"X_{split}"][:, :, active_indices]
    target_indices = tuple(feature_names.index(name) for name in target_names)
    data_quality = array_quality_report(np_arrays, target_names)

    created_at = _utc_now()
    total_samples = sum(int(np_arrays[name].shape[0]) for name in ("X_train", "X_val", "X_test"))
    input_shape = tuple(int(item) for item in np_arrays["X_train"].shape[1:])
    target_shape = tuple(int(item) for item in np_arrays["y_train"].shape[1:])
    compatibility_interval = int(round(cadence_seconds))
    horizon_duration_seconds = cadence_seconds * horizon_steps
    stats = ForecastDatasetStats(
        source_windows=str(windows_jsonl),
        output_npz=str(output_path),
        output_meta=str(meta_path),
        horizon_steps=horizon_steps,
        resample_interval_sec=compatibility_interval,
        cadence_seconds=cadence_seconds,
        horizon_duration_seconds=horizon_duration_seconds,
        cadence_diagnostics=cadence,
        window_size=selected_window_size,
        purge_gap_steps=selected_purge_gap,
        feature_names=feature_names,
        target_names=target_names,
        target_indices=target_indices,
        total_samples=total_samples,
        train_samples=int(np_arrays["X_train"].shape[0]),
        val_samples=int(np_arrays["X_val"].shape[0]),
        test_samples=int(np_arrays["X_test"].shape[0]),
        input_shape=(total_samples, input_shape[0], input_shape[1]),
        target_shape=(total_samples, target_shape[0]),
        nodes=tuple(sorted(by_node)),
        start_timestamp=min(starts) if starts else None,
        end_timestamp=max(ends) if ends else None,
        split_time_range=split_time_range,
        normalization_ranges=merged_ranges,
        feature_manifest=feature_manifest,
        data_quality=data_quality,
        created_at=created_at,
    )
    dataset_meta = stats.as_dict()
    np.savez_compressed(
        output_path,
        **np_arrays,
        feature_names=np.asarray(feature_names),
        target_names=np.asarray(target_names),
        target_indices=np.asarray(target_indices, dtype=np.int64),
        horizon_steps=np.asarray([horizon_steps], dtype=np.int64),
        resample_interval_sec=np.asarray([compatibility_interval], dtype=np.int64),
        cadence_seconds=np.asarray([cadence_seconds], dtype=np.float64),
        horizon_duration_seconds=np.asarray([horizon_duration_seconds], dtype=np.float64),
        window_size=np.asarray([selected_window_size], dtype=np.int64),
        purge_gap_steps=np.asarray([selected_purge_gap], dtype=np.int64),
        feature_schema_sha256=np.asarray([feature_manifest["schema_sha256"]]),
        feature_manifest_json=np.asarray([json.dumps(feature_manifest, sort_keys=True)]),
        data_quality_json=np.asarray([json.dumps(data_quality, sort_keys=True)]),
        cadence_diagnostics_json=np.asarray([json.dumps(cadence, sort_keys=True)]),
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


def build_lstm_forecaster(
    input_size: int,
    output_size: int,
    hidden_size: int = 64,
    num_layers: int = 1,
    dropout: float = 0.0,
):
    _torch, nn, _loader, _dataset = _require_torch()
    if hidden_size < 1 or num_layers < 1:
        raise ValueError("hidden_size and num_layers must be >= 1")
    if not 0.0 <= dropout < 1.0:
        raise ValueError("dropout must be in [0, 1)")

    class LSTMForecaster(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.lstm = nn.LSTM(
                input_size,
                hidden_size,
                num_layers=num_layers,
                batch_first=True,
                dropout=dropout if num_layers > 1 else 0.0,
            )
            self.head = nn.Linear(hidden_size, output_size)
            # Residual-friendly start: near-zero head keeps early predictions near baseline.
            nn.init.xavier_uniform_(self.lstm.weight_ih_l0)
            nn.init.orthogonal_(self.lstm.weight_hh_l0)
            nn.init.zeros_(self.head.weight)
            nn.init.zeros_(self.head.bias)

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
    forecast_strategy: str = "residual",
    dropout: float = 0.1,
    weight_decay: float = 1e-4,
    grad_clip_norm: float = 1.0,
) -> dict[str, Any]:
    np = _require_numpy()
    torch, nn, DataLoader, TensorDataset = _require_torch()
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if weight_decay < 0 or grad_clip_norm < 0:
        raise ValueError("weight_decay and grad_clip_norm must be non-negative")

    data = np.load(dataset_npz, allow_pickle=False)
    X_train = data["X_train"].astype("float32")
    y_train = data["y_train"].astype("float32")
    X_val = data["X_val"].astype("float32")
    y_val = data["y_val"].astype("float32")
    feature_names = tuple(str(item) for item in data["feature_names"])
    target_names = tuple(str(item) for item in data["target_names"])
    target_indices = data["target_indices"].astype("int64")
    dataset_meta = _load_dataset_metadata(data)
    normalization_ranges = _load_normalization_ranges(data)
    resolved_device = _device_name(device)
    if forecast_strategy not in FORECAST_STRATEGIES:
        raise ValueError(f"forecast_strategy must be one of {FORECAST_STRATEGIES}")

    y_train_fit = y_train
    y_val_fit = y_val
    if forecast_strategy == "residual":
        y_train_fit = y_train - X_train[:, -1, target_indices]
        y_val_fit = y_val - X_val[:, -1, target_indices]

    model = build_lstm_forecaster(
        X_train.shape[2],
        y_train.shape[1],
        hidden_size,
        num_layers,
        dropout=dropout,
    ).to(resolved_device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=max(2, patience // 2)
    )
    criterion = nn.SmoothL1Loss(beta=0.01)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train_fit.astype("float32"))),
        batch_size=min(batch_size, max(1, len(X_train))),
        shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val_fit.astype("float32"))),
        batch_size=min(batch_size, max(1, len(X_val))),
        shuffle=False,
    )

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
            if grad_clip_norm > 0:
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip_norm)
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
        scheduler.step(val_loss)
        history.append(
            {
                "epoch": epoch,
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
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.pt"
    created_at = _utc_now()
    model_metadata = {
        "model_version": model_version,
        "forecast_strategy": forecast_strategy,
        "created_at": created_at,
        "horizon_steps": int(data["horizon_steps"][0]) if "horizon_steps" in data else None,
        "resample_interval_sec": int(data["resample_interval_sec"][0]) if "resample_interval_sec" in data else 60,
        "cadence_seconds": float(data["cadence_seconds"][0]) if "cadence_seconds" in data else float(data["resample_interval_sec"][0]),
        "horizon_duration_seconds": float(data["horizon_duration_seconds"][0]) if "horizon_duration_seconds" in data else None,
        "window_size": int(data["window_size"][0]) if "window_size" in data else int(X_train.shape[1]),
        "feature_names": list(feature_names),
        "feature_schema_sha256": str(data["feature_schema_sha256"][0]) if "feature_schema_sha256" in data else None,
        "feature_manifest": _loads_json_array_value(data.get("feature_manifest_json"), {}),
        "data_quality": _loads_json_array_value(data.get("data_quality_json"), {}),
        "target_names": list(target_names),
        "target_indices": [int(index) for index in target_indices],
        "normalization_ranges": {key: list(value) for key, value in normalization_ranges.items()},
        "dataset_meta": dataset_meta,
        "metrics_ref": str(out_dir / "metrics.json"),
        "dropout": dropout,
        "weight_decay": weight_decay,
        "grad_clip_norm": grad_clip_norm,
        "status": "EXPERIMENTAL",
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
    try:
        checkpoint = torch.load(
            model_path,
            map_location=resolved_device,
            weights_only=True,
        )
    except Exception as exc:
        raise ValueError(f"unsafe or invalid forecast checkpoint rejected: {model_path}") from exc
    if not isinstance(checkpoint, dict):
        raise ValueError("forecast checkpoint must be a mapping")
    required = {"state_dict", "input_size", "output_size", "hidden_size", "num_layers"}
    missing = sorted(required.difference(checkpoint))
    if missing:
        raise ValueError(f"forecast checkpoint is missing required keys: {missing}")
    if not isinstance(checkpoint["state_dict"], dict):
        raise ValueError("forecast checkpoint state_dict must be a mapping")
    model = build_lstm_forecaster(
        int(checkpoint["input_size"]),
        int(checkpoint["output_size"]),
        int(checkpoint["hidden_size"]),
        int(checkpoint["num_layers"]),
        dropout=float(checkpoint.get("dropout", 0.0)),
    ).to(resolved_device)
    model.load_state_dict(checkpoint["state_dict"], strict=True)
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


def _apply_forecast_strategy(raw_prediction: Any, x: Any, target_indices: Any, strategy: str) -> Any:
    if strategy == "residual":
        return x[:, -1, target_indices] + raw_prediction
    return raw_prediction


def _baseline_status(
    test_metrics: dict[str, Any],
    *,
    effective_target_names: tuple[str, ...] | None = None,
) -> str:
    """Align LSTM gate with edge majority-win policy.

    Historical all-target-win rule marked strong models EXPERIMENTAL when one
    low-variance target (e.g. synthetic Gary pressure) lost while overall skill
    and majority targets improved.
    """

    per_target = test_metrics["baseline_delta"]["per_target"]
    names = effective_target_names or tuple(per_target.keys())
    if not names:
        return "UNDER_BASELINE"
    wins = sum(1 for name in names if per_target[name]["beats_baseline"])
    total = len(names)
    overall_better = test_metrics["baseline_delta"]["overall_rmse_delta"] < 0
    majority = wins >= math.ceil(total / 2)
    if overall_better and majority:
        return "BEATS_BASELINE"
    if wins == 0 and not overall_better:
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
    seasonal_period: int = 0,
) -> dict[str, Any]:
    np = _require_numpy()
    torch, _nn, _loader, _dataset = _require_torch()
    data = np.load(dataset_npz, allow_pickle=False)
    model, checkpoint, resolved_device = _load_model(model_path, device)
    target_names = tuple(str(item) for item in data["target_names"])
    feature_names = tuple(str(item) for item in data["feature_names"])
    target_indices = data["target_indices"].astype("int64")
    if tuple(checkpoint.get("feature_names", ())) != feature_names:
        raise ValueError("LSTM checkpoint feature schema does not match dataset")
    if tuple(checkpoint.get("target_names", ())) != target_names:
        raise ValueError("LSTM checkpoint target schema does not match dataset")
    forecast_strategy = str(checkpoint.get("forecast_strategy", "absolute"))
    dataset_meta = _load_dataset_metadata(data)
    data_quality = _loads_json_array_value(data.get("data_quality_json"), {}) or dataset_meta.get("data_quality", {})
    feature_manifest = _loads_json_array_value(data.get("feature_manifest_json"), {}) or dataset_meta.get("feature_manifest", {})
    cadence_diagnostics = _loads_json_array_value(data.get("cadence_diagnostics_json"), {}) or dataset_meta.get("cadence_diagnostics", {})
    stored_ranges = _load_normalization_ranges(data)
    ranges = _merged_ranges(normalization_ranges or stored_ranges)
    horizon_steps = int(data["horizon_steps"][0]) if "horizon_steps" in data else 1
    cadence_seconds = float(data["cadence_seconds"][0]) if "cadence_seconds" in data else float(data["resample_interval_sec"][0])
    horizon_duration_seconds = float(data["horizon_duration_seconds"][0]) if "horizon_duration_seconds" in data else horizon_steps * cadence_seconds
    result: dict[str, Any] = {
        "model_path": str(model_path),
        "dataset_npz": str(dataset_npz),
        "device": resolved_device,
        "model_version": checkpoint.get("model_version", MODEL_VERSION),
        "forecast_strategy": forecast_strategy,
        "created_at": _utc_now(),
        "target_names": list(target_names),
        "feature_names": list(feature_names),
        "feature_manifest": feature_manifest,
        "data_quality": data_quality,
        "cadence_diagnostics": cadence_diagnostics,
        "horizon_steps": horizon_steps,
        "horizon_duration_seconds": horizon_duration_seconds,
        "forecast_horizon_minutes": horizon_duration_seconds / 60.0,
        "resample_interval_sec": int(round(cadence_seconds)),
        "window_size": int(data["window_size"][0]) if "window_size" in data else None,
        "normalization_ranges": {key: list(value) for key, value in ranges.items()},
        "dataset_meta": dataset_meta,
        "seasonal_period": seasonal_period,
        "baseline_selection_split": "val",
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
        output = np.concatenate(preds, axis=0) if preds else np.empty((0, len(target_names)), dtype=np.float32)
        if output.shape[0] != x.shape[0]:
            raise RuntimeError("LSTM evaluator dropped or duplicated samples")
        return output

    split_candidates: dict[str, dict[str, dict[str, Any]]] = {}
    split_actual: dict[str, Any] = {}
    result["eval_batch_size"] = eval_batch_size
    for split in ("train", "val", "test"):
        x = data[f"X_{split}"].astype("float32")
        y = data[f"y_{split}"].astype("float32")
        candidates = baseline_candidates(
            x,
            target_indices,
            horizon_steps=horizon_steps,
            seasonal_period=seasonal_period,
        )
        split_candidates[split] = candidates
        split_actual[split] = y
        raw_pred = predict_batches(x)
        pred = _apply_forecast_strategy(raw_pred, x, target_indices, forecast_strategy)
        lstm_metrics = _regression_metrics(y, pred, target_names)
        y_denorm = _denormalize_matrix(y, target_names, ranges)
        pred_denorm = _denormalize_matrix(pred, target_names, ranges)
        lstm_metrics["denormalized"] = _regression_metrics(y_denorm, pred_denorm, target_names)
        baseline_metrics: dict[str, Any] = {}
        for name, item in candidates.items():
            if not item["applicable"] or item["predictions"] is None:
                baseline_metrics[name] = {"applicable": False, "reason": item["reason"]}
                continue
            metrics = _regression_metrics(y, item["predictions"], target_names)
            metrics["denormalized"] = _regression_metrics(
                y_denorm,
                _denormalize_matrix(item["predictions"], target_names, ranges),
                target_names,
            )
            baseline_metrics[name] = {"applicable": True, "reason": None, **metrics}
        last_value_metrics = baseline_metrics["last_value"]
        result["splits"][split] = {
            "samples": int(x.shape[0]),
            "lstm": lstm_metrics,
            "last_value_baseline": {
                key: value for key, value in last_value_metrics.items() if key not in {"applicable", "reason"}
            },
            "baselines": baseline_metrics,
        }

    selection = select_baseline_per_target(
        split_actual["val"], split_candidates["val"], target_names
    )
    result["baseline_selection"] = selection
    for split in ("train", "val", "test"):
        selected = compose_selected_baseline(split_candidates[split], selection, target_names)
        baseline_metrics = _regression_metrics(split_actual[split], selected, target_names)
        baseline_metrics["denormalized"] = _regression_metrics(
            _denormalize_matrix(split_actual[split], target_names, ranges),
            _denormalize_matrix(selected, target_names, ranges),
            target_names,
        )
        lstm_metrics = result["splits"][split]["lstm"]
        result["splits"][split]["validation_selected_baseline"] = baseline_metrics
        result["splits"][split]["baseline_delta"] = _metric_deltas(lstm_metrics, baseline_metrics)
        result["splits"][split]["denormalized_baseline_delta"] = _metric_deltas(
            lstm_metrics["denormalized"], baseline_metrics["denormalized"]
        )

    result["nan_count"] = int(sum(np.isnan(data[name]).sum() for name in ("X_train", "y_train", "X_val", "y_val", "X_test", "y_test")))
    result["inf_count"] = int(sum(np.isinf(data[name]).sum() for name in ("X_train", "y_train", "X_val", "y_val", "X_test", "y_test")))
    result["data_status"] = "PASS" if result["nan_count"] == 0 and result["inf_count"] == 0 else "FAIL"
    effective_targets = tuple(data_quality.get("effective_target_names", target_names))
    if not effective_targets:
        effective_targets = target_names
    result["baseline_comparison_status"] = _baseline_status(
        result["splits"]["test"],
        effective_target_names=effective_targets,
    )
    test_split = result["splits"]["test"]
    model_rmse = float(test_split["lstm"]["overall_rmse"])
    baseline_rmse = float(test_split["validation_selected_baseline"]["overall_rmse"])
    per_target_wins = sum(
        1
        for name in effective_targets
        if test_split["baseline_delta"]["per_target"][name]["beats_baseline"]
    )
    quality_passed = bool(
        data_quality.get("status") == "PASS"
        and len(effective_targets) == len(target_names)
    )
    baseline_passed = result["baseline_comparison_status"] == "BEATS_BASELINE"
    result["baseline_gate"] = {
        "best_baseline": "validation_selected_per_target",
        "selected_by_target": selection["selected_by_target"],
        "model_rmse": model_rmse,
        "baseline_rmse": baseline_rmse,
        "rmse_skill_score": None if baseline_rmse == 0 else 1.0 - model_rmse / baseline_rmse,
        "per_target_wins": per_target_wins,
        "effective_target_count": len(effective_targets),
        "target_count": len(target_names),
        "baseline_passed": baseline_passed,
        "data_quality_passed": quality_passed,
        "passed": bool(baseline_passed and quality_passed and result["data_status"] == "PASS"),
    }
    if result["data_status"] != "PASS":
        result["model_readiness"] = "NOT_READY"
    elif result["baseline_gate"]["passed"]:
        result["model_readiness"] = "PROMISING"
    else:
        result["model_readiness"] = "EXPERIMENTAL"
    result["quality_gate_passed"] = quality_passed
    result["status"] = "PASS" if result["baseline_gate"]["passed"] else result["data_status"]
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
    target_indices = np.asarray(
        checkpoint.get("target_indices", [feature_names.index(name) for name in target_names]),
        dtype=np.int64,
    )
    forecast_strategy = str(checkpoint.get("forecast_strategy", "absolute"))
    ranges = _merged_ranges(normalization_ranges or {
        key: (float(value[0]), float(value[1]))
        for key, value in checkpoint.get("normalization_ranges", {}).items()
    })
    output_path = Path(output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as out:
        for record in _read_window_records(windows_jsonl):
            source_feature_names = tuple(record["feature_names"])
            missing_features = [name for name in feature_names if name not in source_feature_names]
            if missing_features:
                raise ValueError(
                    f"window feature schema is missing trained features: {missing_features}"
                )
            selected_indices = [source_feature_names.index(name) for name in feature_names]
            selected_x = [
                [row[index] for index in selected_indices]
                for row in record["x"]
            ]
            x = np.asarray([selected_x], dtype=np.float32)
            if x.shape[2] != int(checkpoint["input_size"]):
                raise ValueError("selected window feature count does not match checkpoint input_size")
            with torch.no_grad():
                raw_pred = model(torch.from_numpy(x).to(resolved_device)).detach().cpu().numpy()
                pred = _apply_forecast_strategy(raw_pred, x, target_indices, forecast_strategy)[0]
            prediction_normalized = [float(value) for value in pred]
            horizon_steps = checkpoint.get("horizon_steps")
            cadence_seconds = float(
                checkpoint.get("cadence_seconds", checkpoint.get("resample_interval_sec", 60))
            )
            horizon_duration_seconds = checkpoint.get("horizon_duration_seconds")
            if horizon_duration_seconds is None:
                horizon_duration_seconds = (horizon_steps or 0) * cadence_seconds
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
                "forecast_strategy": forecast_strategy,
                "forecast_horizon_steps": horizon_steps,
                "forecast_horizon_seconds": horizon_duration_seconds,
                "forecast_horizon_minutes": float(horizon_duration_seconds) / 60.0,
                "feature_schema_sha256": checkpoint.get("feature_schema_sha256"),
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
        "pressure_note": "pressure_hpa from any derived Gary workflow is synthetic and is excluded from real-sensor validation claims",
        "air_quality_note": "CO, O3, CO2, and PM2.5 targets are evaluated only when the selected dataset lane actually contains those measurements",
    }


def select_best_forecast_model(
    summary_csv: str | Path,
    output_json: str | Path,
    top_k: int = 5,
    co_weight: float = 0.20,
    o3_weight: float = 0.15,
    co2_weight: float = 0.15,
    pm25_weight: float = 0.15,
    temperature_weight: float = 0.10,
    humidity_weight: float = 0.08,
    pressure_weight: float = 0.07,
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
        "co_ppm": co_weight,
        "o3_ppm": o3_weight,
        "co2_ppm": co2_weight,
        "pm25_ug_m3": pm25_weight,
        "temperature_c": temperature_weight,
        "humidity_pct": humidity_weight,
        "pressure_hpa": pressure_weight,
        "overall": overall_weight,
    }
    scored = [(row, _selection_score(row, weights, prefer_readiness)) for row in candidates]
    critical_scores_positive = any(
        _safe_float(row.get("test_rmse_skill_score")) > 0
        or _safe_float(row.get("co_ppm_skill_score")) > 0
        or _safe_float(row.get("o3_ppm_skill_score")) > 0
        or _safe_float(row.get("co2_ppm_skill_score")) > 0
        or _safe_float(row.get("pm25_ug_m3_skill_score")) > 0
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
            "fallback": "lowest test_lstm_rmse when overall and measured air-quality target skill scores are all below baseline",
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
        "forecast_horizon_minutes": stats.horizon_duration_seconds / 60.0,
        "cadence_seconds": stats.cadence_seconds,
        "window_size": stats.window_size,
        "hidden_size": hidden_size,
        "forecast_strategy": training.get("forecast_strategy", "absolute"),
        "epochs_ran": training["epochs_ran"],
        "data_status": metrics["data_status"],
        "baseline_comparison_status": metrics["baseline_comparison_status"],
        "model_readiness": metrics["model_readiness"],
        "test_samples": test["samples"],
        "test_lstm_mae": test["lstm"]["overall_mae"],
        "test_lstm_rmse": test["lstm"]["overall_rmse"],
        "test_baseline_mae": test["validation_selected_baseline"]["overall_mae"],
        "test_baseline_rmse": test["validation_selected_baseline"]["overall_rmse"],
        "test_rmse_skill_score": test["baseline_delta"]["overall_rmse_skill_score"],
        "input_shape": list(stats.input_shape),
        "target_shape": list(stats.target_shape),
        "run_dir": str(Path(training["model_path"]).parent),
    }
    for target in stats.target_names:
        row[f"{target}_rmse"] = test["lstm"]["per_target"][target]["rmse"]
        row[f"{target}_baseline_rmse"] = test["validation_selected_baseline"]["per_target"][target]["rmse"]
        row[f"{target}_skill_score"] = test["baseline_delta"]["per_target"][target]["rmse_skill_score"]
        row[f"{target}_denorm_rmse"] = test["lstm"]["denormalized"]["per_target"][target]["rmse"]
        row[f"{target}_baseline_denorm_rmse"] = test["validation_selected_baseline"]["denormalized"]["per_target"][target]["rmse"]
    return row


def run_forecast_experiments(
    windows_jsonl: str | Path = "data/processed/windows.jsonl",
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
    resample_interval_sec: int | None = None,
    normalization_ranges: dict[str, tuple[float, float]] | None = None,
    model_version: str = MODEL_VERSION,
    eval_batch_size: int = 1024,
    forecast_strategy: str = "absolute",
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
                    forecast_strategy,
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


def _decision_rule(severity: str, factor: str, reason: str) -> dict[str, str]:
    return {"severity": severity, "factor": factor, "reason": reason}


def _pick_decision(rules: list[dict[str, str]]) -> dict[str, str]:
    if not rules:
        return _decision_rule("normal", "model_readiness", "all forecast values are inside v1 rule thresholds")
    return min(
        rules,
        key=lambda item: (
            -SEVERITY_RANK[item["severity"]],
            DECISION_FACTOR_PRIORITY[item["factor"]],
        ),
    )


def _forecast_decision(predicted_sensor: dict[str, Any], model_readiness: str) -> dict[str, str]:
    rules: list[dict[str, str]] = []
    if model_readiness == "NOT_READY":
        rules.append(_decision_rule("warning", "model_readiness", "model_readiness is NOT_READY"))
    co_ppm = _safe_float(predicted_sensor.get("co_ppm"), math.nan)
    o3_ppm = _safe_float(predicted_sensor.get("o3_ppm"), math.nan)
    co2_ppm = _safe_float(predicted_sensor.get("co2_ppm"), math.nan)
    pm25_ug_m3 = _safe_float(predicted_sensor.get("pm25_ug_m3"), math.nan)
    temperature_c = _safe_float(predicted_sensor.get("temperature_c"), math.nan)
    humidity_pct = _safe_float(predicted_sensor.get("humidity_pct"), math.nan)
    pressure_hpa = _safe_float(predicted_sensor.get("pressure_hpa"), math.nan)
    # These are project commissioning defaults, not regulatory safety limits.
    # Production thresholds remain configuration-owned and require domain review.
    if math.isfinite(co_ppm):
        if co_ppm >= 35.0:
            rules.append(_decision_rule("critical", "co_ppm", "CO forecast exceeded project critical threshold"))
        elif co_ppm >= 9.0:
            rules.append(_decision_rule("warning", "co_ppm", "CO forecast exceeded project warning threshold"))
    if math.isfinite(o3_ppm):
        if o3_ppm >= 0.10:
            rules.append(_decision_rule("critical", "o3_ppm", "O3 forecast exceeded project critical threshold"))
        elif o3_ppm >= 0.07:
            rules.append(_decision_rule("warning", "o3_ppm", "O3 forecast exceeded project warning threshold"))
    if math.isfinite(co2_ppm):
        if co2_ppm >= 2000.0:
            rules.append(_decision_rule("critical", "co2_ppm", "CO2 forecast exceeded project critical threshold"))
        elif co2_ppm >= 1000.0:
            rules.append(_decision_rule("warning", "co2_ppm", "CO2 forecast exceeded project warning threshold"))
    if math.isfinite(pm25_ug_m3):
        if pm25_ug_m3 >= 75.0:
            rules.append(_decision_rule("critical", "pm25_ug_m3", "PM2.5 forecast exceeded project critical threshold"))
        elif pm25_ug_m3 >= 35.0:
            rules.append(_decision_rule("warning", "pm25_ug_m3", "PM2.5 forecast exceeded project warning threshold"))
    if math.isfinite(temperature_c):
        if temperature_c >= 38:
            rules.append(_decision_rule("critical", "temperature_c", "temperature_c forecast exceeded critical threshold"))
        elif temperature_c >= 35:
            rules.append(_decision_rule("warning", "temperature_c", "temperature_c forecast exceeded warning threshold"))
        elif temperature_c <= 10:
            rules.append(_decision_rule("warning", "temperature_c", "temperature_c forecast is below low warning threshold"))
    if math.isfinite(humidity_pct):
        if humidity_pct >= 90:
            rules.append(_decision_rule("critical", "humidity_pct", "humidity_pct forecast exceeded critical threshold"))
        elif humidity_pct >= 85:
            rules.append(_decision_rule("warning", "humidity_pct", "humidity_pct forecast exceeded warning threshold"))
        elif humidity_pct <= 25:
            rules.append(_decision_rule("warning", "humidity_pct", "humidity_pct forecast is below low warning threshold"))
    if math.isfinite(pressure_hpa):
        if pressure_hpa >= 1025:
            rules.append(_decision_rule("warning", "pressure_hpa", "pressure_hpa forecast exceeded warning threshold"))
        elif pressure_hpa <= 995:
            rules.append(_decision_rule("warning", "pressure_hpa", "pressure_hpa forecast is below low warning threshold"))
    return _pick_decision(rules)


def build_forecast_decision_v1(
    forecast_payloads_jsonl: str | Path,
    output_jsonl: str | Path,
) -> Path:
    output_path = Path(output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Path(forecast_payloads_jsonl).open(encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            forecast = json.loads(line)
            predicted_sensor = forecast.get("predicted_sensor") or {}
            model_readiness = forecast.get("model_readiness", "EXPERIMENTAL")
            decision = _forecast_decision(predicted_sensor, model_readiness)
            payload = {
                "schema": FORECAST_DECISION_SCHEMA,
                "gateway_id": forecast["gateway_id"],
                "node_id": forecast["node_id"],
                "room_id": forecast["room_id"],
                "input_start_timestamp": forecast["input_start_timestamp"],
                "input_end_timestamp": forecast["input_end_timestamp"],
                "forecast_horizon_minutes": forecast.get("forecast_horizon_minutes"),
                "env_status": decision["severity"],
                "main_factor": decision["factor"],
                "reason": decision["reason"],
                "predicted_sensor": predicted_sensor,
                "model_version": forecast.get("model_version", MODEL_VERSION),
                "model_readiness": model_readiness,
                "metrics_ref": forecast.get("metrics_ref"),
            }
            dst.write(json.dumps(payload, separators=(",", ":")) + "\n")
    return output_path
