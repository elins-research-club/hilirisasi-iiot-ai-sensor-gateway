from __future__ import annotations

import json
import os
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
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str)


def _extract_payload(decoded: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(decoded, dict):
        raise ValueError("payload line must be a JSON object")
    payload = decoded.get("payload", decoded)
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("payload field must be a JSON object or JSON object string")
    radio = decoded.get("radio") or decoded.get("lora") or payload.get("radio") or payload.get("lora") or {}
    if not isinstance(radio, dict):
        raise ValueError("radio metadata must be an object")
    return payload, radio


def _rotate_path(path: Path) -> Path:
    """Atomically rename one existing JSONL file to a unique UTC archive name."""

    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = path.with_name(f"{path.stem}.{stamp}{path.suffix}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.{stamp}.{counter}{path.suffix}")
        counter += 1
    os.replace(path, candidate)
    return candidate


def _rotate_if_needed(path: Path, max_bytes: int) -> Path | None:
    """Atomically rotate a full JSONL file before appending more records."""

    if max_bytes <= 0 or not path.exists() or path.stat().st_size < max_bytes:
        return None
    return _rotate_path(path)


class _AppendJsonlWriter:
    """Line writer with append-only semantics and bounded in-run rotation."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._file = None
        self._bytes = 0

    def __enter__(self) -> "_AppendJsonlWriter":
        _rotate_if_needed(self.path, self.max_bytes)
        self._open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _open(self) -> None:
        self._file = self.path.open("a", encoding="utf-8", buffering=1)
        self._bytes = self.path.stat().st_size if self.path.exists() else 0

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None

    def write_record(self, record: dict[str, Any]) -> None:
        line = _json_dump(record) + "\n"
        encoded_bytes = len(line.encode("utf-8"))
        if (
            self.max_bytes > 0
            and self._bytes > 0
            and self._bytes + encoded_bytes > self.max_bytes
        ):
            self.close()
            _rotate_path(self.path)
            self._open()
        if self._file is None:
            raise RuntimeError("JSONL writer is not open")
        self._file.write(line)
        self._file.flush()
        self._bytes += encoded_bytes


def _write_line(file: _AppendJsonlWriter, record: dict[str, Any]) -> None:
    file.write_record(record)


class LiveReceiver:
    """Receive payload lines and keep append-only raw/canonical audit trails."""

    def __init__(self, parser: PayloadParser, validator: ReadingValidator) -> None:
        self.parser = parser
        self.validator = validator

    def run(
        self,
        source: LineSource,
        output_dir: str | Path,
        max_messages: int = 0,
        *,
        rotate_max_bytes: int = 10 * 1024 * 1024,
    ) -> ReceiverSummary:
        if max_messages < 0:
            raise ValueError("max_messages must be >= 0")
        if rotate_max_bytes < 0:
            raise ValueError("rotate_max_bytes must be >= 0")
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        paths = {
            "accepted": output_path / "accepted_payloads.jsonl",
            "rejected": output_path / "rejected_payloads.jsonl",
            "events": output_path / "receiver_events.jsonl",
            "raw": output_path / "raw_envelopes.jsonl",
        }
        source_meta = source.metadata()
        processed = accepted = rejected = 0
        with (
            _AppendJsonlWriter(paths["accepted"], rotate_max_bytes) as accepted_file,
            _AppendJsonlWriter(paths["rejected"], rotate_max_bytes) as rejected_file,
            _AppendJsonlWriter(paths["events"], rotate_max_bytes) as events_file,
            _AppendJsonlWriter(paths["raw"], rotate_max_bytes) as raw_file,
        ):
            _write_line(
                events_file,
                {
                    "event": "receiver_started",
                    "timestamp": _utc_now(),
                    "source": source.source_name,
                    "max_messages": max_messages,
                    **source_meta,
                },
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
                    raw_file,
                )
                if line_accepted:
                    accepted += 1
                else:
                    rejected += 1
            _write_line(
                events_file,
                {
                    "event": "receiver_stopped",
                    "timestamp": _utc_now(),
                    "source": source.source_name,
                    "processed_count": processed,
                    "accepted_count": accepted,
                    "rejected_count": rejected,
                    **source_meta,
                },
            )
        return ReceiverSummary(
            source.source_name,
            str(output_path),
            processed,
            accepted,
            rejected,
            max_messages,
        )

    def _process_line(
        self,
        raw_line: str,
        receive_timestamp: str,
        source_name: str,
        source_meta: dict[str, Any],
        accepted_file: _AppendJsonlWriter,
        rejected_file: _AppendJsonlWriter,
        raw_file: _AppendJsonlWriter,
    ) -> bool:
        common = {"receive_timestamp": receive_timestamp, "source": source_name, **source_meta}
        raw_common = {**common, "raw": raw_line}
        if not raw_line.strip():
            record = {**common, "raw_line": raw_line, "category": "empty_line", "error": "empty line"}
            _write_line(rejected_file, record)
            _write_line(raw_file, {**raw_common, "parse_status": "rejected", "category": "empty_line"})
            return False
        try:
            decoded = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            record = {
                **common,
                "raw_line": raw_line,
                "category": "json_decode_error",
                "error": str(exc),
            }
            _write_line(rejected_file, record)
            _write_line(raw_file, {**raw_common, "parse_status": "rejected", "category": "json_decode_error"})
            return False
        try:
            payload, radio = _extract_payload(decoded)
            reading = self.parser.parse(
                payload,
                receive_timestamp=receive_timestamp,
                source=source_name,
                radio_meta=radio,
            )
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            record = {**common, "raw_line": raw_line, "category": "parse_error", "error": str(exc)}
            _write_line(rejected_file, record)
            _write_line(raw_file, {**raw_common, "parse_status": "rejected", "category": "parse_error"})
            return False

        result = self.validator.validate(reading)
        if not result.is_valid:
            record = {
                **common,
                "raw_line": raw_line,
                "category": "validation_error",
                "error": "payload failed validation",
                "validation_issues": list(result.issues),
                "event_id": reading.event_id,
                "node_id": reading.node_id,
                "room_id": reading.room_id,
                "boot_id": reading.boot_id,
                "sequence": reading.sequence,
            }
            _write_line(rejected_file, record)
            _write_line(
                raw_file,
                {
                    **raw_common,
                    "parse_status": "rejected",
                    "category": "validation_error",
                    "event_id": reading.event_id,
                },
            )
            return False

        accepted_record = {
            **common,
            "raw_line": raw_line,
            "payload_original_compact_json": _json_dump(payload),
            "payload": payload,
            "canonical": reading.as_record(),
            "event_id": reading.event_id,
            "node_id": reading.node_id,
            "room_id": reading.room_id,
            "boot_id": reading.boot_id,
            "sequence": reading.sequence,
            "time_quality": reading.time_quality,
            "validation_issues": list(result.issues),
        }
        _write_line(accepted_file, accepted_record)
        _write_line(
            raw_file,
            {
                **raw_common,
                "parse_status": "accepted",
                "event_id": reading.event_id,
                "radio": reading.radio.as_dict(),
            },
        )
        return True
