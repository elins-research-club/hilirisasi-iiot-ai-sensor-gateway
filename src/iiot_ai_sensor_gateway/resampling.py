from __future__ import annotations

from datetime import datetime

from .contracts import ResampledPoint, SensorValues, ValidationResult

def _bucket_key(ts: datetime, interval_sec: int) -> int:
    return int(ts.timestamp()) // interval_sec * interval_sec

def _avg(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None

def resample(results: list[ValidationResult], interval_sec: int) -> list[ResampledPoint]:
    buckets: dict[int, list[ValidationResult]] = {}
    for result in results:
        buckets.setdefault(_bucket_key(result.reading.timestamp, interval_sec), []).append(result)
    points: list[ResampledPoint] = []
    for key in sorted(buckets):
        group = buckets[key]
        first = group[0].reading
        sensors = [item.reading.sensor for item in group]
        sensor = SensorValues(**{name: _avg([getattr(item, name) for item in sensors]) for name in SensorValues.__dataclass_fields__})
        missing_count = sum(1 for item in group for issue in item.issues if issue.startswith('missing_'))
        seq_gap_count = sum(1 for item in group if 'sequence_gap' in item.issues)
        valid_count = sum(1 for item in group if item.is_valid)
        points.append(ResampledPoint(first.gateway_id, first.node_id, first.room_id, datetime.fromtimestamp(key, tz=first.timestamp.tzinfo), sensor, valid_count / len(group), missing_count, seq_gap_count))
    return points
