from __future__ import annotations

from datetime import datetime

from .contracts import ResampledPoint, SensorValues, ValidationResult


def _bucket_key(ts: datetime, interval_sec: int) -> int:
    if interval_sec < 1:
        raise ValueError("resample interval_sec must be >= 1")
    return int(ts.timestamp()) // interval_sec * interval_sec


def _avg(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def resample(results: list[ValidationResult], interval_sec: int) -> list[ResampledPoint]:
    """Resample independently per gateway/node/room and time bucket.

    Identity is part of the key so a batch containing multiple nodes can never
    average their observations together. Source event IDs and preprocessing
    version are retained for deterministic audit/replay.
    """

    buckets: dict[tuple[str, str, str, int], list[ValidationResult]] = {}
    for result in results:
        reading = result.reading
        key = (
            reading.gateway_id,
            reading.node_id,
            reading.room_id,
            _bucket_key(reading.timestamp, interval_sec),
        )
        buckets.setdefault(key, []).append(result)

    points: list[ResampledPoint] = []
    for identity_key in sorted(buckets, key=lambda item: (item[0], item[1], item[3])):
        gateway_id, node_id, room_id, bucket = identity_key
        group = sorted(
            buckets[identity_key],
            key=lambda item: (item.reading.timestamp, item.reading.sequence),
        )
        sensors = [item.reading.sensor for item in group]
        sensor = SensorValues(
            **{
                name: _avg([getattr(item, name) for item in sensors])
                for name in SensorValues.__dataclass_fields__
            }
        )
        missing_count = sum(
            1 for item in group for issue in item.issues if issue.startswith("missing_")
        )
        seq_gap_count = sum(1 for item in group if "sequence_gap" in item.issues)
        valid_count = sum(1 for item in group if item.is_valid)
        versions = {item.reading.preprocessing_version for item in group}
        preprocessing_version = (
            next(iter(versions)) if len(versions) == 1 else "mixed_preprocessing_rejected"
        )
        if len(versions) != 1:
            raise ValueError(
                f"mixed preprocessing versions in one bucket for {node_id}: {sorted(versions)}"
            )
        points.append(
            ResampledPoint(
                gateway_id=gateway_id,
                node_id=node_id,
                room_id=room_id,
                timestamp=datetime.fromtimestamp(
                    bucket,
                    tz=group[0].reading.timestamp.tzinfo,
                ),
                sensor=sensor,
                valid_ratio=valid_count / len(group),
                missing_count=missing_count,
                seq_gap_count=seq_gap_count,
                source_event_ids=tuple(item.reading.event_id for item in group),
                preprocessing_version=preprocessing_version,
            )
        )
    return points
