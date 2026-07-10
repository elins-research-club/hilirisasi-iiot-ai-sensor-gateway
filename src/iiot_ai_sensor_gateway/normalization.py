from __future__ import annotations

from .contracts import FeatureVector
from .features import CANONICAL_SENSOR_FEATURES, DELTA_FIELDS

DEFAULT_RANGES: dict[str, tuple[float, float]] = {
    "temperature_c": (-10.0, 80.0),
    "humidity_pct": (0.0, 100.0),
    "pressure_hpa": (800.0, 1200.0),
    "bme_gas_ohm": (100.0, 10_000_000.0),
    "co_ppm": (0.0, 1000.0),
    "no2_raw_mv": (0.0, 3300.0),
    "no2_ratio": (0.0, 20.0),
    "o3_ppm": (0.0, 10.0),
    "co2_ppm": (0.0, 10_000.0),
    "pm1_ug_m3": (0.0, 5000.0),
    "pm25_ug_m3": (0.0, 5000.0),
    "pm10_ug_m3": (0.0, 5000.0),
    "battery_voltage": (0.0, 60.0),
    "current_ma": (-20_000.0, 20_000.0),
    "power_mw": (-1_000_000.0, 1_000_000.0),
    "voc_raw": (0.0, 5000.0),
    "bme_gas_raw": (0.0, 50_000.0),
    "co_raw": (0.0, 1.0),
    "gas_raw": (0.0, 50_000.0),
    "missing_count": (0.0, 64.0),
    "valid_ratio": (0.0, 1.0),
    "seq_gap_count": (0.0, 32.0),
}

for field in CANONICAL_SENSOR_FEATURES:
    DEFAULT_RANGES[f"has_{field}"] = (0.0, 1.0)
for field in DELTA_FIELDS:
    low, high = DEFAULT_RANGES[field]
    span = high - low
    DEFAULT_RANGES[f"{field}_delta"] = (-span, span)
for field in ("temperature_c", "humidity_pct", "co2_ppm", "pm25_ug_m3"):
    DEFAULT_RANGES[f"{field}_mean_3"] = DEFAULT_RANGES[field]
    low, high = DEFAULT_RANGES[field]
    DEFAULT_RANGES[f"{field}_std_3"] = (0.0, high - low)


class MinMaxNormalizer:
    def __init__(self, ranges: dict[str, tuple[float, float]] | None = None) -> None:
        self.ranges = {**DEFAULT_RANGES, **(ranges or {})}

    @staticmethod
    def _scale(value: float, low: float, high: float) -> float:
        if high <= low:
            raise ValueError("normalization range must have high > low")
        return max(0.0, min(1.0, (float(value) - low) / (high - low)))

    def normalize(self, vector: FeatureVector) -> FeatureVector:
        values: dict[str, float] = {}
        for name, value in vector.values.items():
            low, high = self.ranges.get(name, (0.0, 1.0))
            values[name] = self._scale(float(value), low, high)
        return FeatureVector(
            vector.gateway_id,
            vector.node_id,
            vector.room_id,
            vector.timestamp,
            values,
        )
