"""Serialize QaReport to JSON."""

from __future__ import annotations

from typing import Optional

from qa_agent.reporting.schema import QaReport


def report_to_json(report: QaReport, *, indent: Optional[int] = 2) -> str:
    """Return a UTF-8 JSON string (timestamps in ISO 8601)."""
    return report.model_dump_json(indent=indent)
