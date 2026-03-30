"""Placeholder for pushing reports to external sinks (S3, webhooks, etc.)."""

from __future__ import annotations

import time
from typing import Any, Mapping

from qa_agent.core.types import RunContext, StepResult, StepStatus


def emit_report_sink(
    context: RunContext,
    plugin_config: Mapping[str, Any],
) -> StepResult:
    start = time.perf_counter()
    if not plugin_config.get("enabled", False):
        return StepResult(
            layer="plugins",
            name="report_sink",
            status=StepStatus.SKIPPED,
            detail={"reason": "disabled"},
        )
    context.merge_metadata(
        {
            "reporter": {
                "report_sink": {"status": "placeholder", "format": plugin_config.get("format")}
            }
        }
    )
    duration_ms = (time.perf_counter() - start) * 1000
    return StepResult(
        layer="plugins",
        name="report_sink",
        status=StepStatus.SUCCEEDED,
        duration_ms=duration_ms,
        detail={"placeholder": True},
    )
