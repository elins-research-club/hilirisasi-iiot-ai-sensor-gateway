from __future__ import annotations

from statistics import pstdev

from .contracts import FeatureVector, ResampledPoint

FEATURE_NAMES = (
    'temperature_c', 'humidity_pct', 'pressure_hpa', 'voc_raw', 'bme_gas_raw', 'co_raw', 'gas_raw',
    'temperature_delta', 'humidity_delta', 'pressure_delta', 'gas_delta', 'gas_mean_3', 'gas_std_3',
    'missing_count', 'valid_ratio', 'seq_gap_count',
)

def _value(point: ResampledPoint | None, name: str) -> float:
    if point is None:
        return 0.0
    value = getattr(point.sensor, name)
    return float(value) if value is not None else 0.0

def _gas(point: ResampledPoint | None) -> float:
    return _value(point, 'voc_raw') or _value(point, 'bme_gas_raw') or _value(point, 'co_raw') or _value(point, 'gas_raw')

def extract_features(points: list[ResampledPoint]) -> list[FeatureVector]:
    vectors: list[FeatureVector] = []
    for index, point in enumerate(points):
        previous = points[index - 1] if index else None
        recent = points[max(0, index - 2): index + 1]
        gas_values = [_gas(item) for item in recent]
        values = {
            'temperature_c': _value(point, 'temperature_c'),
            'humidity_pct': _value(point, 'humidity_pct'),
            'pressure_hpa': _value(point, 'pressure_hpa'),
            'voc_raw': _value(point, 'voc_raw'),
            'bme_gas_raw': _value(point, 'bme_gas_raw'),
            'co_raw': _value(point, 'co_raw'),
            'gas_raw': _value(point, 'gas_raw'),
            'temperature_delta': _value(point, 'temperature_c') - _value(previous, 'temperature_c'),
            'humidity_delta': _value(point, 'humidity_pct') - _value(previous, 'humidity_pct'),
            'pressure_delta': _value(point, 'pressure_hpa') - _value(previous, 'pressure_hpa'),
            'gas_delta': _gas(point) - (_gas(previous) if previous else _gas(point)),
            'gas_mean_3': sum(gas_values) / len(gas_values),
            'gas_std_3': pstdev(gas_values) if len(gas_values) > 1 else 0.0,
            'missing_count': float(point.missing_count),
            'valid_ratio': float(point.valid_ratio),
            'seq_gap_count': float(point.seq_gap_count),
        }
        vectors.append(FeatureVector(point.gateway_id, point.node_id, point.room_id, point.timestamp, values))
    return vectors
