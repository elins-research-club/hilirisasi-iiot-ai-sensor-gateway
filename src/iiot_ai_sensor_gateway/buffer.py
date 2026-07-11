from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from .contracts import SensorReading, ValidationResult

NodeIdentity = tuple[str, str, str]


class NodeBuffer:
    def __init__(self, max_size: int) -> None:
        if max_size < 1:
            raise ValueError("buffer max_size must be >= 1")
        self._items: dict[NodeIdentity, deque[ValidationResult]] = defaultdict(
            lambda: deque(maxlen=max_size)
        )

    @staticmethod
    def identity(reading: SensorReading) -> NodeIdentity:
        return (reading.gateway_id, reading.node_id, reading.room_id)

    def add(self, result: ValidationResult) -> None:
        self._items[self.identity(result.reading)].append(result)

    def identity_items(self, identity: NodeIdentity) -> list[ValidationResult]:
        return sorted(self._items[identity], key=lambda item: item.reading.timestamp)

    def items_for(self, reading: SensorReading) -> list[ValidationResult]:
        return self.identity_items(self.identity(reading))

    def node_items(self, node_id: str) -> list[ValidationResult]:
        """Compatibility helper aggregating identities with the requested node ID.

        Runtime pipeline should use `items_for()`/`identity_items()` to avoid
        cross-gateway or cross-room contamination.
        """

        items = [
            item
            for identity, group in self._items.items()
            if identity[1] == node_id
            for item in group
        ]
        return sorted(items, key=lambda item: item.reading.timestamp)

    def identities(self) -> Iterable[NodeIdentity]:
        return self._items.keys()

    def nodes(self) -> Iterable[str]:
        return {identity[1] for identity in self._items}
