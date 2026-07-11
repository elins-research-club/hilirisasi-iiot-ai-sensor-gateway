from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from typing import Any

from ..contracts import SensorValues, ValidationResult
from .filters import FieldFilter, FilterSpec


class GatewaySemanticPreprocessor:
    """Apply versioned, per-node semantic filters without cross-node state.

    Compact v3 is a hardware observation and is eligible by default. Compact v2
    already contains node-side smoothing, so it is passed through unless
    `apply_to_v2=True` is explicitly selected for a controlled migration study.
    """

    def __init__(
        self,
        *,
        version: str,
        filters: dict[str, FilterSpec | dict[str, Any]] | None = None,
        apply_to_v2: bool = False,
        state_max_entries: int = 100_000,
    ) -> None:
        if not version.strip():
            raise ValueError("preprocessing version must not be empty")
        if state_max_entries < 1:
            raise ValueError("preprocessing state_max_entries must be >= 1")
        self.version = version
        self.apply_to_v2 = apply_to_v2
        self.state_max_entries = state_max_entries
        self.specs: dict[str, FilterSpec] = {
            name: spec if isinstance(spec, FilterSpec) else FilterSpec.from_mapping(spec)
            for name, spec in (filters or {}).items()
        }
        unknown = sorted(set(self.specs).difference(SensorValues.__dataclass_fields__))
        if unknown:
            raise ValueError(f"preprocessing filters reference unknown sensor fields: {unknown}")
        self._state: OrderedDict[tuple[str, str, str, str], FieldFilter] = OrderedDict()

    def _filter(self, gateway_id: str, node_id: str, boot_id: str, field: str) -> FieldFilter:
        key = (gateway_id, node_id, boot_id, field)
        if key in self._state:
            self._state.move_to_end(key)
            return self._state[key]
        instance = FieldFilter(self.specs.get(field, FilterSpec()))
        self._state[key] = instance
        while len(self._state) > self.state_max_entries:
            self._state.popitem(last=False)
        return instance

    def process(self, result: ValidationResult) -> ValidationResult:
        reading = result.reading
        eligible = reading.compact_version == 3 or (
            reading.compact_version == 2 and self.apply_to_v2
        )
        if not eligible:
            # v2 remains explicitly identified as already preprocessed by node.
            if reading.compact_version == 2 and reading.preprocessing_version == "unprocessed":
                reading = replace(
                    reading,
                    preprocessing_version="legacy_node_preprocessed.v2",
                    source_event_id=reading.source_event_id or reading.event_id,
                )
                return replace(result, reading=reading)
            return result

        values = reading.sensor.as_dict()
        filtered = {
            field: self._filter(
                reading.gateway_id,
                reading.node_id,
                reading.boot_id,
                field,
            ).update(value)
            for field, value in values.items()
        }
        processed = replace(
            reading,
            sensor=SensorValues(**filtered),
            preprocessing_version=self.version,
            source_event_id=reading.source_event_id or reading.event_id,
        )
        return replace(result, reading=processed)

    @property
    def state_entries(self) -> int:
        return len(self._state)
