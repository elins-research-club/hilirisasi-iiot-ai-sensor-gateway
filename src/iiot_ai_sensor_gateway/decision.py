from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

DECISION_SCHEMA = "sensor_decision.v1"
SEVERITY = {"unknown": -1, "normal": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True)
class ThresholdBand:
    warning_high: float | None = None
    critical_high: float | None = None
    warning_low: float | None = None
    critical_low: float | None = None


@dataclass(frozen=True)
class DecisionConfig:
    # Project commissioning defaults only. They are not regulatory limits and
    # must be reviewed for each deployment.
    thresholds: dict[str, ThresholdBand] = field(
        default_factory=lambda: {
            "temperature_c": ThresholdBand(35.0, 38.0, 10.0, 5.0),
            "humidity_pct": ThresholdBand(85.0, 90.0, 25.0, 15.0),
            "co_ppm": ThresholdBand(9.0, 35.0),
            "o3_ppm": ThresholdBand(0.07, 0.10),
            "co2_ppm": ThresholdBand(1000.0, 2000.0),
            "pm25_ug_m3": ThresholdBand(35.0, 75.0),
            "battery_voltage": ThresholdBand(warning_low=3.5, critical_low=3.3),
            "no2_ratio": ThresholdBand(1.5, 2.5),
        }
    )
    anomaly_warning: float = 0.7
    anomaly_critical: float = 0.9

    def validate(self) -> None:
        if not 0 <= self.anomaly_warning <= self.anomaly_critical <= 1:
            raise ValueError("anomaly thresholds must satisfy 0 <= warning <= critical <= 1")


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _threshold_status(value: float, band: ThresholdBand) -> str:
    if band.critical_high is not None and value >= band.critical_high:
        return "critical"
    if band.critical_low is not None and value <= band.critical_low:
        return "critical"
    if band.warning_high is not None and value >= band.warning_high:
        return "warning"
    if band.warning_low is not None and value <= band.warning_low:
        return "warning"
    return "normal"


def build_sensor_decision(
    *,
    sensor: dict[str, Any],
    quality: str,
    source_status: str,
    anomaly_score: float | None = None,
    drift_detected: bool = False,
    forecast_status: str = "unavailable",
    model_readiness: str = "EXPERIMENTAL",
    config: DecisionConfig | None = None,
) -> dict[str, Any]:
    selected = config or DecisionConfig()
    selected.validate()
    quality_normalized = str(quality).lower()
    source_normalized = str(source_status).lower()
    if quality_normalized not in {"valid", "ok", "partial", "degraded"} or source_normalized in {
        "invalid",
        "sensor_error",
        "offline",
        "stale",
        "unknown",
    }:
        return {
            "schema_version": DECISION_SCHEMA,
            "env_status": "unknown",
            "main_factor": "data_quality",
            "battery_status": "unknown",
            "node_health": "invalid" if source_normalized != "offline" else "offline",
            "confidence": None,
            "abstain": True,
            "reason": "invalid, stale, or unavailable source data",
            "forecast_status": forecast_status,
            "model_readiness": model_readiness,
        }

    candidates: list[tuple[str, str, str]] = []
    battery_status = "unknown"
    for field_name, band in selected.thresholds.items():
        value = _number(sensor.get(field_name))
        if value is None:
            continue
        status = _threshold_status(value, band)
        if field_name == "battery_voltage":
            battery_status = status
        elif status != "normal":
            candidates.append((status, field_name, f"{field_name} crossed project commissioning threshold"))

    score = _number(anomaly_score)
    if score is not None:
        if score >= selected.anomaly_critical:
            candidates.append(("critical", "anomaly_score", "streaming anomaly score crossed critical threshold"))
        elif score >= selected.anomaly_warning:
            candidates.append(("warning", "anomaly_score", "streaming anomaly score crossed warning threshold"))
    if drift_detected:
        candidates.append(("warning", "model_drift", "online drift detector reported a distribution change"))

    if candidates:
        status, factor, reason = max(candidates, key=lambda item: SEVERITY[item[0]])
    else:
        status, factor, reason = "normal", "none", "no project commissioning rule was crossed"
    missing_ratio = sum(sensor.get(name) is None for name in selected.thresholds) / max(1, len(selected.thresholds))
    base_confidence = max(0.0, 1.0 - missing_ratio)
    if quality_normalized in {"partial", "degraded"}:
        base_confidence *= 0.6
    if model_readiness not in {"PROMISING", "PRODUCTION"}:
        base_confidence *= 0.8
    return {
        "schema_version": DECISION_SCHEMA,
        "env_status": status,
        "main_factor": factor,
        "battery_status": battery_status,
        "node_health": "healthy" if quality_normalized in {"valid", "ok"} else "degraded",
        "confidence": round(base_confidence, 4),
        "abstain": False,
        "reason": reason,
        "forecast_status": forecast_status,
        "model_readiness": model_readiness,
        "no2_semantics": "ordinal_ratio_only" if sensor.get("no2_ratio") is not None else "unavailable",
    }
