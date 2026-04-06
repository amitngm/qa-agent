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
        meta = context.metadata_as_dict()
        ae = (meta.get("executor") or {}).get("auto_explore_ui") or {}

        login_ok = ae.get("login_ok")       # True | False | None
        login_detail = ae.get("login_detail") or ""
        login_strategy = ae.get("login_strategy") or "unknown"

        checks_run = 0
        checks_passed = 0
        assertion_errors: list[str] = []
        assertions: list[dict] = []

        if login_ok is not None:
            checks_run += 1
            if login_ok:
                checks_passed += 1
                assertions.append({"check": "login", "passed": True, "detail": login_detail or "Login succeeded"})
            else:
                msg = f"Login failed (strategy={login_strategy}): {login_detail}" if login_detail else f"Login failed (strategy={login_strategy})"
                assertion_errors.append(msg)
                assertions.append({"check": "login", "passed": False, "detail": msg})

        context.merge_metadata(
            {
                "validator": {
                    "step_assertions": {
                        "categories_addressed": [
                            ValidationCategory.UI.value,
                            ValidationCategory.UX.value,
                            ValidationCategory.STATE.value,
                        ],
                        "checks_run": checks_run,
                        "checks_passed": checks_passed,
                        "assertions": assertions,
                    }
                }
            }
        )
        duration_ms = (time.perf_counter() - start) * 1000
        status = StepStatus.FAILED if assertion_errors else StepStatus.SUCCEEDED
        detail: dict = {
            "checks_run": checks_run,
            "checks_passed": checks_passed,
            "scope": "step",
        }
        if assertion_errors:
            detail["failure_category"] = ValidationCategory.UI.value
        return StepResult(
            layer="step_assertions",
            name=self.name,
            status=status,
            duration_ms=duration_ms,
            errors=assertion_errors,
            detail=detail,
        )
