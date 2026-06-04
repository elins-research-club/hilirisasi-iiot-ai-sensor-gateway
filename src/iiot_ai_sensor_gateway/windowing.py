from __future__ import annotations

from collections import defaultdict, deque

from .contracts import FeatureVector, WindowSample
from .features import FEATURE_NAMES

class WindowBuilder:
    def __init__(self, window_size: int, step: int = 1) -> None:
        self.window_size = window_size
        self.step = max(1, step)
        self.buffers: dict[str, deque[FeatureVector]] = defaultdict(lambda: deque(maxlen=window_size))
        self.counts: dict[str, int] = defaultdict(int)

    def add(self, vector: FeatureVector) -> WindowSample | None:
        node_id = vector.node_id
        self.buffers[node_id].append(vector)
        self.counts[node_id] += 1
        if len(self.buffers[node_id]) < self.window_size:
            return None
        if (self.counts[node_id] - self.window_size) % self.step:
            return None
        items = list(self.buffers[node_id])
        x = [[item.values[name] for name in FEATURE_NAMES] for item in items]
        return WindowSample(vector.gateway_id, node_id, vector.room_id, items[0].timestamp, items[-1].timestamp, FEATURE_NAMES, x)
