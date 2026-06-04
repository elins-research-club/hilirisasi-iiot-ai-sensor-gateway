from __future__ import annotations

from .contracts import FeatureVector
from .features import FEATURE_NAMES

DEFAULT_RANGES = {
    'temperature_c': (-10.0, 80.0),
    'humidity_pct': (0.0, 100.0),
    'pressure_hpa': (900.0, 1100.0),
    'voc_raw': (0.0, 5000.0),
    'bme_gas_raw': (0.0, 5000.0),
    'co_raw': (0.0, 0.05),
    'gas_raw': (0.0, 5000.0),
    'temperature_delta': (-10.0, 10.0),
    'humidity_delta': (-20.0, 20.0),
    'pressure_delta': (-20.0, 20.0),
    'gas_delta': (-1.0, 1.0),
    'gas_mean_3': (0.0, 0.05),
    'gas_std_3': (0.0, 0.02),
    'missing_count': (0.0, 10.0),
    'valid_ratio': (0.0, 1.0),
    'seq_gap_count': (0.0, 10.0),
}

class MinMaxNormalizer:
    def __init__(self, ranges: dict[str, tuple[float, float]]) -> None:
        self.ranges = {**DEFAULT_RANGES, **ranges}

    def normalize(self, vector: FeatureVector) -> FeatureVector:
        values: dict[str, float] = {}
        for name in FEATURE_NAMES:
            value = float(vector.values.get(name, 0.0))
            if name in self.ranges:
                low, high = self.ranges[name]
                value = 0.0 if high == low else (value - low) / (high - low)
                value = min(1.0, max(0.0, value))
            values[name] = value
        return FeatureVector(vector.gateway_id, vector.node_id, vector.room_id, vector.timestamp, values)
