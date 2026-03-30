"""Execution layer — drive actions (API calls, UI steps, jobs)."""

from __future__ import annotations

import time

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.layers.base import ExecutionLayer


class DefaultExecution(ExecutionLayer):
    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        start = time.perf_counter()
        context.merge_metadata({"executor": {"execution": {"actions_completed": 0}}})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="execution",
            name=self.name,
            status=StepStatus.SUCCEEDED,
            duration_ms=duration_ms,
            detail={"placeholder": True},
        )
