from __future__ import annotations

from collections import defaultdict, deque

from .contracts import FeatureVector, WindowSample
from .features import FEATURE_NAMES

WindowIdentity = tuple[str, str, str]


class WindowBuilder:
    def __init__(self, window_size: int, step: int = 1) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        self.window_size = window_size
        self.step = max(1, step)
        self.buffers: dict[WindowIdentity, deque[FeatureVector]] = defaultdict(
            lambda: deque(maxlen=window_size)
        )
        self.counts: dict[WindowIdentity, int] = defaultdict(int)

    @staticmethod
    def identity(vector: FeatureVector) -> WindowIdentity:
        return (vector.gateway_id, vector.node_id, vector.room_id)

    def add(self, vector: FeatureVector) -> WindowSample | None:
        identity = self.identity(vector)
        self.buffers[identity].append(vector)
        self.counts[identity] += 1
        if len(self.buffers[identity]) < self.window_size:
            return None
        if (self.counts[identity] - self.window_size) % self.step:
            return None
        items = list(self.buffers[identity])
        x = [[item.values[name] for name in FEATURE_NAMES] for item in items]
        return WindowSample(
            vector.gateway_id,
            vector.node_id,
            vector.room_id,
            items[0].timestamp,
            items[-1].timestamp,
            FEATURE_NAMES,
            x,
        )
