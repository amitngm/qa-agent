"""Flow-level assertions (scenario / end-to-end)."""

from __future__ import annotations

import time

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.layers.base import FlowAssertionsLayer
from qa_agent.validation.categories import ValidationCategory


class DefaultFlowAssertions(FlowAssertionsLayer):
    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        start = time.perf_counter()
        context.merge_metadata(
            {
                "validator": {
                    "flow_assertions": {
                        "categories_addressed": [ValidationCategory.API.value, ValidationCategory.DATA.value],
                        "checks_run": 0,
                        "checks_passed": 0,
                    }
                }
            }
        )
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="flow_assertions",
            name=self.name,
            status=StepStatus.SUCCEEDED,
            duration_ms=duration_ms,
            detail={"placeholder": True, "scope": "flow"},
        )
