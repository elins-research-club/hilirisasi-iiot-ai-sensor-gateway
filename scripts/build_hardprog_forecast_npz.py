#!/usr/bin/env python3
"""Build forecast NPZ from real per-sensor CSV captures (hardprog 2026-08-28).

Why not prepare-forecast-dataset: that CLI requires long canonical windows and
rejects short per-sensor real captures ("not enough forecast samples"). These
captures are ~1000 points at 0.5-2 s cadence per sensor — a legitimate but
short real lane. We build sliding-window temporal splits ourselves, following
project rules:
- train/val/test split is temporal and non-overlapping (purge gap between splits)
- normalization fitted on train only, ranges stored for denormalization
- targets are the measured fields of that sensor lane (constant/meaningless
  targets like CO=0 or power_mW=6.25 are excluded)
- NPZ keys match edge_forecasting._load_dataset() exactly
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

# Keep the documented direct invocation working without requiring callers to
# remember PYTHONPATH=src.  The project package is still the source of truth
# for the canonical data-quality report.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

LANES = {
    "data_bme": {
        "file": "data_bme.csv",
        "features": ["temperature_c", "humidity_pct", "pressure_hpa", "bme_gas_ohm"],
        "delim": ";",
        "colmap": {
            "temperature_c": "temperature_c", "humidity_pct": "humidity_rh",
            "pressure_hpa": "pressure_hpa", "bme_gas_ohm": "gas_ohm",
        },
    },
    "data_mentah_bme688": {
        "file": "data_mentah_bme688.csv",
        "features": ["temperature_c", "humidity_pct", "pressure_hpa", "bme_gas_ohm"],
        "delim": ";",
        "colmap": {
            "temperature_c": "temperature_c", "humidity_pct": "humidity_%",
            "pressure_hpa": "pressure_hPa", "bme_gas_ohm": "gas_ohm",
        },
    },
    "data_co2": {
        "file": "data_co2.csv",
        "features": ["co2_ppm"],
        "delim": ";",
        "colmap": {"co2_ppm": "co2_ppm"},
        "invalid_values": {"co2_ppm": [-1.0]},
    },
    "data_no2": {
        "file": "data_no2.csv",
        "features": ["raw_adc", "voltage_V"],
        "delim": ";",
        "colmap": {"raw_adc": "raw_adc", "voltage_V": "voltage_V"},
    },
    "data_pms_1": {
        "file": "data_pms_1.csv",
        "features": ["pm1_0", "pm2_5", "pm10"],
        "delim": ",",
        "colmap": {"pm1_0": "pm1_0", "pm2_5": "pm2_5", "pm10": "pm10"},
    },
    "data_INA226": {
        "file": "data_INA226.csv",
        "features": ["bus_voltage_v", "current_mA"],  # power_mW constant -> excluded
        "delim": ";",
        "colmap": {"bus_voltage_v": "bus_voltage_v", "current_mA": "current_mA"},
    },
    # data_tof_1.csv is intentionally excluded: ToF belongs to the
    # hybrid-camera reference lane, never to the environmental sensor model.
    # data_co.csv: co_ppm=0.00 and board_temp_c=26.45 are constant across the
    # whole capture -> no usable feature; intentionally NOT modeled (honest skip).
}


def read_csv(path: Path, delim: str) -> list[dict]:
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f, delimiter=delim) if r]


def to_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "null", "nan", "NaN"):
        return None
    try:
        value = float(s.replace(",", "."))
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def is_invalid(v, invalid_list):
    if invalid_list is None:
        return False
    return v is not None and any(abs(v - iv) < 1e-9 for iv in invalid_list)


def infer_cadence_seconds(timestamps_ms: list[float]) -> float:
    deltas = [
        later - earlier
        for earlier, later in zip(timestamps_ms, timestamps_ms[1:])
        if later > earlier
    ]
    if not deltas:
        raise ValueError("timestamp column has no positive intervals")
    return float(np.median(np.asarray(deltas, dtype=np.float64)) / 1000.0)


def _make_windows(values: np.ndarray, window_size: int, horizon: int) -> tuple[np.ndarray, np.ndarray]:
    total_windows = len(values) - window_size - horizon + 1
    if total_windows < 1:
        return np.empty((0, window_size, values.shape[1])), np.empty((0, values.shape[1]))
    x = np.stack([values[i:i + window_size] for i in range(total_windows)])
    y = np.stack([values[i + window_size + horizon - 1] for i in range(total_windows)])
    return x, y


def build_npz(lane: str, csv_dir: Path, out_npz: Path, out_meta: Path,
              window_size: int = 16, horizon: int = 5,
              val_frac: float = 0.15, test_frac: float = 0.15,
              expected_runtime_cadence_sec: float | None = None,
              cadence_tolerance_fraction: float = 0.20) -> dict:
    if expected_runtime_cadence_sec is not None and expected_runtime_cadence_sec <= 0:
        raise ValueError("expected_runtime_cadence_sec must be positive")
    if not 0.0 <= cadence_tolerance_fraction < 1.0:
        raise ValueError("cadence_tolerance_fraction must be in [0, 1)")
    cfg = LANES[lane]
    rows = read_csv(csv_dir / cfg["file"], cfg["delim"])
    features = cfg["features"]
    meta_cadence_note = "per-sensor real capture; source timestamps are uptime milliseconds"
    # gather per-feature series, dropping rows where all features are None
    series = {f: [] for f in features}
    ts = []
    invalid_sentinel_counts = {f: 0 for f in features}
    dropped_all_missing_or_invalid_rows = 0
    for r in rows:
        raw_vals = [to_float(r.get(cfg["colmap"][f], "")) for f in features]
        # Mark lane-specific sensor error sentinels as invalid before
        # train-only imputation/normalization.
        vals = []
        for f, value in zip(features, raw_vals):
            if is_invalid(value, cfg.get("invalid_values", {}).get(f)):
                invalid_sentinel_counts[f] += 1
                vals.append(None)
            else:
                vals.append(value)
        if all(v is None for v in vals):
            dropped_all_missing_or_invalid_rows += 1
            continue
        for f, v in zip(features, vals):
            series[f].append(v if v is not None else np.nan)
        t = to_float(r.get(list(r.keys())[0], ""))
        ts.append(t if t is not None else np.nan)
    n = len(ts)
    # Feature/target availability decisions are train-only.  Looking at
    # validation/test here would leak future distribution information into the
    # schema even before model fitting.
    train_feature_end = int(n * (1 - val_frac - test_frac))
    if train_feature_end < 1:
        raise ValueError(f"lane {lane}: empty training segment")
    keep = []
    dropped_features: dict[str, str] = {}
    for f in features:
        arr = np.array(series[f], dtype=float)
        train_values = arr[:train_feature_end]
        finite = train_values[~np.isnan(train_values)]
        if len(finite) < 30:
            reason = f"train_finite_count_{len(finite)}_below_30"
            dropped_features[f] = reason
            print(f"  [skip] {f}: only {len(finite)} finite train values")
            continue
        if np.nanmax(finite) - np.nanmin(finite) < 1e-9:
            dropped_features[f] = "train_constant"
            print(f"  [skip] {f}: constant in train")
            continue
        keep.append(f)
    if not keep:
        raise ValueError(f"lane {lane}: no usable features")
    feature_manifest_payload = {
        "ordered_features": keep,
        "dropped_features": dropped_features,
        "selection_split": "train",
    }
    feature_schema_sha256 = hashlib.sha256(
        json.dumps(
            feature_manifest_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    feature_manifest = {
        **feature_manifest_payload,
        "schema_sha256": feature_schema_sha256,
    }
    # build matrix
    X = np.column_stack([np.array(series[f], dtype=float) for f in keep])
    if len(ts) != n or any(not np.isfinite(value) for value in ts):
        raise ValueError(f"lane {lane}: timestamp column contains missing/non-finite values")
    cadence_seconds = infer_cadence_seconds(ts)
    capture_duration_seconds = (max(ts) - min(ts)) / 1000.0 if len(ts) > 1 else 0.0
    runtime_cadence_gate = {
        "status": "NOT_REQUESTED",
        "observed_cadence_seconds": cadence_seconds,
        "expected_runtime_cadence_seconds": expected_runtime_cadence_sec,
        "tolerance_fraction": cadence_tolerance_fraction,
    }
    if expected_runtime_cadence_sec is not None:
        tolerance_seconds = expected_runtime_cadence_sec * cadence_tolerance_fraction
        delta_seconds = abs(cadence_seconds - expected_runtime_cadence_sec)
        runtime_cadence_gate.update(
            {
                "status": "PASS" if delta_seconds <= tolerance_seconds else "FAIL",
                "tolerance_seconds": tolerance_seconds,
                "absolute_delta_seconds": delta_seconds,
            }
        )
        if delta_seconds > tolerance_seconds:
            raise ValueError(
                f"lane {lane}: observed cadence {cadence_seconds:g}s is incompatible with "
                f"expected runtime cadence {expected_runtime_cadence_sec:g}s "
                f"(tolerance {cadence_tolerance_fraction:.0%}); resample the source to the "
                "runtime cadence before training instead of relabeling the horizon"
            )
    purge_gap_steps = horizon
    n_train_points = int(n * (1 - val_frac - test_frac))
    n_val_points = int(n * val_frac)
    segments = {
        "train": (0, n_train_points),
        "val": (n_train_points + purge_gap_steps,
                n_train_points + purge_gap_steps + n_val_points),
        "test": (n_train_points + purge_gap_steps + n_val_points + purge_gap_steps, n),
    }
    if any(end - start < window_size + horizon for start, end in segments.values()):
        raise ValueError(f"lane {lane}: split too small after purge gap={purge_gap_steps}")

    # Fit both imputation and normalization on train only.  If a feature is
    # entirely missing in train, fail closed instead of borrowing test values.
    train_end = segments["train"][1]
    train_medians = np.nanmedian(X[:train_end], axis=0)
    if not np.all(np.isfinite(train_medians)):
        raise ValueError(f"lane {lane}: a retained feature is entirely missing in train")
    for j in range(X.shape[1]):
        X[np.isnan(X[:, j]), j] = train_medians[j]
    lo = np.nanmin(X[:train_end], axis=0)
    hi = np.nanmax(X[:train_end], axis=0)
    span = hi - lo
    span[span < 1e-9] = 1.0
    Xn = (X - lo) / span

    split_arrays = {
        split: _make_windows(Xn[start:end], window_size, horizon)
        for split, (start, end) in segments.items()
    }
    X_train, y_train = split_arrays["train"]
    X_val, y_val = split_arrays["val"]
    X_test, y_test = split_arrays["test"]
    if min(len(X_train), len(X_val), len(X_test)) < 5:
        raise ValueError(f"lane {lane}: split too small train={len(X_train)} val={len(X_val)} test={len(X_test)}")
    target_indices = np.arange(len(keep), dtype=np.int64)
    norm_ranges = {f: [float(lo[i]), float(hi[i])] for i, f in enumerate(keep)}
    # Canonical data-quality report (same as prepare-forecast-dataset) so the
    # evaluator's quality gate can actually pass when targets are healthy.
    from iiot_ai_sensor_gateway.dataset_quality import array_quality_report

    data_quality = array_quality_report(
        {"y_train": y_train, "y_val": y_val, "y_test": y_test}, tuple(keep)
    )
    cadence = {
        "note": meta_cadence_note,
        "source": "per-sensor real capture; uptime milliseconds",
        "cadence_seconds": cadence_seconds,
        "capture_duration_seconds": capture_duration_seconds,
        "positive_delta_count": sum(
            later > earlier for earlier, later in zip(ts, ts[1:])
        ),
    }
    source_quality = {
        "source_row_count": len(rows),
        "usable_point_count": int(n),
        "dropped_all_missing_or_invalid_rows": dropped_all_missing_or_invalid_rows,
        "invalid_sentinel_counts": invalid_sentinel_counts,
    }
    meta = {
        "lane": lane, "source": str(csv_dir / cfg["file"]),
        "n_points": int(n), "window_size": window_size, "horizon_steps": horizon,
        "purge_gap_steps": purge_gap_steps,
        "cadence_seconds": cadence_seconds,
        "horizon_duration_seconds": cadence_seconds * horizon,
        "features": keep, "normalization_ranges": norm_ranges,
        "split": {"train": len(X_train), "val": len(X_val), "test": len(X_test)},
        "cadence_note": meta_cadence_note,
        "calibration_note": "uncalibrated raw CSV (hardprog team)",
        "source_quality": source_quality,
        "runtime_cadence_gate": runtime_cadence_gate,
        "feature_manifest": feature_manifest,
        "data_quality": data_quality,
    }
    np.savez(
        out_npz,
        X_train=X_train.astype("float32"), y_train=y_train.astype("float32"),
        X_val=X_val.astype("float32"), y_val=y_val.astype("float32"),
        X_test=X_test.astype("float32"), y_test=y_test.astype("float32"),
        feature_names=np.array(keep, dtype="U64"),
        target_names=np.array(keep, dtype="U64"),
        target_indices=target_indices,
        horizon_steps=np.array([horizon], dtype=np.int64),
        resample_interval_sec=np.array([max(1, int(round(cadence_seconds)))], dtype=np.int64),
        cadence_seconds=np.array([cadence_seconds], dtype=np.float64),
        horizon_duration_seconds=np.array([cadence_seconds * horizon], dtype=np.float64),
        window_size=np.array([window_size], dtype=np.int64),
        purge_gap_steps=np.array([purge_gap_steps], dtype=np.int64),
        feature_schema_sha256=np.array([feature_schema_sha256], dtype="U64"),
        normalization_ranges_json=np.array([json.dumps(norm_ranges)], dtype="U1024"),
        dataset_meta_json=np.array([json.dumps(meta)], dtype="U8192"),
        feature_manifest_json=np.array([json.dumps(feature_manifest, sort_keys=True)], dtype="U2048"),
        data_quality_json=np.array([json.dumps(data_quality, sort_keys=True)], dtype="U8192"),
        cadence_diagnostics_json=np.array([json.dumps(cadence, sort_keys=True)], dtype="U2048"),
    )
    out_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  -> {out_npz.name}: features={keep} windows train={len(X_train)} val={len(X_val)} test={len(X_test)} dq={data_quality['status']}")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--window-size", type=int, default=16)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument(
        "--expected-runtime-cadence-sec",
        type=float,
        default=None,
        help=(
            "fail closed unless the source cadence already matches the intended runtime cadence; "
            "resample first when this gate fails"
        ),
    )
    ap.add_argument("--cadence-tolerance-fraction", type=float, default=0.20)
    args = ap.parse_args()
    csv_dir = Path(args.csv_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    failures = []
    for lane in LANES:
        print(f"=== {lane} ===")
        source = csv_dir / LANES[lane]["file"]
        if not source.exists():
            print(f"  SKIP {lane}: source file not found ({source.name})")
            continue
        try:
            meta = build_npz(
                lane, csv_dir,
                out_dir / f"{lane}_dataset.npz",
                out_dir / f"{lane}_meta.json",
                window_size=args.window_size, horizon=args.horizon,
                expected_runtime_cadence_sec=args.expected_runtime_cadence_sec,
                cadence_tolerance_fraction=args.cadence_tolerance_fraction,
            )
            results[lane] = meta
        except ValueError as e:
            print(f"  !! {e}")
            failures.append({"lane": lane, "error": str(e)})
    (out_dir / "build_summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\nDONE")
    if args.expected_runtime_cadence_sec is not None and failures:
        print(
            "runtime cadence gate failed for: "
            + ", ".join(item["lane"] for item in failures),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
