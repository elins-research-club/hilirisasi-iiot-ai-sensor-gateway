from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median

FILTER_KINDS = {"none", "ema", "median", "moving_average"}


@dataclass(frozen=True)
class FilterSpec:
    """Configuration for one sensor field filter.

    `moving_average` exists primarily for v2 shadow/parity studies. Production
    configuration should be selected per sensor from real hardware evidence.
    """

    kind: str = "none"
    alpha: float = 0.25
    window: int = 3

    def __post_init__(self) -> None:
        if self.kind not in FILTER_KINDS:
            raise ValueError(f"unsupported filter kind: {self.kind}")
        if not 0.0 < self.alpha <= 1.0:
            raise ValueError("filter alpha must be in (0, 1]")
        if self.window < 1:
            raise ValueError("filter window must be >= 1")

    @classmethod
    def from_mapping(cls, value: object) -> "FilterSpec":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("filter config must be a table/object")
        return cls(
            kind=str(value.get("kind", "none")),
            alpha=float(value.get("alpha", 0.25)),
            window=int(value.get("window", 3)),
        )


class FieldFilter:
    def __init__(self, spec: FilterSpec) -> None:
        self.spec = spec
        self._history: deque[float] = deque(maxlen=spec.window)
        self._ema: float | None = None

    def update(self, value: float | None) -> float | None:
        if value is None:
            return None
        number = float(value)
        if self.spec.kind == "none":
            return number
        if self.spec.kind == "ema":
            self._ema = number if self._ema is None else (
                self.spec.alpha * number + (1.0 - self.spec.alpha) * self._ema
            )
            return self._ema
        self._history.append(number)
        if self.spec.kind == "median":
            return float(median(self._history))
        if self.spec.kind == "moving_average":
            return float(sum(self._history) / len(self._history))
        raise AssertionError(f"unreachable filter kind: {self.spec.kind}")
