"""Legacy JSONL utility retained for compatibility.

The active live path uses ``real.live_receiver`` and the canonical pipeline. This
module is not invoked automatically by the production receiver.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import WindowSample

class DatasetWriter:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / 'lstm_windows.jsonl'

    def write(self, window: WindowSample) -> None:
        with self.path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(window.as_record(), separators=(',', ':')) + '\n')
