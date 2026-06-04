from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import SensorReading, ValidationResult

REQUIRED_FIELDS = ('temperature_c', 'humidity_pct')
GAS_FIELDS = ('voc_raw', 'bme_gas_raw', 'co_raw', 'gas_raw')
ERROR_STATUSES = {'sensor_error', 'invalid', 'error'}
SOFT_ISSUES = {'sequence_gap', 'missing_pressure_hpa', 'missing_voc_raw', 'missing_bme_gas_raw'}

@dataclass
class NodeValidationState:
    last_sequence: dict[str, int] = field(default_factory=dict)

class ReadingValidator:
    def __init__(self, ranges: dict[str, tuple[float, float]], sequence_gap_warn: bool = True) -> None:
        self.ranges = ranges
        self.sequence_gap_warn = sequence_gap_warn
        self.state = NodeValidationState()

    def validate(self, reading: SensorReading) -> ValidationResult:
        issues: list[str] = []
        values = reading.sensor.as_dict()
        if not reading.node_id:
            issues.append('missing_node_id')
        for field_name in REQUIRED_FIELDS:
            if values[field_name] is None:
                issues.append(f'missing_{field_name}')
        if all(values[name] is None for name in GAS_FIELDS):
            issues.append('missing_gas_signal')
        for optional_name in ('pressure_hpa', 'voc_raw', 'bme_gas_raw'):
            if values.get(optional_name) is None:
                issues.append(f'missing_{optional_name}')
        for field_name, value in values.items():
            if value is not None and field_name in self.ranges:
                low, high = self.ranges[field_name]
                if not low <= value <= high:
                    issues.append(f'range_{field_name}')
        if reading.status.lower() in ERROR_STATUSES or 'sensor_error' in reading.flags:
            issues.append('sensor_error')
        if reading.quality.lower() not in {'valid', 'ok'}:
            issues.append(f'quality_{reading.quality.lower()}')
        previous = self.state.last_sequence.get(reading.node_id)
        if self.sequence_gap_warn and previous is not None and reading.sequence != previous + 1:
            issues.append('sequence_gap')
        self.state.last_sequence[reading.node_id] = reading.sequence
        hard_invalid = [issue for issue in issues if issue not in SOFT_ISSUES]
        return ValidationResult(reading=reading, is_valid=not hard_invalid, issues=tuple(issues))
