"""Map orchestrator pipeline keys to :class:`~qa_agent.store.layer_timing.MajorLayer` for timing."""

from __future__ import annotations

from typing import Optional

from qa_agent.store.layer_timing import MajorLayer

_PIPELINE_KEY_TO_MAJOR: dict[str, MajorLayer] = {
    "planner": MajorLayer.PLANNER,
    "discovery": MajorLayer.DISCOVERER,
    "execution": MajorLayer.EXECUTOR,
    "ui_automation": MajorLayer.VALIDATOR,
    "step_assertions": MajorLayer.VALIDATOR,
    "flow_assertions": MajorLayer.VALIDATOR,
    "api_validation": MajorLayer.VALIDATOR,
    "data_validation": MajorLayer.VALIDATOR,
    "security_validation": MajorLayer.VALIDATOR,
    "analysis": MajorLayer.ANALYZER,
    "reporting": MajorLayer.REPORTER,
    "report_sink": MajorLayer.REPORTER,
}


def major_layer_for_pipeline_key(pipeline_key: str) -> Optional[MajorLayer]:
    """Return the major phase for a pipeline stage key, or ``None`` if unknown."""
    return _PIPELINE_KEY_TO_MAJOR.get(pipeline_key)
