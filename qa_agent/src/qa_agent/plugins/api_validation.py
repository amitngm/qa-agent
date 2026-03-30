"""HTTP API contract checks — generic request/response validation (no browser)."""

from __future__ import annotations

import time
from typing import Any, List, Mapping

import httpx

from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.plugins.http_validation_shared import execute_http_case
from qa_agent.validation.api_models import ApiCaseSpec, ApiValidationCaseResult, ApiValidationSummary
from qa_agent.validation.categories import ValidationCategory


def run_api_validation(
    context: RunContext,
    plugin_config: Mapping[str, Any],
) -> StepResult:
    start = time.perf_counter()
    if not plugin_config.get("enabled", False):
        summary = ApiValidationSummary(status="skipped", checks_run=0, checks_passed=0, failed=False)
        context.merge_metadata({"validator": {"api_validation": summary.model_dump(mode="json")}})
        return StepResult(
            layer="plugins",
            name="api_validation",
            status=StepStatus.SKIPPED,
            detail={"reason": "disabled", "summary": summary.model_dump(mode="json")},
        )

    base_url = str(plugin_config.get("base_url") or "")
    default_timeout = float(plugin_config.get("default_timeout_seconds", 30.0))
    verify_tls = bool(plugin_config.get("verify_tls", True))
    raw_cases = plugin_config.get("cases") or []

    case_errors: List[str] = []
    specs: List[ApiCaseSpec] = []
    for i, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            case_errors.append(f"cases[{i}] must be an object")
            continue
        payload = {**raw, "id": raw.get("id") or f"case_{i}"}
        try:
            specs.append(ApiCaseSpec.model_validate(payload))
        except Exception as exc:  # noqa: BLE001
            case_errors.append(f"cases[{i}]: {exc}")

    if case_errors:
        summary = ApiValidationSummary(
            status="failed",
            checks_run=0,
            checks_passed=0,
            failed=True,
            errors=case_errors,
        )
        context.merge_metadata({"validator": {"api_validation": summary.model_dump(mode="json")}})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="plugins",
            name="api_validation",
            status=StepStatus.FAILED,
            duration_ms=duration_ms,
            detail={
                "failure_category": ValidationCategory.API.value,
                "summary": summary.model_dump(mode="json"),
            },
            errors=case_errors,
        )

    results: List[ApiValidationCaseResult] = []

    with httpx.Client(verify=verify_tls, follow_redirects=True) as client:
        for spec in specs:
            results.append(
                execute_http_case(
                    client,
                    base_url=base_url,
                    default_timeout=default_timeout,
                    spec=spec,
                )
            )

    checks_run = len(results)
    checks_passed = sum(1 for r in results if r.ok)
    any_failed = any(not r.ok for r in results)

    summary = ApiValidationSummary(
        status="completed",
        checks_run=checks_run,
        checks_passed=checks_passed,
        failed=any_failed,
        cases=results,
    )

    context.merge_metadata({"validator": {"api_validation": summary.model_dump(mode="json")}})

    duration_ms = (time.perf_counter() - start) * 1000
    detail: dict[str, Any] = {
        "summary": summary.model_dump(mode="json"),
        "checks_run": checks_run,
        "checks_passed": checks_passed,
    }

    if any_failed:
        detail["failure_category"] = ValidationCategory.API.value
        return StepResult(
            layer="plugins",
            name="api_validation",
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
        name="api_validation",
        status=StepStatus.SUCCEEDED,
        duration_ms=duration_ms,
        detail=detail,
    )
