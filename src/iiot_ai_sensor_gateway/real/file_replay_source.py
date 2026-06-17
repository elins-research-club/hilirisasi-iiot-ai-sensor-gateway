from __future__ import annotations

from pathlib import Path
from typing import Iterator


class FileReplaySource:
    """Replay line-delimited payloads from a local file for hardware-free tests."""

    source_name = "replay_file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def metadata(self) -> dict[str, str]:
        return {"replay_file": str(self.path)}

    def iter_lines(self) -> Iterator[str]:
        with self.path.open(encoding="utf-8") as file:
            for line in file:
                yield line.rstrip("\r\n")
