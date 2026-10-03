from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

BENCHMARK_SCHEMA = "iiot.ai_sensor.anomaly_benchmark.v1"
LABEL_SCHEMA = "iiot.ai_sensor.anomaly_labels.v1"


def _parse_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must include timezone information")
    return timestamp.astimezone(UTC)


@dataclass(frozen=True)
class EventInterval:
    event_id: str
    start: datetime
    end: datetime
    kind: str = "anomaly"

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must not be empty")
        if self.end < self.start:
            raise ValueError("event end must not precede start")

    def as_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "kind": self.kind,
        }


def intervals_from_boolean_records(
    records: Iterable[dict[str, Any]],
    *,
    flag_field: str = "is_anomaly",
    merge_gap_sec: float = 0.0,
) -> list[EventInterval]:
    """Collapse positive sample decisions into timestamp intervals.

    A single isolated positive sample becomes a zero-duration event. Consecutive
    positive samples can be merged across a bounded gap. Warm-up and rejected
    rows must be filtered by the caller through the boolean flag.
    """

    if merge_gap_sec < 0:
        raise ValueError("merge_gap_sec must be non-negative")
    positives: list[datetime] = []
    for record in records:
        if not bool(record.get(flag_field, False)):
            continue
        timestamp_value = record.get("timestamp")
        if not isinstance(timestamp_value, str):
            raise ValueError("positive detection record requires timestamp")
        positives.append(_parse_timestamp(timestamp_value))
    positives = sorted(set(positives))
    if not positives:
        return []

    intervals: list[EventInterval] = []
    started = previous = positives[0]
    for current in positives[1:]:
        if (current - previous).total_seconds() <= merge_gap_sec:
            previous = current
            continue
        intervals.append(
            EventInterval(
                event_id=f"detected-{len(intervals) + 1:04d}",
                start=started,
                end=previous,
                kind="detected_anomaly",
            )
        )
        started = previous = current
    intervals.append(
        EventInterval(
            event_id=f"detected-{len(intervals) + 1:04d}",
            start=started,
            end=previous,
            kind="detected_anomaly",
        )
    )
    return intervals


def load_label_intervals(path: str | Path) -> list[EventInterval]:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if raw.get("schema") != LABEL_SCHEMA:
        raise ValueError(f"unsupported anomaly label schema: {raw.get('schema')}")
    events = raw.get("events")
    if not isinstance(events, list):
        raise ValueError("anomaly label file must contain an events list")
    intervals: list[EventInterval] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"label event {index} must be an object")
        intervals.append(
            EventInterval(
                event_id=str(event.get("event_id", f"label-{index + 1:04d}")),
                start=_parse_timestamp(str(event["start"])),
                end=_parse_timestamp(str(event["end"])),
                kind=str(event.get("kind", "anomaly")),
            )
        )
    return sorted(intervals, key=lambda item: (item.start, item.end, item.event_id))


def _overlap_with_tolerance(
    detected: EventInterval,
    truth: EventInterval,
    tolerance: timedelta,
) -> bool:
    expanded_start = truth.start - tolerance
    expanded_end = truth.end + tolerance
    return detected.end >= expanded_start and detected.start <= expanded_end


def benchmark_events(
    detected: list[EventInterval],
    truth: list[EventInterval],
    *,
    observation_start: datetime,
    observation_end: datetime,
    match_tolerance_sec: float = 0.0,
) -> dict[str, Any]:
    """Greedy one-to-one event matching with delay and false-alert/day metrics."""

    if match_tolerance_sec < 0:
        raise ValueError("match_tolerance_sec must be non-negative")
    if observation_end < observation_start:
        raise ValueError("observation_end must not precede observation_start")
    tolerance = timedelta(seconds=match_tolerance_sec)
    unmatched_truth = set(range(len(truth)))
    matches: list[dict[str, Any]] = []
    false_positive_events: list[str] = []

    for detection in sorted(detected, key=lambda item: (item.start, item.end)):
        candidates = [
            index
            for index in unmatched_truth
            if _overlap_with_tolerance(detection, truth[index], tolerance)
        ]
        if not candidates:
            false_positive_events.append(detection.event_id)
            continue
        selected = min(
            candidates,
            key=lambda index: (
                abs((detection.start - truth[index].start).total_seconds()),
                truth[index].start,
            ),
        )
        unmatched_truth.remove(selected)
        label = truth[selected]
        delay_sec = max(0.0, (detection.start - label.start).total_seconds())
        matches.append(
            {
                "detected_event_id": detection.event_id,
                "truth_event_id": label.event_id,
                "detection_delay_sec": delay_sec,
            }
        )

    true_positives = len(matches)
    false_positives = len(false_positive_events)
    false_negatives = len(unmatched_truth)
    precision = (
        true_positives / (true_positives + false_positives)
        if true_positives + false_positives
        else None
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if true_positives + false_negatives
        else None
    )
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    observation_seconds = max(
        0.0, (observation_end - observation_start).total_seconds()
    )
    observation_days = observation_seconds / 86400.0
    false_alerts_per_day = (
        false_positives / observation_days if observation_days > 0 else None
    )
    delays = [float(item["detection_delay_sec"]) for item in matches]
    delay_summary = {
        "count": len(delays),
        "mean_sec": sum(delays) / len(delays) if delays else None,
        "max_sec": max(delays) if delays else None,
    }
    return {
        "schema": BENCHMARK_SCHEMA,
        "status": "EXPERIMENTAL_FIXTURE_OR_LABEL_DEPENDENT",
        "observation_start": observation_start.isoformat(),
        "observation_end": observation_end.isoformat(),
        "observation_days": observation_days,
        "match_tolerance_sec": match_tolerance_sec,
        "detected_event_count": len(detected),
        "truth_event_count": len(truth),
        "true_positive_events": true_positives,
        "false_positive_events": false_positives,
        "false_negative_events": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alerts_per_day": false_alerts_per_day,
        "detection_delay": delay_summary,
        "matches": matches,
        "unmatched_detected_event_ids": false_positive_events,
        "unmatched_truth_event_ids": [truth[index].event_id for index in sorted(unmatched_truth)],
    }


def _contains(interval: EventInterval, timestamp: datetime) -> bool:
    return interval.start <= timestamp <= interval.end


def _point_metrics(
    records: list[dict[str, Any]],
    truth: list[EventInterval],
) -> dict[str, Any]:
    true_positive = false_positive = false_negative = true_negative = 0
    warmup_false_positive = 0
    by_kind: dict[str, dict[str, int]] = {}
    evaluated = 0
    for record in records:
        timestamp_value = record.get("timestamp")
        if not isinstance(timestamp_value, str):
            continue
        timestamp = _parse_timestamp(timestamp_value)
        detected = bool(record.get("is_anomaly", False))
        active_truth = [interval for interval in truth if _contains(interval, timestamp)]
        expected = bool(active_truth)
        evaluated += 1
        if detected and expected:
            true_positive += 1
        elif detected and not expected:
            false_positive += 1
        elif not detected and expected:
            false_negative += 1
        else:
            true_negative += 1
        if detected and not bool(record.get("warmup_complete", True)):
            warmup_false_positive += 1
        for kind in {interval.kind for interval in active_truth}:
            item = by_kind.setdefault(kind, {"truth_points": 0, "detected_points": 0})
            item["truth_points"] += 1
            if detected:
                item["detected_points"] += 1
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else None
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else None
    )
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    return {
        "evaluated_points": evaluated,
        "true_positive_points": true_positive,
        "false_positive_points": false_positive,
        "false_negative_points": false_negative,
        "true_negative_points": true_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "warmup_false_positive_points": warmup_false_positive,
        "by_truth_kind": {
            kind: {
                **item,
                "recall": (
                    item["detected_points"] / item["truth_points"]
                    if item["truth_points"]
                    else None
                ),
            }
            for kind, item in sorted(by_kind.items())
        },
    }


def _event_kind_breakdown(
    truth: list[EventInterval],
    matches: list[dict[str, Any]],
) -> dict[str, Any]:
    matched = {str(item["truth_event_id"]): item for item in matches}
    by_kind: dict[str, list[EventInterval]] = {}
    for interval in truth:
        by_kind.setdefault(interval.kind, []).append(interval)
    output: dict[str, Any] = {}
    for kind, intervals in sorted(by_kind.items()):
        kind_matches = [matched[item.event_id] for item in intervals if item.event_id in matched]
        delays = [float(item["detection_delay_sec"]) for item in kind_matches]
        output[kind] = {
            "truth_events": len(intervals),
            "matched_events": len(kind_matches),
            "missed_events": len(intervals) - len(kind_matches),
            "recall": len(kind_matches) / len(intervals) if intervals else None,
            "mean_detection_delay_sec": sum(delays) / len(delays) if delays else None,
            "max_detection_delay_sec": max(delays) if delays else None,
        }
    return output


def benchmark_detection_file(
    detections_jsonl: str | Path,
    labels_json: str | Path,
    output_json: str | Path,
    *,
    merge_gap_sec: float = 0.0,
    match_tolerance_sec: float = 0.0,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    for line_number, line in enumerate(
        Path(detections_jsonl).read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"detection line {line_number} must be an object")
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            timestamps.append(_parse_timestamp(timestamp))
        records.append(record)
    if not timestamps:
        raise ValueError("detection file contains no timestamped records")
    detected = intervals_from_boolean_records(
        records,
        flag_field="is_anomaly",
        merge_gap_sec=merge_gap_sec,
    )
    truth = load_label_intervals(labels_json)
    result = benchmark_events(
        detected,
        truth,
        observation_start=min(timestamps),
        observation_end=max(timestamps),
        match_tolerance_sec=match_tolerance_sec,
    )
    result.update(
        {
            "detections_ref": str(detections_jsonl),
            "labels_ref": str(labels_json),
            "merge_gap_sec": merge_gap_sec,
            "point_metrics": _point_metrics(records, truth),
            "event_by_truth_kind": _event_kind_breakdown(truth, result["matches"]),
        }
    )
    output = Path(output_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def inject_labeled_normalized_fixture(
    output_jsonl: str | Path,
    labels_json: str | Path,
    *,
    sample_count: int = 360,
    interval_sec: int = 60,
    event_start_indices: tuple[int, ...] = (120, 260),
    event_length: int = 12,
) -> dict[str, Any]:
    """Create a deterministic normalized fixture for harness regression only.

    This is not a sensor-accuracy dataset. The labels describe injected feature
    shifts and may be used to verify metric plumbing and detector behavior.
    """

    if sample_count < 2 or interval_sec < 1 or event_length < 1:
        raise ValueError("sample_count, interval_sec, and event_length must be positive")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    labels: list[EventInterval] = []
    active_indices: set[int] = set()
    for event_number, event_start in enumerate(event_start_indices, start=1):
        if event_start < 0 or event_start + event_length > sample_count:
            raise ValueError("injected event exceeds fixture bounds")
        active_indices.update(range(event_start, event_start + event_length))
        labels.append(
            EventInterval(
                event_id=f"injected-{event_number:03d}",
                start=start + timedelta(seconds=event_start * interval_sec),
                end=start
                + timedelta(seconds=(event_start + event_length - 1) * interval_sec),
                kind="injected_feature_shift",
            )
        )
    rows: list[str] = []
    for index in range(sample_count):
        seasonal = ((index % 60) - 30) / 3000.0
        event_offset = 0.32 if index in active_indices else 0.0
        features = {
            "temperature_c": min(1.0, max(0.0, 0.42 + seasonal + event_offset)),
            "pm25_ug_m3": min(1.0, max(0.0, 0.18 - seasonal + event_offset * 1.4)),
        }
        rows.append(
            json.dumps(
                {
                    "timestamp": (start + timedelta(seconds=index * interval_sec)).isoformat(),
                    "features": features,
                    "fixture_truth": index in active_indices,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
    output_path = Path(output_jsonl)
    labels_path = Path(labels_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(rows), encoding="utf-8")
    labels_path.write_text(
        json.dumps(
            {
                "schema": LABEL_SCHEMA,
                "source": "deterministic_injected_normalized_fixture",
                "production_accuracy_claim": None,
                "events": [event.as_dict() for event in labels],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "fixture": str(output_path),
        "labels": str(labels_path),
        "sample_count": sample_count,
        "event_count": len(labels),
        "status": "SYNTHETIC_HARNESS_ONLY",
    }
