"""Permission / access-style HTTP checks — reuses shared httpx helpers; generic policy labels only."""

from __future__ import annotations

import time
from typing import Any, List, Mapping

import httpx

from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.plugins.http_validation_shared import execute_http_case
from qa_agent.validation.categories import ValidationCategory
from qa_agent.validation.security_models import (
    SecurityCheckSpec,
    SecurityValidationCaseResult,
    SecurityValidationSummary,
    to_effective_api_spec,
    wrap_security_case_result,
)


def run_security_validation(
    context: RunContext,
    plugin_config: Mapping[str, Any],
) -> StepResult:
    start = time.perf_counter()
    if not plugin_config.get("enabled", False):
        summary = SecurityValidationSummary(status="skipped", checks_run=0, checks_passed=0, failed=False)
        context.merge_metadata({"validator": {"security_validation": summary.model_dump(mode="json")}})
        return StepResult(
            layer="plugins",
            name="security_validation",
            status=StepStatus.SKIPPED,
            detail={
                "reason": "disabled",
                "category": ValidationCategory.SECURITY.value,
                "summary": summary.model_dump(mode="json"),
            },
        )

    base_url = str(plugin_config.get("base_url") or "")
    default_timeout = float(plugin_config.get("default_timeout_seconds", 30.0))
    verify_tls = bool(plugin_config.get("verify_tls", True))
    raw_cases = plugin_config.get("cases") or []

    case_errors: List[str] = []
    specs: List[SecurityCheckSpec] = []
    for i, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            case_errors.append(f"cases[{i}] must be an object")
            continue
        payload = {**raw, "id": raw.get("id") or f"case_{i}"}
        try:
            specs.append(SecurityCheckSpec.model_validate(payload))
        except Exception as exc:  # noqa: BLE001
            case_errors.append(f"cases[{i}]: {exc}")

    if case_errors:
        summary = SecurityValidationSummary(
            status="failed",
            checks_run=0,
            checks_passed=0,
            failed=True,
            errors=case_errors,
        )
        context.merge_metadata({"validator": {"security_validation": summary.model_dump(mode="json")}})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="plugins",
            name="security_validation",
            status=StepStatus.FAILED,
            duration_ms=duration_ms,
            detail={
                "failure_category": ValidationCategory.SECURITY.value,
                "summary": summary.model_dump(mode="json"),
            },
            errors=case_errors,
        )

    results: List[SecurityValidationCaseResult] = []
    with httpx.Client(verify=verify_tls, follow_redirects=True) as client:
        for spec in specs:
            effective = to_effective_api_spec(spec)
            api_res = execute_http_case(
                client,
                base_url=base_url,
                default_timeout=default_timeout,
                spec=effective,
            )
            results.append(wrap_security_case_result(api_res, spec))

    checks_run = len(results)
    checks_passed = sum(1 for r in results if r.ok)
    any_failed = any(not r.ok for r in results)

    summary = SecurityValidationSummary(
        status="completed",
        checks_run=checks_run,
        checks_passed=checks_passed,
        failed=any_failed,
        cases=results,
    )
    context.merge_metadata({"validator": {"security_validation": summary.model_dump(mode="json")}})

    duration_ms = (time.perf_counter() - start) * 1000
    detail: dict[str, Any] = {
        "category": ValidationCategory.SECURITY.value,
        "summary": summary.model_dump(mode="json"),
        "checks_run": checks_run,
        "checks_passed": checks_passed,
    }

    if any_failed:
        detail["failure_category"] = ValidationCategory.SECURITY.value
        return StepResult(
            layer="plugins",
            name="security_validation",
            status=StepStatus.FAILED,
            duration_ms=duration_ms,
            detail=detail,
            errors=[
                f"case {r.case_id}: {r.error or '; '.join(r.validation_errors) or 'validation failed'}"
                for r in results
                if not r.ok
            ],
        )

    return StepResult(
        layer="plugins",
        name="security_validation",
        status=StepStatus.SUCCEEDED,
        duration_ms=duration_ms,
        detail=detail,
    )
