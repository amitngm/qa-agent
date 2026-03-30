"""Streaming helpers for JSONL files (e.g. ``steps.jsonl``) — avoid loading entire files into memory."""

from __future__ import annotations

from pathlib import Path


def count_nonempty_lines(path: Path) -> int:
    """Count non-blank lines by streaming; returns 0 if the path is missing or unreadable."""
    if not path.is_file():
        return 0
    n = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    n += 1
    except OSError:
        return 0
    return n
