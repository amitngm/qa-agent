"""Step-level assertions (per-invocation, fine-grained)."""

from __future__ import annotations

import time

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.layers.base import StepAssertionsLayer
from qa_agent.validation.categories import ValidationCategory


class DefaultStepAssertions(StepAssertionsLayer):
    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        start = time.perf_counter()
        context.merge_metadata(
            {
                "validator": {
                    "step_assertions": {
                        "categories_addressed": [
                            ValidationCategory.UI.value,
                            ValidationCategory.UX.value,
                            ValidationCategory.STATE.value,
                        ],
                        "checks_run": 0,
                        "checks_passed": 0,
                    }
                }
            }
        )
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="step_assertions",
            name=self.name,
            status=StepStatus.SUCCEEDED,
            duration_ms=duration_ms,
            detail={"placeholder": True, "scope": "step"},
        )
