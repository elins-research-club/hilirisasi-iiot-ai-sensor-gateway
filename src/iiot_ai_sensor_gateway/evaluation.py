from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .parser import PayloadParser
from .validation import ReadingValidator
from .window_paths import resolve_windows_path

TARGET_FIELDS = ('temperature_c', 'humidity_pct', 'pressure_hpa', 'bme_gas_raw', 'co_raw')
USED_COLUMNS = ('temp', 'humidity', 'co', 'lpg', 'smoke')
IGNORED_COLUMNS = ('light', 'motion')
PROXY_COLUMNS = ('lpg', 'smoke')

@dataclass(frozen=True)
class EvaluationResult:
    pipeline_status: str
    dataset_coverage_status: str
    lstm_readiness: str
    input_source: str
    simulation_layer: str
    gateway_layer: str
    total_data: int
    node_count: int
    nodes: tuple[str, ...]
    start_timestamp: str | None
    end_timestamp: str | None
    valid_data: int
    invalid_data: int
    missing_rate: dict[str, float]
    used_columns: tuple[str, ...]
    ignored_columns: tuple[str, ...]
    proxy_columns: tuple[str, ...]
    window_count: int
    shape: tuple[int, int, int]
    nan_count: int
    inf_count: int
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data['nodes'] = list(self.nodes)
        data['used_columns'] = list(self.used_columns)
        data['ignored_columns'] = list(self.ignored_columns)
        data['proxy_columns'] = list(self.proxy_columns)
        data['warnings'] = list(self.warnings)
        data['shape'] = list(self.shape)
        return data

def evaluate_preprocessing(
    canonical_jsonl: str | Path,
    windows_jsonl: str | Path,
    config: AppConfig,
    input_source: str = 'gary_stafford_canonical_or_compact_payload',
    simulation_layer: str = 'dataset_or_esp32_light_preprocessing',
    gateway_layer: str = 'raspberry_pi_pre_model_pipeline',
) -> EvaluationResult:
    parser = PayloadParser(config.identity.gateway_id, config.identity.default_room_id)
    validator = ReadingValidator(
        config.validation_ranges,
        config.pipeline.sequence_gap_warn,
        config.pipeline.validation_state_max_entries,
    )
    total = valid = invalid = 0
    nodes: set[str] = set()
    start = None
    end = None
    missing = {name: 0 for name in TARGET_FIELDS}
    for line in Path(canonical_jsonl).read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        total += 1
        reading = parser.parse(line)
        result = validator.validate(reading)
        valid += 1 if result.is_valid else 0
        invalid += 0 if result.is_valid else 1
        nodes.add(reading.node_id)
        start = reading.timestamp if start is None or reading.timestamp < start else start
        end = reading.timestamp if end is None or reading.timestamp > end else end
        values = reading.sensor.as_dict()
        for name in TARGET_FIELDS:
            if values.get(name) is None:
                missing[name] += 1
    window_count = 0
    timesteps = 0
    features = 0
    nan_count = 0
    inf_count = 0
    windows_path = resolve_windows_path(windows_jsonl)
    if windows_path.exists():
        for line in windows_path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            window_count += 1
            shape = record.get('shape') or [0, 0]
            timesteps, features = int(shape[0]), int(shape[1])
            for row in record.get('x', []):
                for value in row:
                    if isinstance(value, float) and math.isnan(value):
                        nan_count += 1
                    if isinstance(value, float) and math.isinf(value):
                        inf_count += 1
    rates = {name: (missing[name] / total if total else 1.0) for name in TARGET_FIELDS}
    warnings: list[str] = []
    if rates['pressure_hpa'] >= 1.0:
        warnings.append('pressure_hpa_unavailable_in_gary')
    if 'gary' in input_source and rates['bme_gas_raw'] < 1.0:
        warnings.append('bme_gas_raw_uses_lpg_smoke_proxy')
    if 'derived_project_schema' in input_source or 'project_schema' in input_source:
        warnings.append('pressure_hpa_synthetic_in_derived_dataset')
        warnings.append('bme_gas_raw_scaled_from_lpg_smoke')
    if window_count == 0 or nan_count or inf_count or invalid > total * 0.05:
        pipeline_status = 'FAIL'
    else:
        pipeline_status = 'PASS'
    dataset_coverage_status = 'FULL'
    if rates['pressure_hpa'] >= 1.0:
        dataset_coverage_status = 'PARTIAL'
    if pipeline_status == 'FAIL':
        lstm_readiness = 'NOT_READY'
    elif dataset_coverage_status == 'PARTIAL':
        lstm_readiness = 'READY_WITH_LIMITATIONS'
    else:
        lstm_readiness = 'READY'
    return EvaluationResult(
        pipeline_status,
        dataset_coverage_status,
        lstm_readiness,
        input_source,
        simulation_layer,
        gateway_layer,
        total,
        len(nodes),
        tuple(sorted(nodes)),
        start.isoformat() if start else None,
        end.isoformat() if end else None,
        valid,
        invalid,
        rates,
        USED_COLUMNS,
        IGNORED_COLUMNS,
        PROXY_COLUMNS,
        window_count,
        (window_count, timesteps, features),
        nan_count,
        inf_count,
        tuple(warnings),
    )

def write_evaluation_report(result: EvaluationResult, output_json: str | Path) -> None:
    path = Path(output_json)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.as_dict(), indent=2), encoding='utf-8')
