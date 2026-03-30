"""UI automation plugin — drives :class:`~qa_agent.platform.driver.PlatformDriver` from config steps."""

from __future__ import annotations

import time
from typing import Any, List, Mapping, MutableMapping

from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.platform.driver import NavigateTarget
from qa_agent.platform.playwright_driver import PlaywrightPlatformDriver
from qa_agent.platform.types import DriverResult
from qa_agent.platform.ui_models import UiAutomationSummary, UiStepResult
from qa_agent.validation.categories import ValidationCategory


def _driver_result_to_step_result(step_index: int, op: str, dr: DriverResult) -> UiStepResult:
    return UiStepResult(
        step_index=step_index,
        op=op,
        ok=dr.ok,
        detail=dict(dr.detail),
        errors=list(dr.errors),
    )


def _run_step(
    driver: PlaywrightPlatformDriver,
    step: Mapping[str, Any],
) -> tuple[str, DriverResult]:
    op = (step.get("op") or step.get("operation") or "").lower()
    params: MutableMapping[str, Any] = {k: v for k, v in dict(step).items() if k not in ("op", "operation")}

    if op == "navigate":
        target: NavigateTarget = params.get("url") or params.get("goto") or params
        if isinstance(target, dict) and not target.get("url") and not target.get("goto"):
            target = params
        return op, driver.navigate(target)

    if op == "interact":
        return op, driver.interact(dict(params))

    if op == "read":
        return op, driver.read(dict(params))

    if op == "wait":
        return op, driver.wait(dict(params))

    return op, DriverResult(
        ok=False,
        errors=(f"unknown op {op!r}; expected navigate, interact, read, wait",),
        detail={"step_keys": list(step.keys())},
    )


def run_ui_automation(
    context: RunContext,
    plugin_config: Mapping[str, Any],
) -> StepResult:
    start = time.perf_counter()
    if not plugin_config.get("enabled", False):
        summary = UiAutomationSummary(status="skipped", steps_run=0, steps_passed=0, failed=False)
        context.merge_metadata({"executor": {"ui_automation": summary.model_dump(mode="json")}})
        return StepResult(
            layer="plugins",
            name="ui_automation",
            status=StepStatus.SKIPPED,
            detail={"reason": "disabled", "summary": summary.model_dump(mode="json")},
        )

    browser = str(plugin_config.get("browser") or "chromium")
    headless = bool(plugin_config.get("headless", True))
    raw_steps = plugin_config.get("steps") or []

    case_errors: List[str] = []
    steps: List[Mapping[str, Any]] = []
    if not isinstance(raw_steps, list):
        case_errors.append("plugins.ui_automation.steps must be a list")
    else:
        for i, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                case_errors.append(f"steps[{i}] must be an object")
                continue
            if not (raw.get("op") or raw.get("operation")):
                case_errors.append(f"steps[{i}] must include op")
                continue
            steps.append(raw)

    if case_errors:
        summary = UiAutomationSummary(
            status="failed",
            driver="playwright",
            browser=browser,
            failed=True,
            errors=case_errors,
        )
        context.merge_metadata({"executor": {"ui_automation": summary.model_dump(mode="json")}})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="plugins",
            name="ui_automation",
            status=StepStatus.FAILED,
            duration_ms=duration_ms,
            detail={
                "failure_category": ValidationCategory.UI.value,
                "summary": summary.model_dump(mode="json"),
            },
            errors=case_errors,
        )

    if not steps:
        summary = UiAutomationSummary(
            status="completed",
            driver="playwright",
            browser=browser,
            steps_run=0,
            steps_passed=0,
            failed=False,
            steps=[],
        )
        context.merge_metadata({"executor": {"ui_automation": summary.model_dump(mode="json")}})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="plugins",
            name="ui_automation",
            status=StepStatus.SUCCEEDED,
            duration_ms=duration_ms,
            detail={"summary": summary.model_dump(mode="json"), "steps_run": 0},
        )

    driver = PlaywrightPlatformDriver(browser=browser, headless=headless)
    step_results: List[UiStepResult] = []

    try:
        driver.start()
        for i, step in enumerate(steps):
            op, dr = _run_step(driver, step)
            step_results.append(_driver_result_to_step_result(i, op, dr))
    except Exception as exc:  # noqa: BLE001 — plugin boundary
        summary = UiAutomationSummary(
            status="failed",
            driver="playwright",
            browser=browser,
            failed=True,
            steps=step_results,
            errors=[str(exc)],
        )
        context.merge_metadata({"executor": {"ui_automation": summary.model_dump(mode="json")}})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="plugins",
            name="ui_automation",
            status=StepStatus.FAILED,
            duration_ms=duration_ms,
            detail={
                "failure_category": ValidationCategory.UI.value,
                "summary": summary.model_dump(mode="json"),
            },
            errors=[str(exc)],
        )
    finally:
        try:
            driver.close()
        except Exception:
            pass

    steps_run = len(step_results)
    steps_passed = sum(1 for s in step_results if s.ok)
    any_failed = any(not s.ok for s in step_results)

    summary = UiAutomationSummary(
        status="completed",
        driver="playwright",
        browser=browser,
        steps_run=steps_run,
        steps_passed=steps_passed,
        failed=any_failed,
        steps=step_results,
    )
    context.merge_metadata({"executor": {"ui_automation": summary.model_dump(mode="json")}})

    duration_ms = (time.perf_counter() - start) * 1000
    detail: dict[str, Any] = {
        "summary": summary.model_dump(mode="json"),
        "steps_run": steps_run,
        "steps_passed": steps_passed,
    }

    if any_failed:
        detail["failure_category"] = ValidationCategory.UI.value
        return StepResult(
            layer="plugins",
            name="ui_automation",
            status=StepStatus.FAILED,
            duration_ms=duration_ms,
            detail=detail,
            errors=[f"step {s.step_index} ({s.op}): {'; '.join(s.errors)}" for s in step_results if not s.ok],
        )

    return StepResult(
        layer="plugins",
        name="ui_automation",
        status=StepStatus.SUCCEEDED,
        duration_ms=duration_ms,
        detail=detail,
    )
