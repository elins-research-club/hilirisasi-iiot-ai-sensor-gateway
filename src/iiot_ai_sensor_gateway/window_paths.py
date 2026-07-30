"""Canonical and legacy window-artifact path handling.

New pipeline runs write only ``windows.jsonl``. Readers retain a narrow,
two-way filename fallback so existing local artifacts and old commands keep
working during migration without creating a second copy.
"""

from __future__ import annotations

import warnings
from pathlib import Path

CANONICAL_WINDOWS_FILENAME = "windows.jsonl"
LEGACY_WINDOWS_FILENAME = "lstm_windows.jsonl"


def canonical_windows_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / CANONICAL_WINDOWS_FILENAME


def resolve_windows_path(path: str | Path) -> Path:
    """Return an existing window path, allowing only the known filename alias.

    An explicitly existing path always wins. Fallback is attempted only when the
    requested path is missing and its filename is one of the two known names.
    """

    requested = Path(path)
    if requested.exists():
        return requested
    sibling_name = {
        CANONICAL_WINDOWS_FILENAME: LEGACY_WINDOWS_FILENAME,
        LEGACY_WINDOWS_FILENAME: CANONICAL_WINDOWS_FILENAME,
    }.get(requested.name)
    if sibling_name is None:
        return requested
    sibling = requested.with_name(sibling_name)
    if sibling.exists():
        warnings.warn(
            f"window artifact {requested} is missing; using migration-compatible {sibling}",
            UserWarning,
            stacklevel=2,
        )
        return sibling
    return requested
