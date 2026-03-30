"""Deprecated single validation entrypoint — use step_assertions + flow_assertions."""

from __future__ import annotations

# Re-export defaults for callers that still import DefaultValidation
from qa_agent.layers.flow_assertions import DefaultFlowAssertions
from qa_agent.layers.step_assertions import DefaultStepAssertions

DefaultValidation = DefaultStepAssertions

__all__ = ["DefaultFlowAssertions", "DefaultStepAssertions", "DefaultValidation"]
