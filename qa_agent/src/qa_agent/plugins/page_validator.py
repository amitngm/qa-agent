"""Page validation plugin — applies rule-based checks to every page visited by auto_explore_ui.

Reads ``executor.auto_explore_ui.visited`` (list of PageExploreResult) from RunContext metadata
and produces a ``PageValidationSummary`` stored under ``validator.page_validation``.

For Known Flow runs (no auto_explore_ui data), the stage is skipped automatically.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Mapping, Optional

from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.platform.auto_explore_models import PageExploreResult
from qa_agent.validation.page_models import (
    FeatureValidationGroup,
    PageCheckResult,
    PageValidationResult,
    PageValidationSummary,
)

# Regex patterns that signal a JS crash in console errors
_JS_CRASH_RE = re.compile(
    r"(TypeError|ReferenceError|Uncaught|SyntaxError|RangeError|Cannot read)",
    re.IGNORECASE,
)

# Patterns that indicate real network failures worth flagging
_NET_FAIL_RE = re.compile(r"\b(4\d\d|5\d\d)\b")

_ALL_RULES = [
    "page_load",
    "console_errors",
    "js_crash",
    "network_failures",
    "blank_page",
]


def _rule_page_load(page: PageExploreResult) -> PageCheckResult:
    if not page.ok:
        return PageCheckResult(
            rule="page_load",
            passed=False,
            severity="fail",
            detail="Page did not load successfully",
        )
    return PageCheckResult(rule="page_load", passed=True, severity="fail", detail="")


def _rule_console_errors(page: PageExploreResult) -> PageCheckResult:
    errs = [e for e in (page.console_errors or []) if e]
    if errs:
        sample = errs[0][:120]
        return PageCheckResult(
            rule="console_errors",
            passed=False,
            severity="fail",
            detail=f"{len(errs)} console error(s); first: {sample}",
        )
    return PageCheckResult(rule="console_errors", passed=True, severity="fail", detail="")


def _rule_js_crash(page: PageExploreResult) -> Optional[PageCheckResult]:
    crashes = [e for e in (page.console_errors or []) if _JS_CRASH_RE.search(e)]
    if crashes:
        sample = crashes[0][:120]
        return PageCheckResult(
            rule="js_crash",
            passed=False,
            severity="fail",
            detail=f"JS crash detected: {sample}",
        )
    return PageCheckResult(rule="js_crash", passed=True, severity="fail", detail="")


def _rule_network_failures(page: PageExploreResult) -> PageCheckResult:
    fails = [f for f in (page.network_failures or []) if f]
    if fails:
        sample = fails[0][:120]
        return PageCheckResult(
            rule="network_failures",
            passed=False,
            severity="fail",
            detail=f"{len(fails)} network failure(s); first: {sample}",
        )
    return PageCheckResult(rule="network_failures", passed=True, severity="fail", detail="")


def _rule_blank_page(page: PageExploreResult) -> PageCheckResult:
    if not (page.heading or "").strip() and not (page.title or "").strip():
        return PageCheckResult(
            rule="blank_page",
            passed=False,
            severity="warn",
            detail="Page rendered with no heading and no title",
        )
    return PageCheckResult(rule="blank_page", passed=True, severity="warn", detail="")


def _validate_page(page: PageExploreResult) -> PageValidationResult:
    checks: List[PageCheckResult] = [
        _rule_page_load(page),
        _rule_console_errors(page),
        _rule_js_crash(page),
        _rule_network_failures(page),
        _rule_blank_page(page),
    ]

    hard_fails = [c for c in checks if not c.passed and c.severity == "fail"]
    warnings = [c for c in checks if not c.passed and c.severity == "warn"]

    errors_text = [c.detail for c in hard_fails if c.detail]
    warnings_text = [c.detail for c in warnings if c.detail]

    primary_feature = ""
    all_features = list(page.matched_features or [])
    if all_features:
        primary_feature = all_features[0]

    return PageValidationResult(
        url=page.url,
        title=page.title or "",
        feature=primary_feature,
        all_features=all_features,
        passed=len(hard_fails) == 0,
        has_warnings=len(warnings) > 0,
        checks=checks,
        errors=errors_text,
        warnings=warnings_text,
    )


def _group_by_feature(results: List[PageValidationResult]) -> tuple[
    List[FeatureValidationGroup], List[PageValidationResult]
]:
    feature_map: Dict[str, List[PageValidationResult]] = {}
    untagged: List[PageValidationResult] = []

    for r in results:
        if r.feature:
            feature_map.setdefault(r.feature, []).append(r)
        else:
            untagged.append(r)

    groups: List[FeatureValidationGroup] = []
    for feature, pages in sorted(feature_map.items()):
        groups.append(
            FeatureValidationGroup(
                feature=feature,
                pages_total=len(pages),
                pages_passed=sum(1 for p in pages if p.passed and not p.has_warnings),
                pages_failed=sum(1 for p in pages if not p.passed),
                pages_warned=sum(1 for p in pages if p.passed and p.has_warnings),
                pages=pages,
            )
        )
    return groups, untagged


def _extract_visited_pages(context: RunContext) -> Optional[List[Mapping[str, Any]]]:
    """Pull PageExploreResult dicts from metadata.executor.auto_explore_ui.visited."""
    meta = context.metadata_as_dict()
    ex = meta.get("executor") or {}
    ae = ex.get("auto_explore_ui")
    if not isinstance(ae, dict):
        return None
    visited = ae.get("visited")
    if not isinstance(visited, list) or not visited:
        return None
    return visited


def run_page_validation(
    context: RunContext,
    plugin_config: Mapping[str, Any],
) -> StepResult:
    start = time.perf_counter()

    if not plugin_config.get("enabled", True):
        summary = PageValidationSummary(
            status="skipped",
            failed=False,
            skip_reason="disabled via config",
        )
        context.merge_metadata({"validator": {"page_validation": summary.model_dump(mode="json")}})
        return StepResult(
            layer="page_validator",
            name="page_validator",
            status=StepStatus.SKIPPED,
            detail={"reason": "disabled"},
        )

    raw_pages = _extract_visited_pages(context)
    if raw_pages is None:
        summary = PageValidationSummary(
            status="skipped",
            failed=False,
            skip_reason="no auto_explore_ui visited pages (known_flow run or explore not yet completed)",
        )
        context.merge_metadata({"validator": {"page_validation": summary.model_dump(mode="json")}})
        return StepResult(
            layer="page_validator",
            name="page_validator",
            status=StepStatus.SKIPPED,
            detail={"reason": "no_explore_data"},
        )

    results: List[PageValidationResult] = []
    for raw in raw_pages:
        if not isinstance(raw, dict):
            continue
        try:
            page = PageExploreResult.model_validate(raw)
        except Exception:
            page = PageExploreResult(
                url=str(raw.get("url", "")),
                ok=bool(raw.get("ok", True)),
                title=str(raw.get("title", "")),
                heading=str(raw.get("heading", "")),
                console_errors=list(raw.get("console_errors") or []),
                network_failures=list(raw.get("network_failures") or []),
                matched_features=list(raw.get("matched_features") or []),
            )
        results.append(_validate_page(page))

    groups, untagged = _group_by_feature(results)

    pages_total = len(results)
    pages_failed = sum(1 for r in results if not r.passed)
    pages_warned = sum(1 for r in results if r.passed and r.has_warnings)
    pages_passed = pages_total - pages_failed - pages_warned

    checks_run = sum(len(r.checks) for r in results)
    checks_passed = sum(sum(1 for c in r.checks if c.passed) for r in results)

    failed = pages_failed > 0

    summary = PageValidationSummary(
        status="completed",
        pages_total=pages_total,
        pages_passed=pages_passed,
        pages_failed=pages_failed,
        pages_warned=pages_warned,
        checks_run=checks_run,
        checks_passed=checks_passed,
        rules_applied=_ALL_RULES,
        features=groups,
        untagged_pages=untagged,
        failed=failed,
    )

    context.merge_metadata({"validator": {"page_validation": summary.model_dump(mode="json")}})
    duration_ms = (time.perf_counter() - start) * 1000

    status = StepStatus.FAILED if failed else StepStatus.SUCCEEDED
    detail: dict[str, Any] = {
        "pages_total": pages_total,
        "pages_passed": pages_passed,
        "pages_failed": pages_failed,
        "pages_warned": pages_warned,
        "features_validated": len(groups),
    }
    if failed:
        detail["failure_category"] = "ui"

    return StepResult(
        layer="page_validator",
        name="page_validator",
        status=status,
        duration_ms=duration_ms,
        detail=detail,
        errors=[f"{pages_failed} page(s) failed validation"] if failed else [],
    )
