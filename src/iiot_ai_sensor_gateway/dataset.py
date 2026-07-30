"""Legacy JSONL utility retained for compatibility.

The active live path uses ``real.live_receiver`` and the canonical pipeline. This
module is not invoked automatically by the production receiver.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import WindowSample
from .window_paths import canonical_windows_path

class DatasetWriter:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = canonical_windows_path(self.output_dir)

    def write(self, window: WindowSample) -> None:
        with self.path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(window.as_record(), separators=(',', ':')) + '\n')
