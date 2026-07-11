from __future__ import annotations

from collections import defaultdict
from typing import Any

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
    """Physical/configured min-max transform with observable clipping.

    The transform remains deterministic and backward-compatible, but every
    lower/upper clip is counted so public/reference lane mismatch cannot remain
    silent. Reports contain counts only, never raw sensor data.
    """

    def __init__(self, ranges: dict[str, tuple[float, float]] | None = None) -> None:
        self.ranges = {**DEFAULT_RANGES, **(ranges or {})}
        self._stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"seen": 0, "lower_clipped": 0, "upper_clipped": 0}
        )

    @staticmethod
    def _scale(value: float, low: float, high: float) -> float:
        if high <= low:
            raise ValueError("normalization range must have high > low")
        return max(0.0, min(1.0, (float(value) - low) / (high - low)))

    def normalize(self, vector: FeatureVector) -> FeatureVector:
        values: dict[str, float] = {}
        for name, value in vector.values.items():
            low, high = self.ranges.get(name, (0.0, 1.0))
            number = float(value)
            stats = self._stats[name]
            stats["seen"] += 1
            if number < low:
                stats["lower_clipped"] += 1
            elif number > high:
                stats["upper_clipped"] += 1
            values[name] = self._scale(number, low, high)
        return FeatureVector(
            vector.gateway_id,
            vector.node_id,
            vector.room_id,
            vector.timestamp,
            values,
        )

    def report(self) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        total_seen = 0
        total_clipped = 0
        for name in sorted(self._stats):
            stats = self._stats[name]
            seen = stats["seen"]
            clipped = stats["lower_clipped"] + stats["upper_clipped"]
            total_seen += seen
            total_clipped += clipped
            fields[name] = {
                **stats,
                "clip_fraction": clipped / seen if seen else 0.0,
                "range": list(self.ranges.get(name, (0.0, 1.0))),
            }
        return {
            "schema": "iiot.ai_sensor.normalization_report.v1",
            "method": "configured_minmax_with_clip",
            "total_seen": total_seen,
            "total_clipped": total_clipped,
            "clip_fraction": total_clipped / total_seen if total_seen else 0.0,
            "fields": fields,
        }
