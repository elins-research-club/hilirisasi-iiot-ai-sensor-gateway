from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any, Iterable


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def infer_cadence(
    records: Iterable[dict[str, Any]],
    *,
    declared_interval_sec: float | None = None,
    relative_tolerance: float = 0.10,
    max_irregular_fraction: float = 0.05,
) -> dict[str, Any]:
    """Infer a robust base cadence from per-node end timestamps.

    Missing samples may produce integer multiples of the base cadence and are
    reported as gaps rather than irregular cadence. A declared cadence is a
    validation constraint, not a replacement for timestamp evidence.
    """

    if declared_interval_sec is not None and declared_interval_sec <= 0:
        raise ValueError("declared_interval_sec must be positive when provided")
    if not 0.0 <= relative_tolerance < 1.0:
        raise ValueError("relative_tolerance must be in [0, 1)")
    if not 0.0 <= max_irregular_fraction < 1.0:
        raise ValueError("max_irregular_fraction must be in [0, 1)")

    by_node: dict[str, list[datetime]] = defaultdict(list)
    for record in records:
        node_id = str(record.get("node_id", "")).strip()
        timestamp = record.get("end_timestamp")
        if not node_id or not isinstance(timestamp, str):
            raise ValueError("cadence inference requires node_id and end_timestamp")
        by_node[node_id].append(_parse_dt(timestamp))

    deltas_by_node: dict[str, list[float]] = {}
    all_deltas: list[float] = []
    for node_id, timestamps in sorted(by_node.items()):
        ordered = sorted(set(timestamps))
        deltas = [
            (current - previous).total_seconds()
            for previous, current in zip(ordered, ordered[1:])
            if (current - previous).total_seconds() > 0
        ]
        if deltas:
            deltas_by_node[node_id] = deltas
            all_deltas.extend(deltas)
    if not all_deltas:
        raise ValueError("cannot infer cadence: fewer than two unique timestamps per node")

    base = float(median(all_deltas))
    if declared_interval_sec is not None:
        allowed = max(1.0, declared_interval_sec * relative_tolerance)
        if abs(base - declared_interval_sec) > allowed:
            raise ValueError(
                f"declared cadence {declared_interval_sec:g}s conflicts with observed median {base:g}s"
            )
        base = float(declared_interval_sec)
    tolerance_sec = max(1.0, base * relative_tolerance)

    irregular = 0
    gap_count = 0
    exact_or_multiple = 0
    node_summaries: dict[str, Any] = {}
    for node_id, deltas in deltas_by_node.items():
        node_irregular = 0
        node_gaps = 0
        for delta in deltas:
            multiple = max(1, round(delta / base))
            if abs(delta - multiple * base) <= tolerance_sec:
                exact_or_multiple += 1
                if multiple > 1:
                    gap_count += multiple - 1
                    node_gaps += multiple - 1
            else:
                irregular += 1
                node_irregular += 1
        node_summaries[node_id] = {
            "observed_delta_count": len(deltas),
            "median_delta_sec": float(median(deltas)),
            "min_delta_sec": float(min(deltas)),
            "max_delta_sec": float(max(deltas)),
            "irregular_delta_count": node_irregular,
            "inferred_missing_steps": node_gaps,
        }

    total = len(all_deltas)
    irregular_fraction = irregular / total
    if irregular_fraction > max_irregular_fraction:
        raise ValueError(
            "timestamp cadence is too irregular: "
            f"{irregular}/{total} deltas ({irregular_fraction:.3%}) exceed "
            f"{max_irregular_fraction:.3%}"
        )
    return {
        "cadence_seconds": base,
        "cadence_source": "declared_validated" if declared_interval_sec is not None else "timestamp_inferred",
        "relative_tolerance": relative_tolerance,
        "tolerance_seconds": tolerance_sec,
        "observed_delta_count": total,
        "regular_or_gap_delta_count": exact_or_multiple,
        "irregular_delta_count": irregular,
        "irregular_fraction": irregular_fraction,
        "inferred_missing_steps": gap_count,
        "nodes": node_summaries,
    }


def select_active_features(
    x_train: Any,
    feature_names: tuple[str, ...],
    target_names: tuple[str, ...],
    *,
    near_constant_epsilon: float = 1e-8,
) -> dict[str, Any]:
    import numpy as np

    if x_train.ndim != 3 or x_train.shape[2] != len(feature_names):
        raise ValueError("x_train shape does not match feature_names")
    flattened = x_train.reshape(-1, x_train.shape[2])
    minimum = np.min(flattened, axis=0)
    maximum = np.max(flattened, axis=0)
    span = maximum - minimum
    target_set = set(target_names)
    active_indices = tuple(
        index
        for index, name in enumerate(feature_names)
        if float(span[index]) > near_constant_epsilon or name in target_set
    )
    if not active_indices:
        raise ValueError("no active features remain after train-only constant-feature analysis")
    active_names = tuple(feature_names[index] for index in active_indices)
    dropped = tuple(name for index, name in enumerate(feature_names) if index not in active_indices)
    manifest_payload = {
        "ordered_features": list(active_names),
        "dropped_train_constant_features": list(dropped),
        "near_constant_epsilon": near_constant_epsilon,
    }
    manifest_hash = hashlib.sha256(
        json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **manifest_payload,
        "active_indices": active_indices,
        "schema_sha256": manifest_hash,
        "train_span": {name: float(span[index]) for index, name in enumerate(feature_names)},
    }


def array_quality_report(
    arrays: dict[str, Any],
    target_names: tuple[str, ...],
    *,
    boundary_tolerance: float = 1e-7,
    near_constant_epsilon: float = 1e-6,
    max_boundary_fraction_for_promotion: float = 0.05,
) -> dict[str, Any]:
    import numpy as np

    split_reports: dict[str, Any] = {}
    effective_targets: list[str] = []
    blocked_targets: dict[str, list[str]] = {}
    for split in ("train", "val", "test"):
        y = arrays[f"y_{split}"]
        if y.ndim != 2 or y.shape[1] != len(target_names):
            raise ValueError(f"y_{split} shape does not match target_names")
        per_target: dict[str, Any] = {}
        for index, name in enumerate(target_names):
            values = y[:, index]
            span = float(np.max(values) - np.min(values)) if values.size else 0.0
            std = float(np.std(values)) if values.size else 0.0
            lower = float(np.mean(values <= boundary_tolerance)) if values.size else 1.0
            upper = float(np.mean(values >= 1.0 - boundary_tolerance)) if values.size else 1.0
            per_target[name] = {
                "sample_count": int(values.size),
                "minimum": float(np.min(values)) if values.size else None,
                "maximum": float(np.max(values)) if values.size else None,
                "span": span,
                "std": std,
                "near_constant": bool(span <= near_constant_epsilon or std <= near_constant_epsilon),
                "lower_boundary_fraction": lower,
                "upper_boundary_fraction": upper,
                "max_boundary_fraction": max(lower, upper),
            }
        split_reports[split] = {"targets": per_target}

    for name in target_names:
        reasons: list[str] = []
        for split in ("train", "test"):
            report = split_reports[split]["targets"][name]
            if report["near_constant"]:
                reasons.append(f"{split}_near_constant")
            if report["max_boundary_fraction"] > max_boundary_fraction_for_promotion:
                reasons.append(f"{split}_boundary_saturation")
        if reasons:
            blocked_targets[name] = reasons
        else:
            effective_targets.append(name)

    if not effective_targets:
        status = "FAIL"
    elif blocked_targets:
        status = "WARN"
    else:
        status = "PASS"
    return {
        "status": status,
        "effective_target_names": effective_targets,
        "effective_target_count": len(effective_targets),
        "target_count": len(target_names),
        "blocked_targets": blocked_targets,
        "near_constant_epsilon": near_constant_epsilon,
        "boundary_tolerance": boundary_tolerance,
        "max_boundary_fraction_for_promotion": max_boundary_fraction_for_promotion,
        "splits": split_reports,
    }


def finite_or_raise(
    arrays: dict[str, Any],
    *,
    chunk_rows: int = 2048,
) -> None:
    """Reject non-finite values without allocating a full bool mask.

    Large forecast tensors (for example Fidas-scale windows) can be multiple
    GiB. ``np.isfinite(array).all()`` materializes a same-shaped bool array and
    can OOM even when the float32 payload itself still fits.
    """

    import numpy as np

    if chunk_rows < 1:
        raise ValueError("chunk_rows must be >= 1")
    for name, array in arrays.items():
        if not isinstance(array, np.ndarray):
            array = np.asarray(array)
        if array.size == 0:
            raise ValueError(f"empty array found in {name}")
        if array.ndim == 0:
            if not math.isfinite(float(array)):
                raise ValueError(f"non-finite values found in {name}")
            continue
        # Scan along the leading axis in bounded chunks so peak RAM stays O(chunk).
        for start in range(0, int(array.shape[0]), chunk_rows):
            block = array[start : start + chunk_rows]
            if not np.isfinite(block).all():
                raise ValueError(f"non-finite values found in {name}")
        minimum = float(np.min(array))
        maximum = float(np.max(array))
        if not (math.isfinite(minimum) and math.isfinite(maximum)):
            raise ValueError(f"invalid numeric range in {name}")
