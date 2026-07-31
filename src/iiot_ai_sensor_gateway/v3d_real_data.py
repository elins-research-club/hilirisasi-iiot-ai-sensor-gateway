from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import random
import resource
import statistics
import threading
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PREPARED_SCHEMA = "iiot.v3d.prepared_dataset.v1"
RUN_SCHEMA = "iiot.v3d.forecast_run.v1"
SUMMARY_SCHEMA = "iiot.v3d.forecast_summary.v1"


@dataclass(frozen=True)
class DatasetConfig:
    dataset_id: str
    filename: str
    target_names: tuple[str, ...]
    cadence_seconds: int
    lookback: int
    horizon: int
    seasonal_period: int
    train_fraction: float = 0.65
    validation_fraction: float = 0.15
    coverage_threshold: float = 0.70
    minimum_valid_points: int = 1000
    maximum_train_windows: int = 8000
    maximum_validation_windows: int = 2500
    maximum_test_windows: int = 3500


DATASET_CONFIGS: dict[str, DatasetConfig] = {
    "uci": DatasetConfig(
        dataset_id="uci_air_quality_360",
        filename="uci_air_quality_360.zip",
        target_names=("temperature_c", "humidity_pct"),
        cadence_seconds=3600,
        lookback=168,
        horizon=24,
        seasonal_period=168,
        minimum_valid_points=4000,
        maximum_train_windows=5000,
        maximum_validation_windows=1500,
        maximum_test_windows=2000,
    ),
    "beijing": DatasetConfig(
        dataset_id="beijing_multi_site_air_quality_501",
        filename="beijing_multi_site_air_quality_501.zip",
        target_names=("pm25_ug_m3", "temperature_c"),
        cadence_seconds=3600,
        lookback=168,
        horizon=24,
        seasonal_period=168,
        minimum_valid_points=20000,
        maximum_train_windows=12000,
        maximum_validation_windows=3600,
        maximum_test_windows=4800,
    ),
    "intel": DatasetConfig(
        dataset_id="intel_lab_sensor_data",
        filename="intel_lab_sensor_data.txt.gz",
        target_names=("temperature_c", "humidity_pct"),
        cadence_seconds=300,
        lookback=288,
        horizon=12,
        seasonal_period=288,
        minimum_valid_points=3500,
        maximum_train_windows=10000,
        maximum_validation_windows=3000,
        maximum_test_windows=4000,
    ),
}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)


def _safe_float_frame(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    output = frame.copy()
    for column in columns:
        output[column] = pd.to_numeric(output[column], errors="coerce")
        output.loc[~np.isfinite(output[column]), column] = np.nan
    return output


def load_uci_archive(path: str | Path) -> dict[str, pd.DataFrame]:
    archive_path = Path(path)
    with zipfile.ZipFile(archive_path) as archive:
        members = [name for name in archive.namelist() if Path(name).name == "AirQualityUCI.csv"]
        if len(members) != 1:
            raise ValueError(f"expected one AirQualityUCI.csv, found {members}")
        with archive.open(members[0]) as handle:
            frame = pd.read_csv(handle, sep=";", decimal=",", encoding="latin-1")
    frame = frame.dropna(axis=1, how="all")
    required = {"Date", "Time", "T", "RH"}
    if not required.issubset(frame.columns):
        raise ValueError(f"UCI archive missing columns: {sorted(required.difference(frame.columns))}")
    timestamp = pd.to_datetime(
        frame["Date"].astype(str).str.strip() + " " + frame["Time"].astype(str).str.strip(),
        format="%d/%m/%Y %H.%M.%S",
        errors="coerce",
    )
    selected = pd.DataFrame(
        {
            "timestamp": timestamp,
            "temperature_c": pd.to_numeric(frame["T"], errors="coerce"),
            "humidity_pct": pd.to_numeric(frame["RH"], errors="coerce"),
        }
    )
    selected = selected.replace(-200, np.nan).dropna(subset=["timestamp"])
    selected = selected.sort_values("timestamp").drop_duplicates("timestamp", keep="first")
    selected = selected.set_index("timestamp").asfreq("1h")
    return {"italian_city_roadside_site": selected}


def load_beijing_archive(path: str | Path) -> dict[str, pd.DataFrame]:
    archive_path = Path(path)
    series: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(archive_path) as outer:
        direct_members = sorted(
            name
            for name in outer.namelist()
            if name.lower().endswith(".csv") and "prsa_data_" in Path(name).name.lower()
        )
        if direct_members:
            archive_context = zipfile.ZipFile(archive_path)
        else:
            nested = [
                name
                for name in outer.namelist()
                if Path(name).name == "PRSA2017_Data_20130301-20170228.zip"
            ]
            if len(nested) != 1:
                raise ValueError("Beijing wrapper archive does not contain the expected official data ZIP")
            archive_context = zipfile.ZipFile(io.BytesIO(outer.read(nested[0])))
        with archive_context as archive:
            members = sorted(
                name
                for name in archive.namelist()
                if name.lower().endswith(".csv") and "prsa_data_" in Path(name).name.lower()
            )
            if len(members) != 12:
                raise ValueError(f"expected 12 Beijing station CSV files, found {len(members)}")
            for member in members:
                with archive.open(member) as handle:
                    frame = pd.read_csv(handle)
                required = {"year", "month", "day", "hour", "PM2.5", "TEMP"}
                if not required.issubset(frame.columns):
                    raise ValueError(f"{member} missing columns: {sorted(required.difference(frame.columns))}")
                timestamp = pd.to_datetime(
                    frame[["year", "month", "day", "hour"]],
                    errors="coerce",
                )
                station_value = (
                    str(frame["station"].dropna().iloc[0])
                    if "station" in frame.columns and not frame["station"].dropna().empty
                    else Path(member).stem.replace("PRSA_Data_", "")
                )
                selected = pd.DataFrame(
                    {
                        "timestamp": timestamp,
                        "pm25_ug_m3": pd.to_numeric(frame["PM2.5"], errors="coerce"),
                        "temperature_c": pd.to_numeric(frame["TEMP"], errors="coerce"),
                    }
                )
                selected = selected.dropna(subset=["timestamp"]).sort_values("timestamp")
                selected = selected.drop_duplicates("timestamp", keep="first").set_index("timestamp").asfreq("1h")
                series[station_value] = selected
    return series


def load_intel_gzip(path: str | Path) -> dict[str, pd.DataFrame]:
    source = Path(path)
    names = ("date", "time", "epoch", "moteid", "temperature_c", "humidity_pct", "light_lux", "voltage_v")
    dtypes = {
        "epoch": "Int32",
        "moteid": "Int16",
        "temperature_c": "float32",
        "humidity_pct": "float32",
        "light_lux": "float32",
        "voltage_v": "float32",
    }
    with gzip.open(source, "rt", encoding="utf-8", errors="replace") as handle:
        frame = pd.read_csv(
            handle,
            sep=r"\s+",
            names=names,
            dtype=dtypes,
            on_bad_lines="skip",
            engine="c",
        )
    timestamp = pd.to_datetime(
        frame["date"].astype(str) + " " + frame["time"].astype(str),
        errors="coerce",
    )
    frame = frame.assign(timestamp=timestamp).dropna(subset=["timestamp", "moteid"])
    frame = _safe_float_frame(frame, ("temperature_c", "humidity_pct", "light_lux", "voltage_v"))
    frame = frame[(frame["humidity_pct"].isna()) | frame["humidity_pct"].between(0, 100)]
    frame = frame[(frame["temperature_c"].isna()) | frame["temperature_c"].between(-20, 80)]
    series: dict[str, pd.DataFrame] = {}
    for mote_id, mote in frame.groupby("moteid", sort=True):
        mote = mote.sort_values("timestamp").drop_duplicates("timestamp", keep="first")
        resampled = (
            mote.set_index("timestamp")[["temperature_c", "humidity_pct"]]
            .resample("5min")
            .median()
        )
        series[f"mote_{int(mote_id):02d}"] = resampled
    return series


def load_dataset_series(dataset_key: str, raw_path: str | Path) -> dict[str, pd.DataFrame]:
    if dataset_key == "uci":
        return load_uci_archive(raw_path)
    if dataset_key == "beijing":
        return load_beijing_archive(raw_path)
    if dataset_key == "intel":
        return load_intel_gzip(raw_path)
    raise ValueError(f"unknown V3D dataset key: {dataset_key}")


def _quality_report(
    series: dict[str, pd.DataFrame],
    config: DatasetConfig,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    accepted: dict[str, pd.DataFrame] = {}
    report: list[dict[str, Any]] = []
    for series_id, frame in sorted(series.items()):
        values = frame.loc[:, list(config.target_names)].to_numpy(dtype=np.float64)
        valid_rows = np.isfinite(values).all(axis=1)
        valid_count = int(valid_rows.sum())
        total_count = int(len(frame))
        coverage = valid_count / max(1, total_count)
        passed = bool(
            total_count >= config.minimum_valid_points
            and valid_count >= config.minimum_valid_points
            and coverage >= config.coverage_threshold
        )
        report.append(
            {
                "series_id": series_id,
                "rows": total_count,
                "valid_rows_all_targets": valid_count,
                "coverage_all_targets": coverage,
                "start": None if frame.empty else str(frame.index.min()),
                "end": None if frame.empty else str(frame.index.max()),
                "passed": passed,
                "reason": None
                if passed
                else "below minimum rows/valid rows/coverage gate",
            }
        )
        if passed:
            accepted[series_id] = frame.loc[:, list(config.target_names)].copy()
    if not accepted:
        raise ValueError(f"no {config.dataset_id} series passed the preregistered quality gate")
    return accepted, report


def _split_bounds(length: int, config: DatasetConfig) -> dict[str, tuple[int, int]]:
    first = int(length * config.train_fraction)
    second = int(length * (config.train_fraction + config.validation_fraction))
    if not (config.lookback + config.horizon < first < second < length):
        raise ValueError("series is too short for configured temporal splits")
    return {"train": (0, first), "val": (first, second), "test": (second, length)}


def _even_cap(indices: np.ndarray, limit: int) -> np.ndarray:
    if limit <= 0 or len(indices) <= limit:
        return indices
    positions = np.linspace(0, len(indices) - 1, num=limit, dtype=np.int64)
    return indices[positions]


def _series_windows(
    values: np.ndarray,
    start: int,
    end: int,
    lookback: int,
    horizon: int,
    limit: int,
) -> tuple[np.ndarray, np.ndarray]:
    latest_start = end - lookback - horizon
    if latest_start < start:
        return (
            np.empty((0, lookback, values.shape[1]), dtype=np.float32),
            np.empty((0, values.shape[1]), dtype=np.float32),
        )
    starts = np.arange(start, latest_start + 1, dtype=np.int64)
    valid: list[int] = []
    for window_start in starts:
        history = values[window_start : window_start + lookback]
        target = values[window_start + lookback + horizon - 1]
        if np.isfinite(history).all() and np.isfinite(target).all():
            valid.append(int(window_start))
    selected = _even_cap(np.asarray(valid, dtype=np.int64), limit)
    X = np.stack([values[index : index + lookback] for index in selected]).astype(np.float32) if len(selected) else np.empty((0, lookback, values.shape[1]), dtype=np.float32)
    y = np.stack([values[index + lookback + horizon - 1] for index in selected]).astype(np.float32) if len(selected) else np.empty((0, values.shape[1]), dtype=np.float32)
    return X, y


def prepare_dataset(
    dataset_key: str,
    raw_path: str | Path,
    output_npz: str | Path,
    output_metadata: str | Path,
) -> dict[str, Any]:
    if dataset_key not in DATASET_CONFIGS:
        raise ValueError(f"unsupported dataset key: {dataset_key}")
    config = DATASET_CONFIGS[dataset_key]
    source = Path(raw_path)
    series, source_sha = load_dataset_series(dataset_key, source), sha256_file(source)
    accepted, quality = _quality_report(series, config)

    train_values = []
    scale_differences = []
    for frame in accepted.values():
        values = frame.to_numpy(dtype=np.float64)
        bounds = _split_bounds(len(values), config)
        train = values[slice(*bounds["train"])]
        train_values.append(train)
        differences = np.abs(np.diff(train, axis=0))
        scale_differences.append(differences[np.isfinite(differences).all(axis=1)])
    all_train = np.concatenate(train_values, axis=0)
    means = np.nanmean(all_train, axis=0)
    stds = np.nanstd(all_train, axis=0)
    stds = np.where(stds < 1e-8, 1.0, stds)
    finite_diffs = np.concatenate(scale_differences, axis=0)
    mase_scale = np.nanmean(finite_diffs, axis=0)
    mase_scale = np.where(mase_scale < 1e-8, np.nan, mase_scale)

    split_X: dict[str, list[np.ndarray]] = {"train": [], "val": [], "test": []}
    split_y: dict[str, list[np.ndarray]] = {"train": [], "val": [], "test": []}
    split_series: dict[str, list[np.ndarray]] = {"train": [], "val": [], "test": []}
    series_ids = tuple(sorted(accepted))
    per_series_limits = {
        "train": max(1, config.maximum_train_windows // len(series_ids)),
        "val": max(1, config.maximum_validation_windows // len(series_ids)),
        "test": max(1, config.maximum_test_windows // len(series_ids)),
    }
    split_timestamps: dict[str, dict[str, list[str]]] = {}
    for numeric_id, series_id in enumerate(series_ids):
        frame = accepted[series_id]
        values = frame.to_numpy(dtype=np.float64)
        bounds = _split_bounds(len(values), config)
        split_timestamps[series_id] = {}
        for split, (start, end) in bounds.items():
            X_raw, y_raw = _series_windows(
                values,
                start,
                end,
                config.lookback,
                config.horizon,
                per_series_limits[split],
            )
            if len(X_raw):
                split_X[split].append(((X_raw - means) / stds).astype(np.float32))
                split_y[split].append(((y_raw - means) / stds).astype(np.float32))
                split_series[split].append(np.full(len(X_raw), numeric_id, dtype=np.int16))
            split_timestamps[series_id][split] = [
                str(frame.index[start]),
                str(frame.index[end - 1]),
            ]

    arrays: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for split in ("train", "val", "test"):
        if not split_X[split]:
            raise ValueError(f"{config.dataset_id} produced no {split} windows")
        arrays[f"X_{split}"] = np.concatenate(split_X[split], axis=0)
        arrays[f"y_{split}"] = np.concatenate(split_y[split], axis=0)
        arrays[f"series_{split}"] = np.concatenate(split_series[split], axis=0)
        counts[split] = int(len(arrays[f"X_{split}"]))
    arrays.update(
        {
            "target_names": np.asarray(config.target_names),
            "series_ids": np.asarray(series_ids),
            "mean": means.astype(np.float64),
            "std": stds.astype(np.float64),
            "mase_scale": mase_scale.astype(np.float64),
            "lookback": np.asarray([config.lookback], dtype=np.int32),
            "horizon": np.asarray([config.horizon], dtype=np.int32),
            "seasonal_period": np.asarray([config.seasonal_period], dtype=np.int32),
            "cadence_seconds": np.asarray([config.cadence_seconds], dtype=np.int32),
            "dataset_id": np.asarray([config.dataset_id]),
            "source_sha256": np.asarray([source_sha]),
        }
    )
    output = Path(output_npz)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, **arrays)
    metadata = {
        "schema": PREPARED_SCHEMA,
        "dataset_key": dataset_key,
        "dataset_id": config.dataset_id,
        "source_path": str(source),
        "source_sha256": source_sha,
        "source_bytes": source.stat().st_size,
        "prepared_path": str(output),
        "prepared_sha256": sha256_file(output),
        "configuration": asdict(config),
        "accepted_series": list(series_ids),
        "quality_report": quality,
        "split_timestamps": split_timestamps,
        "window_counts": counts,
        "normalization": {
            "method": "train-only z-score per target",
            "mean": dict(zip(config.target_names, means.tolist(), strict=True)),
            "std": dict(zip(config.target_names, stds.tolist(), strict=True)),
        },
        "mase_scale": dict(zip(config.target_names, mase_scale.tolist(), strict=True)),
        "missing_policy": "complete input and target windows only; no imputation in primary analysis",
        "resampling": "Intel only: deterministic five-minute median per mote; UCI and Beijing retain hourly cadence",
        "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    atomic_write_json(output_metadata, metadata)
    return metadata


def _torch_modules():
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    return torch, nn, DataLoader, TensorDataset


def build_model(model_name: str, sequence_length: int, target_count: int):
    torch, nn, _loader, _dataset = _torch_modules()
    if model_name in {"dlinear", "fits"}:
        from .edge_forecasting import build_edge_model

        model, config = build_edge_model(
            model_name,
            sequence_length=sequence_length,
            target_count=target_count,
            moving_average_kernel=25,
        )
        return model, {**config, "implementation": "project_existing"}

    if model_name == "lstm":
        class ResidualLSTM(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.lstm = nn.LSTM(target_count, 32, num_layers=1, batch_first=True)
                self.head = nn.Linear(32, target_count)
                nn.init.zeros_(self.head.weight)
                nn.init.zeros_(self.head.bias)

            def forward(self, history):
                encoded, _state = self.lstm(history)
                return history[:, -1, :] + self.head(encoded[:, -1, :])

        return ResidualLSTM(), {
            "architecture": "lstm_residual",
            "hidden_size": 32,
            "num_layers": 1,
            "implementation": "v3d_compact",
        }

    if model_name == "patchtst":
        patch_length = min(16, sequence_length)
        stride = max(4, patch_length // 2)
        patch_count = 1 + max(0, (sequence_length - patch_length) // stride)
        d_model = 32

        class CompactPatchTST(nn.Module):
            """Compact channel-independent PatchTST-inspired forecaster.

            This is a transparent project adaptation, not a bit-for-bit copy of
            the ICLR 2023 research code. It uses channel-independent patches,
            a shared Transformer encoder, and a residual last-value head.
            """

            def __init__(self) -> None:
                super().__init__()
                self.patch_embedding = nn.Linear(patch_length, d_model)
                self.position = nn.Parameter(torch.zeros(1, patch_count, d_model))
                encoder_layer = nn.TransformerEncoderLayer(
                    d_model=d_model,
                    nhead=4,
                    dim_feedforward=64,
                    dropout=0.1,
                    activation="gelu",
                    batch_first=True,
                    norm_first=True,
                )
                self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=1)
                self.head = nn.Linear(d_model, 1)
                nn.init.normal_(self.position, mean=0.0, std=0.02)
                nn.init.zeros_(self.head.weight)
                nn.init.zeros_(self.head.bias)

            def forward(self, history):
                batch, _length, channels = history.shape
                patches = history.transpose(1, 2).unfold(2, patch_length, stride)
                tokens = self.patch_embedding(patches.reshape(batch * channels, patch_count, patch_length))
                encoded = self.encoder(tokens + self.position)
                residual = self.head(encoded.mean(dim=1)).reshape(batch, channels)
                return history[:, -1, :] + residual

        return CompactPatchTST(), {
            "architecture": "patchtst_inspired_compact",
            "patch_length": patch_length,
            "stride": stride,
            "patch_count": patch_count,
            "d_model": d_model,
            "heads": 4,
            "layers": 1,
            "implementation": "project_adaptation_not_bit_for_bit_official",
        }
    raise ValueError(f"unsupported model: {model_name}")


def _baseline_candidates(X_raw: np.ndarray, seasonal_period: int, horizon: int) -> dict[str, np.ndarray]:
    candidates = {
        "last_value": X_raw[:, -1, :],
        "window_mean": X_raw.mean(axis=1),
        "drift": X_raw[:, -1, :] + horizon * (X_raw[:, -1, :] - X_raw[:, 0, :]) / max(1, X_raw.shape[1] - 1),
    }
    seasonal_index = X_raw.shape[1] - seasonal_period + horizon - 1
    if 0 <= seasonal_index < X_raw.shape[1]:
        candidates["seasonal_naive"] = X_raw[:, seasonal_index, :]
    return candidates


def select_baselines(
    X_val_raw: np.ndarray,
    y_val_raw: np.ndarray,
    seasonal_period: int,
    horizon: int,
    target_names: tuple[str, ...],
) -> dict[str, str]:
    candidates = _baseline_candidates(X_val_raw, seasonal_period, horizon)
    selected: dict[str, str] = {}
    for target_index, target in enumerate(target_names):
        scored = []
        for name, prediction in candidates.items():
            rmse = float(np.sqrt(np.mean((prediction[:, target_index] - y_val_raw[:, target_index]) ** 2)))
            scored.append((rmse, name))
        selected[target] = min(scored)[1]
    return selected


def compose_baseline(
    X_raw: np.ndarray,
    selection: dict[str, str],
    seasonal_period: int,
    horizon: int,
    target_names: tuple[str, ...],
) -> np.ndarray:
    candidates = _baseline_candidates(X_raw, seasonal_period, horizon)
    output = np.empty((len(X_raw), len(target_names)), dtype=np.float64)
    for index, target in enumerate(target_names):
        output[:, index] = candidates[selection[target]][:, index]
    return output


def regression_metrics(
    actual: np.ndarray,
    prediction: np.ndarray,
    baseline: np.ndarray,
    mase_scale: np.ndarray,
    target_names: tuple[str, ...],
) -> dict[str, Any]:
    error = prediction - actual
    baseline_error = baseline - actual
    mae = np.mean(np.abs(error), axis=0)
    rmse = np.sqrt(np.mean(error * error, axis=0))
    baseline_rmse = np.sqrt(np.mean(baseline_error * baseline_error, axis=0))
    mase = np.divide(mae, mase_scale, out=np.full_like(mae, np.nan), where=np.isfinite(mase_scale) & (mase_scale > 0))
    skill = np.divide(
        baseline_rmse - rmse,
        baseline_rmse,
        out=np.full_like(rmse, np.nan),
        where=baseline_rmse > 0,
    )
    per_target = {}
    for index, target in enumerate(target_names):
        per_target[target] = {
            "mae": float(mae[index]),
            "rmse": float(rmse[index]),
            "mase": None if not np.isfinite(mase[index]) else float(mase[index]),
            "baseline_rmse": float(baseline_rmse[index]),
            "rmse_skill": None if not np.isfinite(skill[index]) else float(skill[index]),
            "beats_baseline": bool(rmse[index] < baseline_rmse[index]),
        }
    return {
        "mean_mase": None if not np.isfinite(mase).any() else float(np.nanmean(mase)),
        "mean_rmse_skill": None if not np.isfinite(skill).any() else float(np.nanmean(skill)),
        "target_wins": int(np.sum(rmse < baseline_rmse)),
        "target_count": len(target_names),
        "per_target": per_target,
    }


class _MemorySampler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self.peak_rss = 0
        self._thread: threading.Thread | None = None

    def __enter__(self):
        import psutil

        process = psutil.Process()

        def sample() -> None:
            while not self._stop.is_set():
                try:
                    self.peak_rss = max(self.peak_rss, int(process.memory_info().rss))
                except psutil.Error:
                    pass
                self._stop.wait(0.02)

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def _predict(model: Any, X: np.ndarray, batch_size: int, device: str) -> np.ndarray:
    torch, _nn, _loader, _dataset = _torch_modules()
    values: list[np.ndarray] = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(X), batch_size):
            batch = torch.from_numpy(X[start : start + batch_size]).to(device)
            values.append(model(batch).detach().cpu().numpy())
    return np.concatenate(values, axis=0) if values else np.empty((0, X.shape[-1]), dtype=np.float32)


def _latency(model: Any, X: np.ndarray, batch_size: int, device: str) -> dict[str, float | int]:
    torch, _nn, _loader, _dataset = _torch_modules()
    batch = torch.from_numpy(X[: min(batch_size, len(X))]).to(device)
    model.eval()
    with torch.no_grad():
        for _ in range(3):
            model(batch)
        timings = []
        for _ in range(20):
            started = time.perf_counter()
            model(batch)
            if device.startswith("cuda"):
                torch.cuda.synchronize()
            timings.append((time.perf_counter() - started) * 1000.0)
    per_sample = [value / max(1, len(batch)) for value in timings]
    return {
        "batch_size": int(len(batch)),
        "median_ms_per_batch": float(statistics.median(timings)),
        "p95_ms_per_batch": float(np.percentile(timings, 95)),
        "median_ms_per_sample": float(statistics.median(per_sample)),
        "p95_ms_per_sample": float(np.percentile(per_sample, 95)),
    }


def train_and_evaluate(
    prepared_npz: str | Path,
    output_dir: str | Path,
    model_name: str,
    seed: int,
    *,
    epochs: int = 12,
    patience: int = 3,
    batch_size: int = 256,
    device: str = "auto",
) -> dict[str, Any]:
    torch, nn, DataLoader, TensorDataset = _torch_modules()
    if epochs < 1 or patience < 1 or batch_size < 1:
        raise ValueError("epochs, patience, and batch size must be positive")
    data = np.load(prepared_npz, allow_pickle=False)
    X_train = data["X_train"].astype(np.float32)
    y_train = data["y_train"].astype(np.float32)
    X_val = data["X_val"].astype(np.float32)
    y_val = data["y_val"].astype(np.float32)
    X_test = data["X_test"].astype(np.float32)
    y_test = data["y_test"].astype(np.float32)
    target_names = tuple(str(item) for item in data["target_names"])
    means = data["mean"].astype(np.float64)
    stds = data["std"].astype(np.float64)
    mase_scale = data["mase_scale"].astype(np.float64)
    seasonal_period = int(data["seasonal_period"][0])
    horizon = int(data["horizon"][0])
    dataset_id = str(data["dataset_id"][0])
    source_sha = str(data["source_sha256"][0])
    series_ids = tuple(str(item) for item in data["series_ids"])

    resolved_device = "cuda" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(min(4, max(1, torch.get_num_threads())))

    model, model_config = build_model(model_name, X_train.shape[1], y_train.shape[1])
    model = model.to(resolved_device)
    parameter_count = int(sum(parameter.numel() for parameter in model.parameters()))
    learning_rate = 0.01 if model_name == "dlinear" else 0.001
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=1)
    criterion = nn.SmoothL1Loss(beta=0.02)
    generator = torch.Generator().manual_seed(seed)
    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train)),
        batch_size=min(batch_size, len(X_train)),
        shuffle=True,
        generator=generator,
    )
    val_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val)),
        batch_size=min(batch_size, len(X_val)),
        shuffle=False,
    )

    best_loss = math.inf
    best_state = None
    best_epoch = 0
    wait = 0
    history = []
    started = time.perf_counter()
    with _MemorySampler() as memory:
        for epoch in range(1, epochs + 1):
            model.train()
            train_losses = []
            for batch_x, batch_y in train_loader:
                batch_x = batch_x.to(resolved_device)
                batch_y = batch_y.to(resolved_device)
                optimizer.zero_grad(set_to_none=True)
                loss = criterion(model(batch_x), batch_y)
                if not torch.isfinite(loss):
                    raise RuntimeError("non-finite training loss")
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                train_losses.append(float(loss.detach().cpu()))
            model.eval()
            validation_losses = []
            with torch.no_grad():
                for batch_x, batch_y in val_loader:
                    validation_losses.append(
                        float(criterion(model(batch_x.to(resolved_device)), batch_y.to(resolved_device)).detach().cpu())
                    )
            train_loss = float(np.mean(train_losses))
            validation_loss = float(np.mean(validation_losses))
            scheduler.step(validation_loss)
            history.append(
                {
                    "epoch": epoch,
                    "train_loss": train_loss,
                    "validation_loss": validation_loss,
                    "learning_rate": float(optimizer.param_groups[0]["lr"]),
                }
            )
            if validation_loss < best_loss - 1e-8:
                best_loss = validation_loss
                best_epoch = epoch
                best_state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
                wait = 0
            else:
                wait += 1
                if wait >= patience:
                    break
    training_seconds = time.perf_counter() - started
    if best_state is None:
        raise RuntimeError("training did not produce a best checkpoint")
    model.load_state_dict(best_state, strict=True)

    X_val_raw = X_val.astype(np.float64) * stds + means
    y_val_raw = y_val.astype(np.float64) * stds + means
    X_test_raw = X_test.astype(np.float64) * stds + means
    y_test_raw = y_test.astype(np.float64) * stds + means
    baseline_selection = select_baselines(
        X_val_raw,
        y_val_raw,
        seasonal_period,
        horizon,
        target_names,
    )
    baseline_test = compose_baseline(
        X_test_raw,
        baseline_selection,
        seasonal_period,
        horizon,
        target_names,
    )
    prediction_normalized = _predict(model, X_test, batch_size, resolved_device)
    prediction_raw = prediction_normalized.astype(np.float64) * stds + means
    metrics = regression_metrics(y_test_raw, prediction_raw, baseline_test, mase_scale, target_names)
    latency = _latency(model, X_test, min(512, batch_size), resolved_device)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output / "model.pt"
    torch.save(
        {
            "state_dict": best_state,
            "model": model_name,
            "model_config": model_config,
            "dataset_id": dataset_id,
            "seed": seed,
            "target_names": target_names,
            "source_sha256": source_sha,
        },
        checkpoint_path,
    )

    series_numeric = data["series_test"].astype(np.int64)
    series_rows: list[dict[str, Any]] = []
    for numeric_id, series_id in enumerate(series_ids):
        mask = series_numeric == numeric_id
        if not np.any(mask):
            continue
        per_series = regression_metrics(
            y_test_raw[mask],
            prediction_raw[mask],
            baseline_test[mask],
            mase_scale,
            target_names,
        )
        series_rows.append(
            {
                "dataset_id": dataset_id,
                "model": model_name,
                "seed": seed,
                "series_id": series_id,
                "samples": int(mask.sum()),
                "mean_mase": per_series["mean_mase"],
                "mean_rmse_skill": per_series["mean_rmse_skill"],
                "target_wins": per_series["target_wins"],
                "target_count": per_series["target_count"],
            }
        )
    series_path = output / "series_metrics.csv"
    with series_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(series_rows[0]) if series_rows else ["dataset_id"])
        writer.writeheader()
        writer.writerows(series_rows)

    result = {
        "schema": RUN_SCHEMA,
        "dataset_id": dataset_id,
        "prepared_npz": str(prepared_npz),
        "prepared_sha256": sha256_file(prepared_npz),
        "source_sha256": source_sha,
        "model": model_name,
        "model_config": model_config,
        "seed": seed,
        "device": resolved_device,
        "epochs_requested": epochs,
        "epochs_ran": len(history),
        "best_epoch": best_epoch,
        "best_validation_loss": best_loss,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "history": history,
        "baseline_selection": baseline_selection,
        "metrics": metrics,
        "parameter_count": parameter_count,
        "artifact_bytes": checkpoint_path.stat().st_size,
        "training_seconds": training_seconds,
        "peak_process_rss_mb": memory.peak_rss / (1024 * 1024),
        "process_max_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "latency": latency,
        "series_metrics_path": str(series_path),
        "checkpoint_path": str(checkpoint_path),
        "baseline_gate_passed": bool(
            metrics["mean_rmse_skill"] is not None
            and metrics["mean_rmse_skill"] > 0
            and metrics["target_wins"] >= math.ceil(metrics["target_count"] / 2)
        ),
        "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    atomic_write_json(output / "run.json", result)
    return result


def summarize_runs(root: str | Path, output_dir: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    runs = []
    for path in sorted(root_path.rglob("run.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") == RUN_SCHEMA:
            value["run_path"] = str(path)
            runs.append(value)
    if not runs:
        raise ValueError(f"no V3D runs found under {root}")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        groups.setdefault((run["dataset_id"], run["model"]), []).append(run)
    summary_rows = []
    for (dataset_id, model), group in sorted(groups.items()):
        mase_values = [run["metrics"]["mean_mase"] for run in group if run["metrics"]["mean_mase"] is not None]
        skill_values = [run["metrics"]["mean_rmse_skill"] for run in group if run["metrics"]["mean_rmse_skill"] is not None]
        latency_values = [run["latency"]["median_ms_per_sample"] for run in group]
        row = {
            "dataset_id": dataset_id,
            "model": model,
            "seed_count": len(group),
            "mean_mase": float(np.mean(mase_values)),
            "std_mase": float(np.std(mase_values, ddof=0)),
            "worst_seed_mase": float(np.max(mase_values)),
            "mean_rmse_skill": float(np.mean(skill_values)),
            "std_rmse_skill": float(np.std(skill_values, ddof=0)),
            "baseline_gate_pass_count": int(sum(bool(run["baseline_gate_passed"]) for run in group)),
            "parameter_count": int(statistics.median(run["parameter_count"] for run in group)),
            "artifact_bytes": int(statistics.median(run["artifact_bytes"] for run in group)),
            "median_training_seconds": float(statistics.median(run["training_seconds"] for run in group)),
            "median_latency_ms_per_sample": float(statistics.median(latency_values)),
            "median_peak_rss_mb": float(statistics.median(run["peak_process_rss_mb"] for run in group)),
        }
        summary_rows.append(row)

    for dataset_id in sorted({row["dataset_id"] for row in summary_rows}):
        candidates = [row for row in summary_rows if row["dataset_id"] == dataset_id]
        for row in candidates:
            dominated = False
            for other in candidates:
                if other is row:
                    continue
                no_worse = (
                    other["mean_mase"] <= row["mean_mase"]
                    and other["std_mase"] <= row["std_mase"]
                    and other["median_latency_ms_per_sample"] <= row["median_latency_ms_per_sample"]
                )
                strictly_better = (
                    other["mean_mase"] < row["mean_mase"]
                    or other["std_mase"] < row["std_mase"]
                    or other["median_latency_ms_per_sample"] < row["median_latency_ms_per_sample"]
                )
                if no_worse and strictly_better:
                    dominated = True
                    break
            row["pareto"] = not dominated

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "RESULT_SUMMARY_V3D.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)
    combined_series = []
    for run in runs:
        path = Path(run["series_metrics_path"])
        if path.exists():
            with path.open(encoding="utf-8", newline="") as handle:
                combined_series.extend(csv.DictReader(handle))
    series_path = output / "SERIES_LEVEL_RESULTS_V3D.csv"
    if combined_series:
        with series_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(combined_series[0]))
            writer.writeheader()
            writer.writerows(combined_series)
    result = {
        "schema": SUMMARY_SCHEMA,
        "run_count": len(runs),
        "groups": len(summary_rows),
        "summary_csv": str(csv_path),
        "series_csv": str(series_path),
        "rows": summary_rows,
        "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    atomic_write_json(output / "RESULT_SUMMARY_V3D.json", result)
    return result
