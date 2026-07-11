from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TypeVar

from .contracts import SensorReading, ValidationResult

ERROR_STATUSES = {"sensor_error", "invalid", "error", "failed"}
ACCEPTED_QUALITIES = {"valid", "ok", "partial", "degraded", "hardware_observation"}
SOFT_ISSUES = {
    "sequence_gap",
    "node_reboot",
    "legacy_contract_v1",
    "missing_pressure_hpa",
    "missing_bme_gas_ohm",
    "missing_co_ppm",
    "missing_no2_signal",
    "missing_o3_ppm",
    "missing_co2_ppm",
    "missing_pm",
    "missing_power",
}


@dataclass
class NodeValidationState:
    last_sequence: OrderedDict[tuple[str, str, str], int] = field(default_factory=OrderedDict)
    last_boot: OrderedDict[tuple[str, str], str] = field(default_factory=OrderedDict)
    seen_event_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)


_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


def _remember_bounded(
    mapping: OrderedDict[_Key, _Value],
    key: _Key,
    value: _Value,
    max_entries: int,
) -> None:
    """Insert/update one LRU entry without allowing validator state to grow forever."""

    if key in mapping:
        mapping.move_to_end(key)
    mapping[key] = value
    while len(mapping) > max_entries:
        mapping.popitem(last=False)


class ReadingValidator:
    def __init__(
        self,
        ranges: dict[str, tuple[float, float]],
        sequence_gap_warn: bool = True,
        state_max_entries: int = 100_000,
    ) -> None:
        if state_max_entries < 1:
            raise ValueError("state_max_entries must be >= 1")
        self.ranges = ranges
        self.sequence_gap_warn = sequence_gap_warn
        self.state_max_entries = state_max_entries
        self.state = NodeValidationState()

    @staticmethod
    def _missing_groups(reading: SensorReading) -> list[str]:
        sensor = reading.sensor
        issues: list[str] = []
        if sensor.pressure_hpa is None:
            issues.append("missing_pressure_hpa")
        if sensor.bme_gas_ohm is None and sensor.bme_gas_raw is None:
            issues.append("missing_bme_gas_ohm")
        if sensor.co_ppm is None and sensor.co_raw is None:
            issues.append("missing_co_ppm")
        if sensor.no2_raw_mv is None and sensor.no2_ratio is None:
            issues.append("missing_no2_signal")
        if sensor.o3_ppm is None:
            issues.append("missing_o3_ppm")
        if sensor.co2_ppm is None:
            issues.append("missing_co2_ppm")
        if all(value is None for value in (sensor.pm1_ug_m3, sensor.pm25_ug_m3, sensor.pm10_ug_m3)):
            issues.append("missing_pm")
        if all(value is None for value in (sensor.battery_voltage, sensor.current_ma, sensor.power_mw)):
            issues.append("missing_power")
        return issues

    def validate(self, reading: SensorReading) -> ValidationResult:
        issues: list[str] = []
        values = reading.sensor.as_dict()
        if not reading.gateway_id:
            issues.append("missing_gateway_id")
        if not reading.node_id:
            issues.append("missing_node_id")
        if not reading.room_id:
            issues.append("missing_room_id")
        if reading.sensor.present_count() == 0:
            issues.append("missing_sensor_data")
        issues.extend(self._missing_groups(reading))

        for field_name, value in values.items():
            if value is not None and field_name in self.ranges:
                low, high = self.ranges[field_name]
                if not low <= value <= high:
                    issues.append(f"range_{field_name}")

        status = reading.status.lower()
        quality = reading.quality.lower()
        if status in ERROR_STATUSES or "sensor_error" in reading.flags:
            issues.append("sensor_error")
        if quality not in ACCEPTED_QUALITIES:
            issues.append(f"quality_{quality}")
        if reading.compact_version == 1:
            issues.append("legacy_contract_v1")

        node_identity = (reading.gateway_id, reading.node_id)
        previous_boot = self.state.last_boot.get(node_identity)
        if previous_boot is not None and previous_boot != reading.boot_id:
            issues.append("node_reboot")
        sequence_key = (reading.gateway_id, reading.node_id, reading.boot_id)
        previous = self.state.last_sequence.get(sequence_key)
        sequence_issue: str | None = None
        if previous is not None:
            if reading.sequence == previous:
                sequence_issue = "duplicate_sequence"
            elif reading.sequence < previous:
                sequence_issue = "out_of_order_sequence"
            elif self.sequence_gap_warn and reading.sequence > previous + 1:
                sequence_issue = "sequence_gap"
        if reading.event_id in self.state.seen_event_ids and sequence_issue is None:
            sequence_issue = "duplicate_event_id"
        if sequence_issue:
            issues.append(sequence_issue)

        hard_invalid = [issue for issue in issues if issue not in SOFT_ISSUES]
        is_valid = not hard_invalid
        if is_valid and sequence_issue not in {"duplicate_sequence", "out_of_order_sequence", "duplicate_event_id"}:
            _remember_bounded(
                self.state.last_sequence, sequence_key, reading.sequence, self.state_max_entries
            )
            _remember_bounded(
                self.state.last_boot, node_identity, reading.boot_id, self.state_max_entries
            )
            _remember_bounded(
                self.state.seen_event_ids, reading.event_id, None, self.state_max_entries
            )
        return ValidationResult(reading=reading, is_valid=is_valid, issues=tuple(dict.fromkeys(issues)))
