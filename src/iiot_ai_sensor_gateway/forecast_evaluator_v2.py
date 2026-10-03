from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .dataset_quality import finite_or_raise, infer_cadence, select_active_features
from .window_paths import resolve_windows_path

DATASET_SCHEMA = "iiot.ai_sensor.forecast_dataset.v2"
METRICS_SCHEMA = "iiot.ai_sensor.forecast_metrics.v2"


def _np():
    import numpy as np

    return np


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_scalar(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _load_windows(path: str | Path) -> list[dict[str, Any]]:
    resolved = resolve_windows_path(path)
    records: list[dict[str, Any]] = []
    with Path(resolved).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid window JSON at line {line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"window line {line_number} must be an object")
            records.append(record)
    if not records:
        raise ValueError("window dataset is empty")
    return records


def _group_id(record: dict[str, Any]) -> str:
    gateway = str(record.get("gateway_id", "")).strip()
    node = str(record.get("node_id", "")).strip()
    room = str(record.get("room_id", "")).strip()
    if not gateway or not node or not room:
        raise ValueError("each forecast window requires gateway_id, node_id, and room_id")
    return f"{gateway}/{node}/{room}"


def _validate_window_schema(records: list[dict[str, Any]]) -> tuple[tuple[str, ...], int]:
    first_features = tuple(str(item) for item in records[0].get("feature_names", ()))
    first_shape = records[0].get("shape") or []
    if not first_features or len(first_shape) != 2:
        raise ValueError("window records require feature_names and shape=[sequence,features]")
    sequence_length = int(first_shape[0])
    if sequence_length < 2 or int(first_shape[1]) != len(first_features):
        raise ValueError("invalid first window shape")
    for index, record in enumerate(records):
        features = tuple(str(item) for item in record.get("feature_names", ()))
        shape = record.get("shape") or []
        x = record.get("x")
        if features != first_features:
            raise ValueError(f"feature schema changes at window index {index}")
        if len(shape) != 2 or int(shape[0]) != sequence_length or int(shape[1]) != len(first_features):
            raise ValueError(f"window shape changes at index {index}")
        if not isinstance(x, list) or len(x) != sequence_length:
            raise ValueError(f"window payload shape mismatch at index {index}")
        if any(not isinstance(row, list) or len(row) != len(first_features) for row in x):
            raise ValueError(f"window feature width mismatch at index {index}")
        if not isinstance(record.get("end_timestamp"), str):
            raise ValueError(f"window end_timestamp missing at index {index}")
    return first_features, sequence_length


@dataclass(frozen=True)
class _Sample:
    group_id: str
    input_start_timestamp: str
    anchor_timestamp: str
    label_timestamps: tuple[str, ...]
    x: Any
    y: Any
    seasonal: Any
    seasonal_available: Any


def _is_expected_delta(
    start: datetime,
    end: datetime,
    steps: int,
    cadence_seconds: float,
    tolerance_seconds: float,
) -> bool:
    return abs((end - start).total_seconds() - steps * cadence_seconds) <= tolerance_seconds


def _make_group_samples(
    records: list[dict[str, Any]],
    *,
    target_indices: tuple[int, ...],
    horizon_steps: int,
    cadence_seconds: float,
    tolerance_seconds: float,
    seasonal_period: int,
) -> list[_Sample]:
    np = _np()
    ordered = sorted(records, key=lambda item: _parse_dt(item["end_timestamp"]))
    timestamps = [_parse_dt(item["end_timestamp"]) for item in ordered]
    samples: list[_Sample] = []
    for anchor_index in range(0, len(ordered) - horizon_steps):
        anchor_time = timestamps[anchor_index]
        future_indices = [anchor_index + offset for offset in range(1, horizon_steps + 1)]
        if not all(
            _is_expected_delta(
                anchor_time,
                timestamps[future_index],
                offset,
                cadence_seconds,
                tolerance_seconds,
            )
            for offset, future_index in enumerate(future_indices, start=1)
        ):
            continue
        x = np.asarray(ordered[anchor_index]["x"], dtype=np.float32)
        y = np.asarray(
            [
                [ordered[future_index]["x"][-1][column] for column in target_indices]
                for future_index in future_indices
            ],
            dtype=np.float32,
        )
        seasonal = np.full((horizon_steps, len(target_indices)), np.nan, dtype=np.float32)
        available = np.zeros((horizon_steps,), dtype=bool)
        if seasonal_period > horizon_steps:
            for horizon_index in range(1, horizon_steps + 1):
                seasonal_index = anchor_index + horizon_index - seasonal_period
                if seasonal_index < 0:
                    continue
                if not _is_expected_delta(
                    timestamps[seasonal_index],
                    anchor_time,
                    seasonal_period - horizon_index,
                    cadence_seconds,
                    tolerance_seconds,
                ):
                    continue
                seasonal[horizon_index - 1] = np.asarray(
                    [ordered[seasonal_index]["x"][-1][column] for column in target_indices],
                    dtype=np.float32,
                )
                available[horizon_index - 1] = True
            if not bool(np.all(available)):
                # SeasonalNaive must be backed by history independent of the
                # model input window. Drop early anchors until that history is
                # genuinely available instead of silently disabling it later.
                continue
        samples.append(
            _Sample(
                group_id=_group_id(ordered[anchor_index]),
                input_start_timestamp=str(ordered[anchor_index]["start_timestamp"]),
                anchor_timestamp=ordered[anchor_index]["end_timestamp"],
                label_timestamps=tuple(ordered[index]["end_timestamp"] for index in future_indices),
                x=x,
                y=y,
                seasonal=seasonal,
                seasonal_available=available,
            )
        )
    return samples


def _split_group_samples(
    samples: list[_Sample],
    *,
    train_ratio: float,
    val_ratio: float,
    purge_gap_steps: int,
) -> dict[str, list[_Sample]]:
    if not 0.0 < train_ratio < 1.0 or not 0.0 < val_ratio < 1.0:
        raise ValueError("train_ratio and val_ratio must be in (0,1)")
    if train_ratio + val_ratio >= 1.0:
        raise ValueError("train_ratio + val_ratio must be < 1")
    if purge_gap_steps < 0:
        raise ValueError("purge_gap_steps must be non-negative")
    n = len(samples)
    train_end = max(1, int(math.floor(n * train_ratio)))
    val_end = max(train_end + 1, int(math.floor(n * (train_ratio + val_ratio))))
    val_start = min(n, train_end + purge_gap_steps)
    test_start = min(n, val_end + purge_gap_steps)
    result = {
        "train": samples[:train_end],
        "val": samples[val_start:val_end],
        "test": samples[test_start:],
    }
    train_label_end = max(
        (_parse_dt(sample.label_timestamps[-1]) for sample in result["train"]),
        default=None,
    )
    if train_label_end is not None:
        result["val"] = [
            sample
            for sample in result["val"]
            if _parse_dt(sample.input_start_timestamp) > train_label_end
        ]
    val_label_end = max(
        (_parse_dt(sample.label_timestamps[-1]) for sample in result["val"]),
        default=train_label_end,
    )
    if val_label_end is not None:
        result["test"] = [
            sample
            for sample in result["test"]
            if _parse_dt(sample.input_start_timestamp) > val_label_end
        ]
    if any(not result[name] for name in ("train", "val", "test")):
        raise ValueError(
            "not enough contiguous samples for train/val/test after purge; "
            f"n={n}, purge_gap_steps={purge_gap_steps}"
        )
    return result


def _assert_temporal_isolation(split: dict[str, list[_Sample]]) -> None:
    train_label_end = max(_parse_dt(s.label_timestamps[-1]) for s in split["train"])
    val_input_start = min(_parse_dt(s.input_start_timestamp) for s in split["val"])
    val_label_end = max(_parse_dt(s.label_timestamps[-1]) for s in split["val"])
    test_input_start = min(_parse_dt(s.input_start_timestamp) for s in split["test"])
    if val_input_start <= train_label_end:
        raise ValueError("train/validation temporal leakage detected")
    if test_input_start <= val_label_end:
        raise ValueError("validation/test temporal leakage detected")


def _quality_report(y_by_split: dict[str, Any], target_names: tuple[str, ...]) -> dict[str, Any]:
    np = _np()
    splits: dict[str, Any] = {}
    blocked: dict[str, list[str]] = defaultdict(list)
    for split_name, values in y_by_split.items():
        if values.ndim != 3:
            raise ValueError("v2 forecast targets must have shape [samples,horizon,targets]")
        targets: dict[str, Any] = {}
        for target_index, target_name in enumerate(target_names):
            column = values[:, :, target_index].reshape(-1)
            span = float(np.max(column) - np.min(column))
            std = float(np.std(column))
            lower = float(np.mean(column <= 1e-7))
            upper = float(np.mean(column >= 1.0 - 1e-7))
            targets[target_name] = {
                "sample_count": int(column.size),
                "span": span,
                "std": std,
                "near_constant": bool(span <= 1e-6 or std <= 1e-6),
                "lower_boundary_fraction": lower,
                "upper_boundary_fraction": upper,
                "max_boundary_fraction": max(lower, upper),
            }
            if split_name in ("train", "test"):
                if targets[target_name]["near_constant"]:
                    blocked[target_name].append(f"{split_name}_near_constant")
                if targets[target_name]["max_boundary_fraction"] > 0.05:
                    blocked[target_name].append(f"{split_name}_boundary_saturation")
        splits[split_name] = {"targets": targets}
    effective = [name for name in target_names if name not in blocked]
    status = "PASS" if not blocked else ("WARN" if effective else "FAIL")
    return {
        "status": status,
        "effective_target_names": effective,
        "blocked_targets": dict(blocked),
        "splits": splits,
    }


def _mase_scale_from_samples(
    samples: Iterable[_Sample],
    *,
    target_count: int,
    cadence_seconds: float,
    lag: int,
    tolerance_seconds: float,
) -> Any:
    np = _np()
    by_group: dict[str, dict[str, Any]] = defaultdict(dict)
    for sample in samples:
        for timestamp, row in zip(sample.label_timestamps, sample.y):
            by_group[sample.group_id][timestamp] = np.asarray(row, dtype=np.float64)
    deltas: list[Any] = []
    for points in by_group.values():
        ordered = sorted(((_parse_dt(ts), value) for ts, value in points.items()), key=lambda item: item[0])
        for index in range(lag, len(ordered)):
            previous_time, previous = ordered[index - lag]
            current_time, current = ordered[index]
            if not _is_expected_delta(
                previous_time,
                current_time,
                lag,
                cadence_seconds,
                tolerance_seconds,
            ):
                continue
            deltas.append(np.abs(current - previous))
    if not deltas:
        return np.full((target_count,), np.nan, dtype=np.float64)
    return np.mean(np.stack(deltas, axis=0), axis=0)


def _rolling_origin_manifest(
    split_by_group: dict[str, dict[str, list[_Sample]]],
    folds: int,
) -> list[dict[str, Any]]:
    if folds < 1:
        raise ValueError("rolling_origin_folds must be >= 1")
    output: list[dict[str, Any]] = []
    for fold in range(folds):
        fold_groups: dict[str, Any] = {}
        usable = True
        for group_id, split in split_by_group.items():
            pool = split["train"] + split["val"]
            if len(pool) < 6:
                usable = False
                break
            min_train = max(2, len(pool) // 2)
            remaining = len(pool) - min_train
            validation_width = max(1, remaining // (folds + 1))
            train_end = min_train + fold * validation_width
            val_start = train_end
            val_end = min(len(pool), val_start + validation_width)
            if val_end <= val_start or train_end < 2:
                usable = False
                break
            fold_groups[group_id] = {
                "train_start": pool[0].anchor_timestamp,
                "train_end": pool[train_end - 1].anchor_timestamp,
                "validation_start": pool[val_start].anchor_timestamp,
                "validation_end": pool[val_end - 1].anchor_timestamp,
                "train_samples": train_end,
                "validation_samples": val_end - val_start,
            }
        if usable and fold_groups:
            output.append({"fold": fold + 1, "groups": fold_groups})
    return output


def _group_holdout_manifest(split_by_group: dict[str, dict[str, list[_Sample]]]) -> list[dict[str, Any]]:
    groups = sorted(split_by_group)
    if len(groups) < 2:
        return []
    return [
        {
            "held_out_group": group_id,
            "train_groups": [item for item in groups if item != group_id],
            "held_out_samples": sum(
                len(split_by_group[group_id][split]) for split in ("train", "val", "test")
            ),
        }
        for group_id in groups
    ]


def build_forecast_dataset_v2(
    windows_jsonl: str | Path,
    output_npz: str | Path,
    output_meta: str | Path,
    *,
    target_names: tuple[str, ...],
    horizon_steps: int = 5,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    purge_gap_steps: int | None = None,
    declared_cadence_sec: float | None = None,
    cadence_relative_tolerance: float = 0.10,
    max_irregular_fraction: float = 0.05,
    seasonal_period: int = 0,
    rolling_origin_folds: int = 3,
    near_constant_epsilon: float = 1e-8,
    normalization_ranges: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Build a leakage-resistant contiguous multi-horizon forecast dataset.

    Input windows must already be produced at the intended runtime cadence.
    This function validates timestamp cadence; it never relabels a high-rate
    source as a lower-rate horizon.
    """

    if horizon_steps < 1:
        raise ValueError("horizon_steps must be >= 1")
    if seasonal_period < 0:
        raise ValueError("seasonal_period must be >= 0")
    records = _load_windows(windows_jsonl)
    source_features, sequence_length = _validate_window_schema(records)
    missing = [name for name in target_names if name not in source_features]
    if missing:
        raise ValueError(f"forecast targets missing from feature schema: {missing}")
    source_target_indices = tuple(source_features.index(name) for name in target_names)
    cadence = infer_cadence(
        records,
        declared_interval_sec=declared_cadence_sec,
        relative_tolerance=cadence_relative_tolerance,
        max_irregular_fraction=max_irregular_fraction,
    )
    cadence_seconds = float(cadence["cadence_seconds"])
    tolerance_seconds = float(cadence["tolerance_seconds"])
    purge = horizon_steps if purge_gap_steps is None else purge_gap_steps
    if purge < horizon_steps:
        raise ValueError(
            "purge_gap_steps must be >= horizon_steps for contiguous multi-horizon labels"
        )

    by_group_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_group_records[_group_id(record)].append(record)
    split_by_group: dict[str, dict[str, list[_Sample]]] = {}
    for group_id, group_records in sorted(by_group_records.items()):
        samples = _make_group_samples(
            group_records,
            target_indices=source_target_indices,
            horizon_steps=horizon_steps,
            cadence_seconds=cadence_seconds,
            tolerance_seconds=tolerance_seconds,
            seasonal_period=seasonal_period,
        )
        if not samples:
            raise ValueError(f"group {group_id} has no contiguous multi-horizon samples")
        split = _split_group_samples(
            samples,
            train_ratio=train_ratio,
            val_ratio=val_ratio,
            purge_gap_steps=purge,
        )
        _assert_temporal_isolation(split)
        split_by_group[group_id] = split

    combined: dict[str, list[_Sample]] = {
        split: [
            sample
            for group_id in sorted(split_by_group)
            for sample in split_by_group[group_id][split]
        ]
        for split in ("train", "val", "test")
    }
    np = _np()
    arrays: dict[str, Any] = {}
    for split, samples in combined.items():
        arrays[f"X_{split}"] = np.stack([sample.x for sample in samples]).astype(np.float32)
        arrays[f"Y_{split}"] = np.stack([sample.y for sample in samples]).astype(np.float32)
        arrays[f"group_id_{split}"] = np.asarray([sample.group_id for sample in samples])
        arrays[f"anchor_timestamp_{split}"] = np.asarray(
            [sample.anchor_timestamp for sample in samples]
        )
        arrays[f"label_timestamps_{split}"] = np.asarray(
            [sample.label_timestamps for sample in samples]
        )
        arrays[f"seasonal_baseline_{split}"] = np.stack(
            [sample.seasonal for sample in samples]
        ).astype(np.float32)
        arrays[f"seasonal_available_{split}"] = np.stack(
            [sample.seasonal_available for sample in samples]
        ).astype(bool)
    finite_or_raise(
        {
            name: value
            for name, value in arrays.items()
            if name.startswith("X_") or name.startswith("Y_")
        }
    )

    feature_manifest = select_active_features(
        arrays["X_train"],
        source_features,
        target_names,
        near_constant_epsilon=near_constant_epsilon,
    )
    active_indices = np.asarray(feature_manifest.pop("active_indices"), dtype=np.int64)
    feature_names = tuple(feature_manifest["ordered_features"])
    for split in ("train", "val", "test"):
        arrays[f"X_{split}"] = arrays[f"X_{split}"][:, :, active_indices]
    target_indices = tuple(feature_names.index(name) for name in target_names)

    quality = _quality_report(
        {split: arrays[f"Y_{split}"] for split in ("train", "val", "test")},
        target_names,
    )
    mase_lag = seasonal_period if seasonal_period > horizon_steps else 1
    mase_scale = _mase_scale_from_samples(
        combined["train"],
        target_count=len(target_names),
        cadence_seconds=cadence_seconds,
        lag=mase_lag,
        tolerance_seconds=tolerance_seconds,
    )
    ranges = {
        key: [float(value[0]), float(value[1])]
        for key, value in (normalization_ranges or {}).items()
    }
    rolling = _rolling_origin_manifest(split_by_group, rolling_origin_folds)
    group_holdouts = _group_holdout_manifest(split_by_group)
    meta = {
        "schema": DATASET_SCHEMA,
        "source_windows": str(resolve_windows_path(windows_jsonl)),
        "horizon_steps": horizon_steps,
        "horizon_duration_seconds": horizon_steps * cadence_seconds,
        "cadence_seconds": cadence_seconds,
        "cadence_diagnostics": cadence,
        "sequence_length": sequence_length,
        "purge_gap_steps": purge,
        "train_ratio": train_ratio,
        "val_ratio": val_ratio,
        "final_holdout": "test_newest_time",
        "feature_names": list(feature_names),
        "feature_manifest": feature_manifest,
        "target_names": list(target_names),
        "target_indices": list(target_indices),
        "seasonal_period": seasonal_period,
        "seasonal_baseline_policy": (
            "independent_history"
            if seasonal_period > horizon_steps
            else "unavailable_period_must_exceed_horizon"
        ),
        "mase_scale_source": "training_unique_observations_only",
        "mase_lag": mase_lag,
        "mase_scale_normalized": [
            None if not math.isfinite(float(value)) else float(value) for value in mase_scale
        ],
        "data_quality": quality,
        "rolling_origin_folds": rolling,
        "group_holdouts": group_holdouts,
        "groups": sorted(split_by_group),
        "split_samples": {split: len(combined[split]) for split in ("train", "val", "test")},
        "normalization_ranges": ranges,
        "promotion_policy": {
            "selection_split": "validation_or_rolling_origin_only",
            "final_holdout_must_remain_untouched": True,
            "group_generalization_required_when_multiple_groups": bool(group_holdouts),
            "field_accuracy_claim_allowed": False,
        },
    }
    output_path = Path(output_npz)
    meta_path = Path(output_meta)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        **arrays,
        feature_names=np.asarray(feature_names),
        target_names=np.asarray(target_names),
        target_indices=np.asarray(target_indices, dtype=np.int64),
        horizon_steps=np.asarray([horizon_steps], dtype=np.int64),
        cadence_seconds=np.asarray([cadence_seconds], dtype=np.float64),
        sequence_length=np.asarray([sequence_length], dtype=np.int64),
        purge_gap_steps=np.asarray([purge], dtype=np.int64),
        mase_scale=np.asarray(mase_scale, dtype=np.float64),
        feature_schema_sha256=np.asarray([feature_manifest["schema_sha256"]]),
        dataset_meta_json=np.asarray([json.dumps(meta, sort_keys=True)]),
        normalization_ranges_json=np.asarray([json.dumps(ranges, sort_keys=True)]),
    )
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def load_forecast_dataset_v2(path: str | Path) -> dict[str, Any]:
    np = _np()
    data = np.load(path, allow_pickle=False)
    required = {
        "X_train",
        "Y_train",
        "X_val",
        "Y_val",
        "X_test",
        "Y_test",
        "feature_names",
        "target_names",
        "target_indices",
        "mase_scale",
        "dataset_meta_json",
    }
    missing = sorted(required.difference(data.files))
    if missing:
        raise ValueError(f"v2 forecast dataset missing arrays: {missing}")
    result = {name: data[name] for name in data.files}
    meta = _json_scalar(result["dataset_meta_json"], {})
    if meta.get("schema") != DATASET_SCHEMA:
        raise ValueError(f"unsupported forecast dataset schema: {meta.get('schema')}")
    result["meta"] = meta
    result["feature_names_tuple"] = tuple(str(item) for item in result["feature_names"])
    result["target_names_tuple"] = tuple(str(item) for item in result["target_names"])
    finite_or_raise(
        {
            name: result[name]
            for name in ("X_train", "Y_train", "X_val", "Y_val", "X_test", "Y_test")
        }
    )
    return result


def baseline_candidates_v2(data: dict[str, Any], split: str) -> dict[str, dict[str, Any]]:
    np = _np()
    x = data[f"X_{split}"].astype(np.float64)
    y = data[f"Y_{split}"].astype(np.float64)
    target_indices = data["target_indices"].astype(np.int64)
    horizon_steps = int(y.shape[1])
    last = x[:, -1, target_indices]
    mean = np.mean(x[:, :, target_indices], axis=1)
    candidates: dict[str, dict[str, Any]] = {
        "last_value": {
            "applicable": True,
            "reason": None,
            "predictions": np.repeat(last[:, None, :], horizon_steps, axis=1),
        },
        "window_mean": {
            "applicable": True,
            "reason": None,
            "predictions": np.repeat(mean[:, None, :], horizon_steps, axis=1),
        },
    }
    if x.shape[1] >= 2:
        slope = last - x[:, -2, target_indices]
        multipliers = np.arange(1, horizon_steps + 1, dtype=np.float64)[None, :, None]
        candidates["drift"] = {
            "applicable": True,
            "reason": None,
            "predictions": last[:, None, :] + multipliers * slope[:, None, :],
        }
    else:
        candidates["drift"] = {
            "applicable": False,
            "reason": "requires_at_least_two_history_steps",
            "predictions": None,
        }
    seasonal = data.get(f"seasonal_baseline_{split}")
    available = data.get(f"seasonal_available_{split}")
    if seasonal is None or available is None or not bool(np.all(available)):
        candidates["seasonal_naive"] = {
            "applicable": False,
            "reason": "independent_seasonal_history_not_available_for_all_samples_and_horizons",
            "predictions": None,
        }
    else:
        candidates["seasonal_naive"] = {
            "applicable": True,
            "reason": None,
            "predictions": seasonal.astype(np.float64),
        }
    return candidates


def regression_metrics_v2(
    actual: Any,
    predicted: Any,
    target_names: tuple[str, ...],
    mase_scale: Any,
) -> dict[str, Any]:
    np = _np()
    actual = np.asarray(actual, dtype=np.float64)
    predicted = np.asarray(predicted, dtype=np.float64)
    if actual.shape != predicted.shape or actual.ndim != 3:
        raise ValueError("forecast metrics require matching [samples,horizon,targets] arrays")
    scale = np.asarray(mase_scale, dtype=np.float64)
    if scale.shape != (actual.shape[2],):
        raise ValueError("MASE scale shape does not match target count")
    error = predicted - actual
    per_horizon: dict[str, Any] = {}
    for horizon in range(actual.shape[1]):
        horizon_error = error[:, horizon, :]
        mae = np.mean(np.abs(horizon_error), axis=0)
        rmse = np.sqrt(np.mean(horizon_error * horizon_error, axis=0))
        mase = np.divide(
            mae,
            scale,
            out=np.full_like(mae, np.nan, dtype=np.float64),
            where=np.isfinite(scale) & (scale > 0),
        )
        per_horizon[str(horizon + 1)] = {
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
    target_mae = np.mean(np.abs(error), axis=(0, 1))
    target_rmse = np.sqrt(np.mean(error * error, axis=(0, 1)))
    target_mase = np.divide(
        target_mae,
        scale,
        out=np.full_like(target_mae, np.nan, dtype=np.float64),
        where=np.isfinite(scale) & (scale > 0),
    )
    return {
        "overall_mae": float(np.mean(np.abs(error))),
        "overall_rmse": float(np.sqrt(np.mean(error * error))),
        "overall_mase": None if np.isnan(target_mase).all() else float(np.nanmean(target_mase)),
        "per_target": {
            name: {
                "mae": float(target_mae[index]),
                "rmse": float(target_rmse[index]),
                "mase": None if math.isnan(float(target_mase[index])) else float(target_mase[index]),
            }
            for index, name in enumerate(target_names)
        },
        "per_horizon": per_horizon,
    }


def _select_baseline_v2(data: dict[str, Any]) -> dict[str, Any]:
    np = _np()
    actual = data["Y_val"].astype(np.float64)
    candidates = baseline_candidates_v2(data, "val")
    names = data["target_names_tuple"]
    selected: dict[str, dict[str, str]] = {}
    applicability: dict[str, Any] = {}
    usable = {
        name: item["predictions"]
        for name, item in candidates.items()
        if item.get("applicable") and item.get("predictions") is not None
    }
    if not usable:
        raise ValueError("no applicable v2 forecast baselines")
    for name, item in candidates.items():
        applicability[name] = {
            "applicable": bool(item.get("applicable")),
            "reason": item.get("reason"),
        }
    for horizon in range(actual.shape[1]):
        horizon_key = str(horizon + 1)
        selected[horizon_key] = {}
        for target_index, target_name in enumerate(names):
            selected[horizon_key][target_name] = min(
                usable,
                key=lambda candidate_name: float(
                    np.sqrt(
                        np.mean(
                            (
                                usable[candidate_name][:, horizon, target_index]
                                - actual[:, horizon, target_index]
                            )
                            ** 2
                        )
                    )
                ),
            )
    return {"selected_by_horizon_target": selected, "applicability": applicability}


def compose_selected_baseline_v2(
    data: dict[str, Any], split: str, selection: dict[str, Any]
) -> Any:
    np = _np()
    candidates = baseline_candidates_v2(data, split)
    target_names = data["target_names_tuple"]
    y = data[f"Y_{split}"]
    output = np.empty_like(y, dtype=np.float64)
    for horizon in range(y.shape[1]):
        for target_index, target_name in enumerate(target_names):
            baseline_name = selection["selected_by_horizon_target"][str(horizon + 1)][target_name]
            candidate = candidates.get(baseline_name)
            if not candidate or not candidate.get("applicable") or candidate.get("predictions") is None:
                raise ValueError(
                    f"selected baseline {baseline_name} unavailable for {split} h={horizon + 1} {target_name}"
                )
            output[:, horizon, target_index] = candidate["predictions"][:, horizon, target_index]
    return output


def evaluate_baselines_v2(dataset_npz: str | Path, output_json: str | Path | None = None) -> dict[str, Any]:
    data = load_forecast_dataset_v2(dataset_npz)
    selection = _select_baseline_v2(data)
    result: dict[str, Any] = {
        "schema": METRICS_SCHEMA,
        "dataset": str(dataset_npz),
        "model_type": "selected_independent_baselines",
        "baseline_selection_split": "validation",
        "selection": selection,
        "splits": {},
    }
    for split in ("train", "val", "test"):
        predictions = compose_selected_baseline_v2(data, split, selection)
        result["splits"][split] = regression_metrics_v2(
            data[f"Y_{split}"],
            predictions,
            data["target_names_tuple"],
            data["mase_scale"],
        )
    if output_json is not None:
        output = Path(output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def export_direct_horizon_datasets(
    dataset_npz: str | Path,
    output_dir: str | Path,
) -> list[str]:
    """Export v1-compatible direct-horizon datasets for legacy model comparators."""

    np = _np()
    data = load_forecast_dataset_v2(dataset_npz)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    meta = data["meta"]
    cadence = float(data["cadence_seconds"][0])
    for horizon_index in range(data["Y_train"].shape[1]):
        horizon_steps = horizon_index + 1
        path = output / f"horizon_{horizon_steps:02d}.npz"
        direct_meta = {
            **meta,
            "schema": "iiot.ai_sensor.forecast_dataset.direct_adapter.v1",
            "source_v2_dataset": str(dataset_npz),
            "direct_horizon_steps": horizon_steps,
        }
        np.savez_compressed(
            path,
            X_train=data["X_train"],
            y_train=data["Y_train"][:, horizon_index, :],
            X_val=data["X_val"],
            y_val=data["Y_val"][:, horizon_index, :],
            X_test=data["X_test"],
            y_test=data["Y_test"][:, horizon_index, :],
            feature_names=data["feature_names"],
            target_names=data["target_names"],
            target_indices=data["target_indices"],
            horizon_steps=np.asarray([horizon_steps], dtype=np.int64),
            cadence_seconds=np.asarray([cadence], dtype=np.float64),
            resample_interval_sec=np.asarray([int(round(cadence))], dtype=np.int64),
            horizon_duration_seconds=np.asarray([horizon_steps * cadence], dtype=np.float64),
            window_size=np.asarray([data["X_train"].shape[1]], dtype=np.int64),
            purge_gap_steps=data["purge_gap_steps"],
            feature_schema_sha256=data["feature_schema_sha256"],
            feature_manifest_json=np.asarray(
                [json.dumps(meta.get("feature_manifest", {}), sort_keys=True)]
            ),
            data_quality_json=np.asarray(
                [json.dumps(meta.get("data_quality", {}), sort_keys=True)]
            ),
            cadence_diagnostics_json=np.asarray(
                [json.dumps(meta.get("cadence_diagnostics", {}), sort_keys=True)]
            ),
            normalization_ranges_json=np.asarray(
                [json.dumps(meta.get("normalization_ranges", {}), sort_keys=True)]
            ),
            dataset_meta_json=np.asarray([json.dumps(direct_meta, sort_keys=True)]),
        )
        paths.append(str(path))
    return paths
