from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

def setup_logging(level: str = 'INFO') -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format='%(asctime)s %(levelname)s %(message)s')

class JsonlLogger:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, record: dict[str, Any]) -> None:
        path = self.output_dir / f'{name}.jsonl'
        with path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(record, separators=(',', ':'), default=str) + '\n')
