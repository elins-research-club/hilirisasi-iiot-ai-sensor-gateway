from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..parser import PayloadParser
from ..validation import ReadingValidator


class LineSource(Protocol):
    source_name: str

    def metadata(self) -> dict[str, Any]: ...

    def iter_lines(self): ...


@dataclass(frozen=True)
class ReceiverSummary:
    source: str
    output_dir: str
    processed_count: int
    accepted_count: int
    rejected_count: int
    max_messages: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _json_dump(record: dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def _extract_payload(decoded: Any) -> dict[str, Any]:
    if not isinstance(decoded, dict):
        raise ValueError("payload line must be a JSON object")
    payload = decoded.get("payload", decoded)
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("payload field must be a JSON object or JSON object string")
    return payload


class LiveReceiver:
    """Receive real-like payload lines, validate them, and write audit JSONL logs."""

    def __init__(self, parser: PayloadParser, validator: ReadingValidator) -> None:
        self.parser = parser
        self.validator = validator

    def run(self, source: LineSource, output_dir: str | Path, max_messages: int = 0) -> ReceiverSummary:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        accepted_path = output_path / "accepted_payloads.jsonl"
        rejected_path = output_path / "rejected_payloads.jsonl"
        events_path = output_path / "receiver_events.jsonl"

        source_meta = source.metadata()
        processed = accepted = rejected = 0
        with accepted_path.open("w", encoding="utf-8") as accepted_file, rejected_path.open(
            "w", encoding="utf-8"
        ) as rejected_file, events_path.open("w", encoding="utf-8") as events_file:
            events_file.write(
                _json_dump(
                    {
                        "event": "receiver_started",
                        "timestamp": _utc_now(),
                        "source": source.source_name,
                        "max_messages": max_messages,
                        **source_meta,
                    }
                )
                + "\n"
            )
            for raw_line in source.iter_lines():
                if max_messages and processed >= max_messages:
                    break
                processed += 1
                receive_timestamp = _utc_now()
                line_accepted = self._process_line(
                    raw_line,
                    receive_timestamp,
                    source.source_name,
                    source_meta,
                    accepted_file,
                    rejected_file,
                )
                if line_accepted:
                    accepted += 1
                else:
                    rejected += 1
            events_file.write(
                _json_dump(
                    {
                        "event": "receiver_stopped",
                        "timestamp": _utc_now(),
                        "source": source.source_name,
                        "processed_count": processed,
                        "accepted_count": accepted,
                        "rejected_count": rejected,
                        **source_meta,
                    }
                )
                + "\n"
            )
        return ReceiverSummary(source.source_name, str(output_path), processed, accepted, rejected, max_messages)

    def _process_line(
        self,
        raw_line: str,
        receive_timestamp: str,
        source_name: str,
        source_meta: dict[str, Any],
        accepted_file,
        rejected_file,
    ) -> bool:
        common = {"receive_timestamp": receive_timestamp, "source": source_name, **source_meta}
        if not raw_line.strip():
            rejected_file.write(
                _json_dump({**common, "raw_line": raw_line, "category": "empty_line", "error": "empty line"})
                + "\n"
            )
            return False
        try:
            decoded = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            rejected_file.write(
                _json_dump(
                    {
                        **common,
                        "raw_line": raw_line,
                        "category": "json_decode_error",
                        "error": str(exc),
                    }
                )
                + "\n"
            )
            return False
        try:
            payload = _extract_payload(decoded)
            reading = self.parser.parse(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            rejected_file.write(
                _json_dump({**common, "raw_line": raw_line, "category": "parse_error", "error": str(exc)})
                + "\n"
            )
            return False
        result = self.validator.validate(reading)
        if not result.is_valid:
            rejected_file.write(
                _json_dump(
                    {
                        **common,
                        "raw_line": raw_line,
                        "category": "validation_error",
                        "error": "payload failed validation",
                        "validation_issues": list(result.issues),
                        "node_id": reading.node_id,
                        "room_id": reading.room_id,
                        "sequence": reading.sequence,
                    }
                )
                + "\n"
            )
            return False
        accepted_file.write(
            _json_dump(
                {
                    **common,
                    "raw_line": raw_line,
                    "payload_original_compact_json": _json_dump(payload),
                    "payload": payload,
                    "node_id": reading.node_id,
                    "room_id": reading.room_id,
                    "sequence": reading.sequence,
                    "validation_issues": list(result.issues),
                }
            )
            + "\n"
        )
        return True
