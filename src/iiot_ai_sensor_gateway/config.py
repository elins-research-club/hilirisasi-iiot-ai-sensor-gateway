from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class IdentityConfig:
    gateway_id: str = 'raspi_gateway_01'
    default_room_id: str = 'room_A'

@dataclass(frozen=True)
class PipelineConfig:
    buffer_max_size: int = 512
    resample_interval_sec: int = 60
    window_size: int = 12
    window_step: int = 1
    min_valid_ratio: float = 0.75
    node_silent_after_sec: int = 180
    sequence_gap_warn: bool = True

@dataclass(frozen=True)
class AppConfig:
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    validation_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    normalization_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

def _ranges(values: dict[str, Any]) -> dict[str, tuple[float, float]]:
    return {key: (float(val[0]), float(val[1])) for key, val in values.items() if len(val) == 2}

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())

def load_config(path: str | Path = 'config/default.toml') -> AppConfig:
    config_path = Path(os.environ.get('IIOT_CONFIG_FILE', str(path)))
    with config_path.open('rb') as file:
        raw = tomllib.load(file)
    identity_raw = raw.get('identity', {})
    pipeline_raw = raw.get('pipeline', {})
    gateway_id = os.environ.get('IIOT_GATEWAY_ID', identity_raw.get('gateway_id', 'raspi_gateway_01'))
    identity = IdentityConfig(gateway_id=gateway_id, default_room_id=identity_raw.get('default_room_id', 'room_A'))
    pipeline = PipelineConfig(**{**PipelineConfig().__dict__, **pipeline_raw})
    return AppConfig(
        identity=identity,
        pipeline=pipeline,
        validation_ranges=_ranges(raw.get('validation', {}).get('ranges', {})),
        normalization_ranges=_ranges(raw.get('normalization', {}).get('minmax', {})),
    )
