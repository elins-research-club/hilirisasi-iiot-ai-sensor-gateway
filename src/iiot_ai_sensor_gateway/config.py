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
class ChirpStackConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    topic: str = "application/+/device/+/event/up"
    client_id: str = "iiot-ai-sensor-shadow"
    keepalive: int = 30
    queue_max: int = 4096
    reconnect_min_sec: int = 1
    reconnect_max_sec: int = 30
    allow_dev_eui: tuple[str, ...] = ()
    room_by_dev_eui: dict[str, str] = field(default_factory=dict)
    username_env: str = "IIOT_CHIRPSTACK_USER"
    password_env: str = "IIOT_CHIRPSTACK_PASS"


@dataclass(frozen=True)
class LiveRuntimeConfig:
    mode: str = "shadow_ingest"
    output_dir: str = "data/live_runtime"
    rotate_max_bytes: int = 10 * 1024 * 1024
    anomaly_enabled: bool = True
    anomaly_warmup_samples: int = 64
    anomaly_threshold: float = 0.7
    config_version: str = "sensor-live-v1"


@dataclass(frozen=True)
class PublisherConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    client_id: str = "iiot-ai-sensor-publisher"
    keepalive: int = 30
    qos: int = 1
    publish_timeout_sec: float = 10.0
    reconnect_min_sec: int = 1
    reconnect_max_sec: int = 30
    outbox_path: str = "data/live_runtime/outbox.sqlite3"
    outbox_max_entries: int = 10000
    username_env: str = "IIOT_PUBLISH_USER"
    password_env: str = "IIOT_PUBLISH_PASS"


@dataclass(frozen=True)
class ForecastRuntimeConfig:
    enabled: bool = False
    manifest_path: str = ""
    target_node_id: str = ""
    cadence_tolerance_fraction: float = 0.20


@dataclass(frozen=True)
class AppConfig:
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    contract: ContractConfig = field(default_factory=ContractConfig)
    preprocessing: PreprocessingConfig = field(default_factory=PreprocessingConfig)
    receiver: ReceiverConfig = field(default_factory=ReceiverConfig)
    chirpstack: ChirpStackConfig = field(default_factory=ChirpStackConfig)
    live_runtime: LiveRuntimeConfig = field(default_factory=LiveRuntimeConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    forecast_runtime: ForecastRuntimeConfig = field(default_factory=ForecastRuntimeConfig)
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
    chirpstack = config.chirpstack
    if not chirpstack.host or chirpstack.port <= 0 or chirpstack.keepalive <= 0:
        raise ValueError("chirpstack host/port/keepalive are invalid")
    if chirpstack.queue_max < 1:
        raise ValueError("chirpstack.queue_max must be >= 1")
    if chirpstack.reconnect_min_sec < 1 or chirpstack.reconnect_max_sec < chirpstack.reconnect_min_sec:
        raise ValueError("chirpstack reconnect delay is invalid")
    if config.live_runtime.mode not in {"shadow_ingest", "shadow_ai", "publish_ai"}:
        raise ValueError("live_runtime.mode must be shadow_ingest, shadow_ai, or publish_ai")
    if config.live_runtime.rotate_max_bytes < 0:
        raise ValueError("live_runtime.rotate_max_bytes must be >= 0")
    if config.live_runtime.anomaly_warmup_samples < 1:
        raise ValueError("live_runtime.anomaly_warmup_samples must be >= 1")
    if not 0 <= config.live_runtime.anomaly_threshold <= 1:
        raise ValueError("live_runtime.anomaly_threshold must be in [0,1]")
    publisher = config.publisher
    if not publisher.host or publisher.port <= 0 or publisher.keepalive <= 0:
        raise ValueError("publisher host/port/keepalive are invalid")
    if publisher.qos != 1:
        raise ValueError("publisher.qos is frozen to 1")
    if publisher.publish_timeout_sec <= 0 or publisher.outbox_max_entries < 1:
        raise ValueError("publisher timeout/outbox settings are invalid")
    if publisher.reconnect_min_sec < 1 or publisher.reconnect_max_sec < publisher.reconnect_min_sec:
        raise ValueError("publisher reconnect delay is invalid")
    if config.live_runtime.mode == "publish_ai" and not publisher.enabled:
        raise ValueError("publish_ai mode requires publisher.enabled=true")
    forecast_runtime = config.forecast_runtime
    if forecast_runtime.enabled and not forecast_runtime.manifest_path.strip():
        raise ValueError("forecast_runtime.manifest_path is required when enabled")
    if not 0 <= forecast_runtime.cadence_tolerance_fraction <= 1:
        raise ValueError("forecast_runtime.cadence_tolerance_fraction must be in [0,1]")
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
    chirpstack_raw = raw.get("chirpstack", {})
    live_runtime_raw = raw.get("live_runtime", {})
    publisher_raw = raw.get("publisher", {})
    forecast_runtime_raw = raw.get("forecast_runtime", {})
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
        chirpstack=ChirpStackConfig(
            enabled=bool(chirpstack_raw.get("enabled", False)),
            host=str(os.environ.get("IIOT_CHIRPSTACK_HOST", chirpstack_raw.get("host", "127.0.0.1"))),
            port=int(os.environ.get("IIOT_CHIRPSTACK_PORT", chirpstack_raw.get("port", 1883))),
            topic=str(chirpstack_raw.get("topic", ChirpStackConfig.topic)),
            client_id=str(chirpstack_raw.get("client_id", ChirpStackConfig.client_id)),
            keepalive=int(chirpstack_raw.get("keepalive", ChirpStackConfig.keepalive)),
            queue_max=int(chirpstack_raw.get("queue_max", ChirpStackConfig.queue_max)),
            reconnect_min_sec=int(chirpstack_raw.get("reconnect_min_sec", ChirpStackConfig.reconnect_min_sec)),
            reconnect_max_sec=int(chirpstack_raw.get("reconnect_max_sec", ChirpStackConfig.reconnect_max_sec)),
            allow_dev_eui=tuple(str(item).lower() for item in chirpstack_raw.get("allow_dev_eui", [])),
            room_by_dev_eui={str(k).lower(): str(v) for k, v in chirpstack_raw.get("room_by_dev_eui", {}).items()},
            username_env=str(chirpstack_raw.get("username_env", ChirpStackConfig.username_env)),
            password_env=str(chirpstack_raw.get("password_env", ChirpStackConfig.password_env)),
        ),
        live_runtime=LiveRuntimeConfig(
            **{
                **LiveRuntimeConfig().__dict__,
                **live_runtime_raw,
                "mode": str(
                    os.environ.get(
                        "IIOT_LIVE_MODE",
                        live_runtime_raw.get("mode", LiveRuntimeConfig.mode),
                    )
                ),
                "output_dir": str(
                    os.environ.get(
                        "IIOT_LIVE_OUTPUT_DIR",
                        live_runtime_raw.get(
                            "output_dir", LiveRuntimeConfig.output_dir
                        ),
                    )
                ),
            }
        ),
        publisher=PublisherConfig(
            enabled=str(os.environ.get("IIOT_PUBLISH_ENABLED", publisher_raw.get("enabled", False))).lower() in {"1", "true", "yes", "on"},
            host=str(os.environ.get("IIOT_PUBLISH_HOST", publisher_raw.get("host", "127.0.0.1"))),
            port=int(os.environ.get("IIOT_PUBLISH_PORT", publisher_raw.get("port", 1883))),
            client_id=str(publisher_raw.get("client_id", PublisherConfig.client_id)),
            keepalive=int(publisher_raw.get("keepalive", PublisherConfig.keepalive)),
            qos=int(publisher_raw.get("qos", PublisherConfig.qos)),
            publish_timeout_sec=float(publisher_raw.get("publish_timeout_sec", PublisherConfig.publish_timeout_sec)),
            reconnect_min_sec=int(publisher_raw.get("reconnect_min_sec", PublisherConfig.reconnect_min_sec)),
            reconnect_max_sec=int(publisher_raw.get("reconnect_max_sec", PublisherConfig.reconnect_max_sec)),
            outbox_path=str(publisher_raw.get("outbox_path", PublisherConfig.outbox_path)),
            outbox_max_entries=int(publisher_raw.get("outbox_max_entries", PublisherConfig.outbox_max_entries)),
            username_env=str(publisher_raw.get("username_env", PublisherConfig.username_env)),
            password_env=str(publisher_raw.get("password_env", PublisherConfig.password_env)),
        ),
        forecast_runtime=ForecastRuntimeConfig(
            enabled=str(os.environ.get("IIOT_FORECAST_ENABLED", forecast_runtime_raw.get("enabled", False))).lower() in {"1", "true", "yes", "on"},
            manifest_path=str(forecast_runtime_raw.get("manifest_path", "")),
            target_node_id=str(forecast_runtime_raw.get("target_node_id", "")).lower(),
            cadence_tolerance_fraction=float(
                forecast_runtime_raw.get(
                    "cadence_tolerance_fraction",
                    ForecastRuntimeConfig.cadence_tolerance_fraction,
                )
            ),
        ),
        validation_ranges=_ranges(raw.get("validation", {}).get("ranges", {})),
        normalization_ranges=_ranges(raw.get("normalization", {}).get("minmax", {})),
    )
    return _validate(config)
