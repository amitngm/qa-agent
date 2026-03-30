"""Structured database and JSON state checks — config-driven; no product schema."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, List, Mapping, MutableMapping, Tuple

from sqlalchemy import create_engine, text

from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.validation.categories import ValidationCategory
from qa_agent.validation.data_models import DataCheckKind, DataCheckSpec, DataValidationCaseResult, DataValidationSummary


def _navigate(obj: Any, dotted_path: str) -> Any:
    cur: Any = obj
    for part in dotted_path.split("."):
        if part == "":
            continue
        if isinstance(cur, MutableMapping):
            cur = cur.get(part)
        else:
            return None
    return cur


def _load_json_document(spec: DataCheckSpec, context: RunContext) -> Any:
    src = (spec.json_source or "").strip().lower()
    if src == "inline":
        if spec.inline_document is None:
            raise ValueError("inline_document required when json_source=inline")
        return spec.inline_document
    if src == "file":
        if not spec.file_path:
            raise ValueError("file_path required when json_source=file")
        raw = Path(spec.file_path).read_text(encoding="utf-8")
        return json.loads(raw)
    if src == "metadata_path":
        meta = context.metadata.model_dump(mode="json", by_alias=True)
        if not spec.metadata_path:
            raise ValueError("metadata_path required when json_source=metadata_path")
        return _navigate(meta, spec.metadata_path)
    raise ValueError(f"json_source must be inline | file | metadata_path, got {spec.json_source!r}")


def _validate_json_document(doc: Any, spec: DataCheckSpec) -> Tuple[bool, List[str], Mapping[str, Any]]:
    errs: List[str] = []
    detail: dict[str, Any] = {"document_type": type(doc).__name__}

    if spec.expect_json_keys and isinstance(doc, dict):
        for k in spec.expect_json_keys:
            if k not in doc:
                errs.append(f"missing top-level key {k!r}")
    elif spec.expect_json_keys and not isinstance(doc, dict):
        errs.append("expect_json_keys requires a JSON object at root")

    if spec.expect_paths:
        for path, expected in spec.expect_paths.items():
            got = _navigate(doc, path) if path else doc
            if got != expected:
                errs.append(f"path {path!r}: expected {expected!r}, got {got!r}")

    detail["preview"] = repr(doc)[:1500] + ("…" if len(repr(doc)) > 1500 else "")
    return (len(errs) == 0, errs, detail)


def _run_sql_check(
    spec: DataCheckSpec,
    connections: Mapping[str, str],
) -> Tuple[bool, List[str], Mapping[str, Any]]:
    errs: List[str] = []
    if not spec.connection_ref or spec.connection_ref not in connections:
        return False, [f"unknown or missing connection_ref {spec.connection_ref!r}"], {}
    if not spec.sql or not spec.sql.strip():
        return False, ["sql is empty"], {}

    dsn = connections[spec.connection_ref]
    detail: dict[str, Any] = {"dsn_prefix": dsn.split(":", 1)[0] if ":" in dsn else dsn}

    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(spec.sql))
            rows = result.mappings().all()
    except Exception as exc:  # noqa: BLE001 — surface DB errors as validation failures
        return False, [str(exc)], {"error_type": type(exc).__name__}

    n = len(rows)
    detail["row_count"] = n

    if spec.expect_min_rows is not None and n < spec.expect_min_rows:
        errs.append(f"row count {n} < expect_min_rows {spec.expect_min_rows}")
    if spec.expect_max_rows is not None and n > spec.expect_max_rows:
        errs.append(f"row count {n} > expect_max_rows {spec.expect_max_rows}")

    if spec.expect_first_row is not None:
        if not rows:
            errs.append("expect_first_row set but query returned no rows")
        else:
            first = dict(rows[0])
            detail["first_row_keys"] = list(first.keys())
            for col, val in spec.expect_first_row.items():
                if first.get(col) != val:
                    errs.append(f"column {col!r}: expected {val!r}, got {first.get(col)!r}")

    return (len(errs) == 0, errs, detail)


def _run_case(
    spec: DataCheckSpec,
    context: RunContext,
    connections: Mapping[str, str],
) -> DataValidationCaseResult:
    if spec.kind == DataCheckKind.SQL_QUERY:
        ok, verrs, detail = _run_sql_check(spec, connections)
        return DataValidationCaseResult(
            case_id=spec.id,
            ok=ok,
            kind=spec.kind.value,
            validation_errors=verrs,
            detail=detail,
        )

    if spec.kind == DataCheckKind.JSON_STATE:
        try:
            doc = _load_json_document(spec, context)
        except Exception as exc:  # noqa: BLE001
            return DataValidationCaseResult(
                case_id=spec.id,
                ok=False,
                kind=spec.kind.value,
                error=str(exc),
                detail={"error_type": type(exc).__name__},
            )
        ok, verrs, detail = _validate_json_document(doc, spec)
        return DataValidationCaseResult(
            case_id=spec.id,
            ok=ok,
            kind=spec.kind.value,
            validation_errors=verrs,
            detail=detail,
        )

    return DataValidationCaseResult(
        case_id=spec.id,
        ok=False,
        kind=str(spec.kind),
        error=f"unsupported kind {spec.kind!r}",
    )


def run_data_validation(
    context: RunContext,
    plugin_config: Mapping[str, Any],
) -> StepResult:
    start = time.perf_counter()
    if not plugin_config.get("enabled", False):
        summary = DataValidationSummary(status="skipped", checks_run=0, checks_passed=0, failed=False)
        context.merge_metadata({"validator": {"data_validation": summary.model_dump(mode="json")}})
        return StepResult(
            layer="plugins",
            name="data_validation",
            status=StepStatus.SKIPPED,
            detail={"reason": "disabled", "summary": summary.model_dump(mode="json")},
        )

    connections = dict(plugin_config.get("connections") or {})
    raw_cases = plugin_config.get("cases") or []

    case_errors: List[str] = []
    specs: List[DataCheckSpec] = []
    for i, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            case_errors.append(f"cases[{i}] must be an object")
            continue
        payload = {**raw, "id": raw.get("id") or f"case_{i}"}
        try:
            specs.append(DataCheckSpec.model_validate(payload))
        except Exception as exc:  # noqa: BLE001
            case_errors.append(f"cases[{i}]: {exc}")

    if case_errors:
        summary = DataValidationSummary(
            status="failed",
            checks_run=0,
            checks_passed=0,
            failed=True,
            errors=case_errors,
        )
        context.merge_metadata({"validator": {"data_validation": summary.model_dump(mode="json")}})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="plugins",
            name="data_validation",
            status=StepStatus.FAILED,
            duration_ms=duration_ms,
            detail={
                "failure_category": ValidationCategory.DATA.value,
                "summary": summary.model_dump(mode="json"),
            },
            errors=case_errors,
        )

    results: List[DataValidationCaseResult] = []
    for spec in specs:
        results.append(_run_case(spec, context, connections))

    checks_run = len(results)
    checks_passed = sum(1 for r in results if r.ok)
    any_failed = any(not r.ok for r in results)

    summary = DataValidationSummary(
        status="completed",
        checks_run=checks_run,
        checks_passed=checks_passed,
        failed=any_failed,
        cases=results,
    )
    context.merge_metadata({"validator": {"data_validation": summary.model_dump(mode="json")}})

    duration_ms = (time.perf_counter() - start) * 1000
    detail: dict[str, Any] = {
        "summary": summary.model_dump(mode="json"),
        "checks_run": checks_run,
        "checks_passed": checks_passed,
    }

    if any_failed:
        detail["failure_category"] = ValidationCategory.DATA.value
        return StepResult(
            layer="plugins",
            name="data_validation",
            status=StepStatus.FAILED,
            duration_ms=duration_ms,
            detail=detail,
            errors=[
                f"case {r.case_id}: {r.error or '; '.join(r.validation_errors) or 'check failed'}"
                for r in results
                if not r.ok
            ],
        )

    return StepResult(
        layer="plugins",
        name="data_validation",
        status=StepStatus.SUCCEEDED,
        duration_ms=duration_ms,
        detail=detail,
    )
