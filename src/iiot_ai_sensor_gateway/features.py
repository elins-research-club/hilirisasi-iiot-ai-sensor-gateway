from __future__ import annotations

from statistics import pstdev

from .contracts import FeatureVector, ResampledPoint

CANONICAL_SENSOR_FEATURES = (
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "bme_gas_ohm",
    "co_ppm",
    "no2_raw_mv",
    "no2_ratio",
    "o3_ppm",
    "co2_ppm",
    "pm1_ug_m3",
    "pm25_ug_m3",
    "pm10_ug_m3",
    "battery_voltage",
    "current_ma",
    "power_mw",
)
LEGACY_REFERENCE_FEATURES = ("voc_raw", "bme_gas_raw", "co_raw", "gas_raw")
DELTA_FIELDS = (
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "co_ppm",
    "o3_ppm",
    "co2_ppm",
    "pm25_ug_m3",
    "battery_voltage",
)
ROLLING_FIELDS = ("temperature_c", "humidity_pct", "co2_ppm", "pm25_ug_m3")

FEATURE_NAMES = (
    *CANONICAL_SENSOR_FEATURES,
    *LEGACY_REFERENCE_FEATURES,
    *(f"has_{name}" for name in CANONICAL_SENSOR_FEATURES),
    *(f"{name}_delta" for name in DELTA_FIELDS),
    *(name for field in ROLLING_FIELDS for name in (f"{field}_mean_3", f"{field}_std_3")),
    "missing_count",
    "valid_ratio",
    "seq_gap_count",
)


def _present_value(point: ResampledPoint | None, name: str) -> float | None:
    if point is None:
        return None
    value = getattr(point.sensor, name)
    return float(value) if value is not None else None


def _value(point: ResampledPoint | None, name: str) -> float:
    value = _present_value(point, name)
    return value if value is not None else 0.0


def _recent_values(points: list[ResampledPoint], name: str) -> list[float]:
    return [value for point in points if (value := _present_value(point, name)) is not None]


def extract_features(points: list[ResampledPoint]) -> list[FeatureVector]:
    vectors: list[FeatureVector] = []
    for index, point in enumerate(points):
        previous = points[index - 1] if index else None
        recent = points[max(0, index - 2) : index + 1]
        values: dict[str, float] = {
            name: _value(point, name)
            for name in (*CANONICAL_SENSOR_FEATURES, *LEGACY_REFERENCE_FEATURES)
        }
        for name in CANONICAL_SENSOR_FEATURES:
            values[f"has_{name}"] = 1.0 if _present_value(point, name) is not None else 0.0
        for name in DELTA_FIELDS:
            current = _present_value(point, name)
            prior = _present_value(previous, name)
            values[f"{name}_delta"] = 0.0 if current is None or prior is None else current - prior
        for name in ROLLING_FIELDS:
            recent_values = _recent_values(recent, name)
            values[f"{name}_mean_3"] = (
                sum(recent_values) / len(recent_values) if recent_values else 0.0
            )
            values[f"{name}_std_3"] = pstdev(recent_values) if len(recent_values) > 1 else 0.0
        values.update(
            {
                "missing_count": float(point.missing_count),
                "valid_ratio": float(point.valid_ratio),
                "seq_gap_count": float(point.seq_gap_count),
            }
        )
        vectors.append(
            FeatureVector(point.gateway_id, point.node_id, point.room_id, point.timestamp, values)
        )
    return vectors
