from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from .contracts import ValidationResult

class NodeBuffer:
    def __init__(self, max_size: int) -> None:
        self._items: dict[str, deque[ValidationResult]] = defaultdict(lambda: deque(maxlen=max_size))

    def add(self, result: ValidationResult) -> None:
        self._items[result.reading.node_id].append(result)

    def node_items(self, node_id: str) -> list[ValidationResult]:
        return sorted(self._items[node_id], key=lambda item: item.reading.timestamp)

    def nodes(self) -> Iterable[str]:
        return self._items.keys()
