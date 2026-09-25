"""Optional logging helper retained for explicit tool use.

The live receiver owns its append-only audit logs. Importing this module does
not configure global logging and it is not wired automatically into runtime.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

def setup_logging(level: str = 'INFO') -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format='%(asctime)s %(levelname)s %(message)s')

class JsonlLogger:
    def __init__(self, output_dir: str | Path, *, rotate_max_bytes: int = 0) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rotate_max_bytes = rotate_max_bytes

    def _rotate(self, path: Path) -> None:
        if self.rotate_max_bytes <= 0 or not path.exists() or path.stat().st_size < self.rotate_max_bytes:
            return
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = path.with_name(f"{path.stem}.{stamp}{path.suffix}")
        os.replace(path, target)

    def write(self, name: str, record: dict[str, Any]) -> None:
        path = self.output_dir / f'{name}.jsonl'
        self._rotate(path)
        with path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(record, separators=(',', ':'), default=str) + '\n')
            file.flush()
            os.fsync(file.fileno())
