from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IdentityConfig:
    gateway_id: str = "raspi_gateway_01"
    default_room_id: str = "room_A"


@dataclass(frozen=True)
class PipelineConfig:
    buffer_max_size: int = 512
    validation_state_max_entries: int = 100_000
    resample_interval_sec: int = 60
    window_size: int = 12
    window_step: int = 1
    min_valid_ratio: float = 0.75
    node_silent_after_sec: int = 180
    sequence_gap_warn: bool = True


@dataclass(frozen=True)
class ContractConfig:
    allow_legacy_v1: bool = True
    data_topic: str = "iot/{gateway_id}/data"
    status_topic: str = "iot/{gateway_id}/status/sensor"


@dataclass(frozen=True)
class PreprocessingConfig:
    version: str = "gateway_preprocess.v1"
    apply_to_v2: bool = False
    state_max_entries: int = 100_000
    filters: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ReceiverConfig:
    rotate_max_bytes: int = 10 * 1024 * 1024
    serial_idle_sleep_sec: float = 0.05
    reconnect_initial_sec: float = 0.5
    reconnect_max_sec: float = 30.0


@dataclass(frozen=True)
class AppConfig:
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    contract: ContractConfig = field(default_factory=ContractConfig)
    preprocessing: PreprocessingConfig = field(default_factory=PreprocessingConfig)
    receiver: ReceiverConfig = field(default_factory=ReceiverConfig)
    validation_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    normalization_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)


def _ranges(values: dict[str, Any]) -> dict[str, tuple[float, float]]:
    result: dict[str, tuple[float, float]] = {}
    for key, value in values.items():
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"range {key!r} must contain exactly two values")
        low, high = float(value[0]), float(value[1])
        if low >= high:
            raise ValueError(f"range {key!r} must have low < high")
        result[key] = (low, high)
    return result


def _validate(config: AppConfig) -> AppConfig:
    if not config.identity.gateway_id.strip():
        raise ValueError("identity.gateway_id must not be empty")
    if not config.identity.default_room_id.strip():
        raise ValueError("identity.default_room_id must not be empty")
    pipeline = config.pipeline
    positive_ints = {
        "buffer_max_size": pipeline.buffer_max_size,
        "validation_state_max_entries": pipeline.validation_state_max_entries,
        "resample_interval_sec": pipeline.resample_interval_sec,
        "window_size": pipeline.window_size,
        "window_step": pipeline.window_step,
        "node_silent_after_sec": pipeline.node_silent_after_sec,
    }
    for name, value in positive_ints.items():
        if value < 1:
            raise ValueError(f"pipeline.{name} must be >= 1")
    if not 0 < pipeline.min_valid_ratio <= 1:
        raise ValueError("pipeline.min_valid_ratio must be in (0, 1]")
    preprocessing = config.preprocessing
    if not preprocessing.version.strip():
        raise ValueError("preprocessing.version must not be empty")
    if preprocessing.state_max_entries < 1:
        raise ValueError("preprocessing.state_max_entries must be >= 1")
    allowed_filter_kinds = {"none", "ema", "median", "moving_average"}
    for field_name, spec in preprocessing.filters.items():
        if not isinstance(spec, dict):
            raise ValueError(f"preprocessing filter {field_name!r} must be a table")
        kind = str(spec.get("kind", "none"))
        if kind not in allowed_filter_kinds:
            raise ValueError(f"unsupported preprocessing filter kind for {field_name}: {kind}")
        alpha = float(spec.get("alpha", 0.25))
        window = int(spec.get("window", 3))
        if not 0.0 < alpha <= 1.0 or window < 1:
            raise ValueError(f"invalid preprocessing filter parameters for {field_name}")
    receiver = config.receiver
    if receiver.rotate_max_bytes < 0:
        raise ValueError("receiver.rotate_max_bytes must be >= 0")
    if receiver.serial_idle_sleep_sec <= 0:
        raise ValueError("receiver.serial_idle_sleep_sec must be > 0")
    if receiver.reconnect_initial_sec <= 0 or receiver.reconnect_max_sec <= 0:
        raise ValueError("receiver reconnect delays must be > 0")
    if receiver.reconnect_initial_sec > receiver.reconnect_max_sec:
        raise ValueError("receiver.reconnect_initial_sec must be <= reconnect_max_sec")
    if config.contract.data_topic != "iot/{gateway_id}/data":
        raise ValueError("contract.data_topic is frozen to iot/{gateway_id}/data")
    if config.contract.status_topic != "iot/{gateway_id}/status/sensor":
        raise ValueError("contract.status_topic is frozen to iot/{gateway_id}/status/sensor")
    return config


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_config(path: str | Path = "config/default.toml") -> AppConfig:
    config_path = Path(os.environ.get("IIOT_CONFIG_FILE", str(path)))
    if not config_path.is_file():
        raise FileNotFoundError(f"config file not found: {config_path}")
    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    identity_raw = raw.get("identity", {})
    pipeline_raw = raw.get("pipeline", {})
    contract_raw = raw.get("contract", {})
    preprocessing_raw = raw.get("preprocessing", {})
    receiver_raw = raw.get("receiver", {})
    gateway_id = os.environ.get(
        "IIOT_GATEWAY_ID", identity_raw.get("gateway_id", IdentityConfig.gateway_id)
    )
    config = AppConfig(
        identity=IdentityConfig(
            gateway_id=str(gateway_id),
            default_room_id=str(
                identity_raw.get("default_room_id", IdentityConfig.default_room_id)
            ),
        ),
        pipeline=PipelineConfig(**{**PipelineConfig().__dict__, **pipeline_raw}),
        contract=ContractConfig(**{**ContractConfig().__dict__, **contract_raw}),
        preprocessing=PreprocessingConfig(
            version=str(preprocessing_raw.get("version", PreprocessingConfig.version)),
            apply_to_v2=bool(preprocessing_raw.get("apply_to_v2", False)),
            state_max_entries=int(
                preprocessing_raw.get("state_max_entries", PreprocessingConfig.state_max_entries)
            ),
            filters={
                str(name): dict(spec)
                for name, spec in preprocessing_raw.get("filters", {}).items()
            },
        ),
        receiver=ReceiverConfig(**{**ReceiverConfig().__dict__, **receiver_raw}),
        validation_ranges=_ranges(raw.get("validation", {}).get("ranges", {})),
        normalization_ranges=_ranges(raw.get("normalization", {}).get("minmax", {})),
    )
    return _validate(config)
