"""Build a QaReport from orchestrator and optional flow-engine outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from pydantic import ValidationError

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import (
    RunContext,
    RunResult,
    RunStatus,
    StepResult,
    StepStatus,
    derive_run_lifecycle_status,
)
from qa_agent.core.run_metadata import analysis_block, flow_engine_results_list
from qa_agent.reporting.schema import (
    Conclusion,
    EvidenceItem,
    FailureCategoryItem,
    FlowPhaseReport,
    FlowRunReport,
    PassFail,
    QaReport,
    RunSummary,
    StakeholderFailureGroup,
    StepReport,
    Verdict,
)


def _step_pass_fail(status: StepStatus) -> PassFail:
    if status == StepStatus.SUCCEEDED:
        return PassFail.PASS
    if status == StepStatus.FAILED:
        return PassFail.FAIL
    if status == StepStatus.SKIPPED:
        return PassFail.SKIPPED
    if status == StepStatus.RUNNING:
        return PassFail.RUNNING
    if status == StepStatus.PENDING:
        return PassFail.UNKNOWN
    return PassFail.UNKNOWN


def _verdict_from_run_status(status: RunStatus) -> Verdict:
    if status == RunStatus.SUCCEEDED:
        return Verdict.PASS
    if status == RunStatus.FAILED:
        return Verdict.FAIL
    if status == RunStatus.PARTIAL:
        return Verdict.PARTIAL
    return Verdict.UNKNOWN


def _conclusion_message(verdict: Verdict, failed: int, total: int) -> str:
    if verdict == Verdict.PASS:
        return f"Run completed successfully ({total} step(s), none failed)."
    if verdict == Verdict.FAIL:
        return f"Run failed: {failed} of {total} step(s) reported failure."
    if verdict == Verdict.PARTIAL:
        return f"Run completed with partial success ({failed} failed, {total} total steps)."
    return "Run outcome could not be determined."


def _parse_flow_results(raw: Any) -> tuple[list[FlowRunReport], list[Mapping[str, Any]]]:
    """
    Parse flow engine rows into :class:`FlowRunReport` list.

    Non-object rows and schema validation failures are recorded explicitly (issues list and/or
    per-row :attr:`FlowRunReport.parse_notes`) instead of being dropped or masked.
    """
    structural_issues: list[Mapping[str, Any]] = []
    if not raw or not isinstance(raw, (list, tuple)):
        return [], structural_issues
    out: list[FlowRunReport] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            structural_issues.append(
                {
                    "index": index,
                    "reason": "flow_engine_result_not_a_json_object",
                    "value_type": type(item).__name__,
                }
            )
            continue
        try:
            from qa_agent.flows.types import FlowEngineResult

            fr = FlowEngineResult.model_validate(item)
        except ValidationError as err:
            partial = _flow_report_from_dict(item)
            note = (
                f"FlowEngineResult validation failed ({err.error_count()} error(s)); "
                "partial mapping from raw dict was used."
            )
            out.append(partial.model_copy(update={"parse_notes": [note]}))
            continue
        except Exception as err:
            partial = _flow_report_from_dict(item)
            note = f"Unexpected error while parsing flow engine result: {err}"
            out.append(partial.model_copy(update={"parse_notes": [note]}))
            continue
        out.append(_flow_report_from_engine(fr))
    return out, structural_issues


def _flow_report_from_engine(fr: Any) -> FlowRunReport:
    phases = [
        FlowPhaseReport(
            phase=p.phase.value if hasattr(p.phase, "value") else str(p.phase),
            outcome=p.outcome.value if hasattr(p.outcome, "value") else str(p.outcome),
            duration_ms=p.duration_ms,
            errors=list(p.errors),
            detail=dict(p.detail),
        )
        for p in fr.phases
    ]
    fcs: list[FailureCategoryItem] = []
    for fc in fr.failure_classifications:
        fcs.append(
            FailureCategoryItem(
                category=fc.category,
                source="flow_engine",
                flow_key=fr.flow_key,
                recoverable=fc.recoverable,
                detail=dict(fc.detail),
            )
        )
    ev: list[EvidenceItem] = []
    for e in fr.evidence:
        ev.append(
            EvidenceItem(
                kind=e.kind,
                ref=e.ref,
                detail=dict(e.detail),
                source="flow_engine",
                flow_key=fr.flow_key,
            )
        )
    aborted = fr.aborted_after.value if getattr(fr, "aborted_after", None) else None
    return FlowRunReport(
        flow_key=fr.flow_key,
        flow_version=getattr(fr, "flow_version", None),
        flow_instance_id=fr.flow_instance_id,
        ok=fr.ok,
        aborted_after=aborted,
        started_at=fr.started_at,
        finished_at=fr.finished_at,
        phases=phases,
        failure_classifications=fcs,
        evidence=ev,
        summary=dict(fr.summary),
        parse_notes=[],
    )


def _flow_report_from_dict(item: Mapping[str, Any]) -> FlowRunReport:
    phases_raw = item.get("phases") or []
    phases: list[FlowPhaseReport] = []
    for p in phases_raw:
        if not isinstance(p, dict):
            continue
        ph = p.get("phase")
        phases.append(
            FlowPhaseReport(
                phase=ph.value if hasattr(ph, "value") else str(ph),
                outcome=str(p.get("outcome", "")),
                duration_ms=p.get("duration_ms"),
                errors=list(p.get("errors") or ()),
                detail=dict(p.get("detail") or {}),
            )
        )
    fcs: list[FailureCategoryItem] = []
    for fc in item.get("failure_classifications") or []:
        if isinstance(fc, dict):
            fcs.append(
                FailureCategoryItem(
                    category=str(fc.get("category", "unknown")),
                    source="flow_engine",
                    flow_key=item.get("flow_key"),
                    recoverable=fc.get("recoverable"),
                    detail=dict(fc.get("detail") or {}),
                )
            )
    ev: list[EvidenceItem] = []
    for e in item.get("evidence") or []:
        if isinstance(e, dict):
            ev.append(
                EvidenceItem(
                    kind=str(e.get("kind", "artifact")),
                    ref=str(e.get("ref", "")),
                    detail=dict(e.get("detail") or {}),
                    source="flow_engine",
                    flow_key=item.get("flow_key"),
                )
            )
    aa = item.get("aborted_after")
    if aa is not None and hasattr(aa, "value"):
        aa = aa.value
    return FlowRunReport(
        flow_key=str(item.get("flow_key", "")),
        flow_version=item.get("flow_version"),
        flow_instance_id=str(item.get("flow_instance_id", "")),
        ok=bool(item.get("ok", False)),
        aborted_after=str(aa) if aa else None,
        started_at=item.get("started_at"),
        finished_at=item.get("finished_at"),
        phases=phases,
        failure_classifications=fcs,
        evidence=ev,
        summary=dict(item.get("summary") or {}),
        parse_notes=[],
    )


def _merge_failure_categories(
    flows: list[FlowRunReport],
    run_steps: list[StepReport],
) -> list[FailureCategoryItem]:
    merged: list[FailureCategoryItem] = []
    for f in flows:
        merged.extend(f.failure_classifications)
    for s in run_steps:
        if s.failure_category:
            merged.append(
                FailureCategoryItem(
                    category=s.failure_category,
                    source="orchestrator_step",
                    detail={"layer": s.layer, "name": s.name},
                )
            )
    return merged


def _merge_evidence(flows: list[FlowRunReport], steps: list[StepReport]) -> list[EvidenceItem]:
    ev: list[EvidenceItem] = []
    for f in flows:
        ev.extend(f.evidence)
    for s in steps:
        ev.extend(s.evidence_refs)
    return ev


def build_interim_run_result(context: RunContext) -> RunResult:
    """
    Build a :class:`RunResult` snapshot from :attr:`RunContext.pipeline_steps`.

    The orchestrator refreshes ``pipeline_steps`` immediately before each stage's ``run()``,
    so it lists only steps that have **already** completed (none from the current stage yet).
    :attr:`RunContext.executed_pipeline_steps` mirrors the orchestrator's full ``steps`` list
    after each recorded step for callers that need authoritative executed state.
    """
    steps = list(context.pipeline_steps)
    finished_at = datetime.now(timezone.utc)
    status = derive_run_lifecycle_status(steps)
    summary = {
        "step_count": len(steps),
        "failed": sum(1 for s in steps if s.status == StepStatus.FAILED),
        "skipped": sum(1 for s in steps if s.status == StepStatus.SKIPPED),
    }
    return RunResult(
        run_id=context.run_id,
        status=status,
        started_at=context.started_at,
        finished_at=finished_at,
        steps=steps,
        summary=summary,
    )


def build_report(
    run: RunResult,
    *,
    context: Optional[RunContext] = None,
    agent_config: Optional[AgentConfig] = None,
    environment: Optional[str] = None,
    extensions: Optional[Mapping[str, Any]] = None,
) -> QaReport:
    """Assemble a report from a completed run and optional context metadata."""
    meta = context.metadata_as_dict() if context else {}
    env = environment
    if env is None and agent_config is not None:
        env = agent_config.environment
    if env is None and isinstance(meta.get("environment"), str):
        env = meta["environment"]

    steps_out: list[StepReport] = []
    for i, s in enumerate(run.steps):
        steps_out.append(_step_to_report(i, s))

    flows, flow_structural_issues = _parse_flow_results(flow_engine_results_list(meta))
    ext_base = dict(extensions or {})
    if flow_structural_issues:
        prev = ext_base.get("flow_engine_parse_issues")
        merged_issues: list[Mapping[str, Any]] = []
        if isinstance(prev, list):
            merged_issues.extend(p for p in prev if isinstance(p, Mapping))
        merged_issues.extend(flow_structural_issues)
        ext_base["flow_engine_parse_issues"] = merged_issues

    failure_categories = _merge_failure_categories(flows, steps_out)
    evidence = _merge_evidence(flows, steps_out)

    ab = analysis_block(meta)
    technical_tax = list(ab.get("failure_taxonomy") or [])
    stakeholder_raw = ab.get("stakeholder_failure_summary") or []
    stakeholder_summary: list[StakeholderFailureGroup] = []
    if isinstance(stakeholder_raw, list):
        for row in stakeholder_raw:
            if not isinstance(row, dict):
                continue
            stakeholder_summary.append(
                StakeholderFailureGroup(
                    stakeholder_category=str(row.get("stakeholder_category", "")),
                    stakeholder_label=str(row.get("stakeholder_label", "")),
                    count=int(row.get("count", 0)),
                    technical_taxonomies=list(row.get("technical_taxonomies") or []),
                )
            )

    # Lift page_validation and auto_explore summary into extensions so the HTML
    # renderer can produce a features section and a login/explore summary.
    page_val = (meta.get("validator") or {}).get("page_validation")
    if isinstance(page_val, dict) and page_val:
        ext_base["page_validation"] = page_val

    ae_raw = (meta.get("executor") or {}).get("auto_explore_ui")
    if ae_raw is not None:
        if hasattr(ae_raw, "model_dump"):
            ae_dict = ae_raw.model_dump(mode="json")
        elif isinstance(ae_raw, dict):
            ae_dict = ae_raw
        else:
            ae_dict = None
        if isinstance(ae_dict, dict):
            # Exclude the full visited-pages list to keep extensions compact.
            ext_base["auto_explore_summary"] = {
                k: v for k, v in ae_dict.items() if k != "visited"
            }

    step_assertions = (meta.get("validator") or {}).get("step_assertions")
    if isinstance(step_assertions, dict) and step_assertions:
        ext_base["step_assertions"] = step_assertions

    failed = sum(1 for x in steps_out if x.pass_fail == PassFail.FAIL)
    skipped = sum(1 for x in steps_out if x.pass_fail == PassFail.SKIPPED)
    total = len(steps_out)
    verdict = _verdict_from_run_status(run.status)

    conclusion = Conclusion(
        verdict=verdict,
        message=_conclusion_message(verdict, failed, total),
        failed_step_count=failed,
        total_step_count=total,
        skipped_step_count=skipped,
    )

    severity_key = "default"
    if agent_config is not None:
        sr = agent_config.severity_routing
        severity_key = sr.verdict_to_severity.get(conclusion.verdict.value, "default")

    return QaReport(
        severity=severity_key,
        run=RunSummary(
            run_id=run.run_id,
            status=run.status.value,
            started_at=run.started_at,
            finished_at=run.finished_at,
            environment=env,
            orchestrator_summary=dict(run.summary),
        ),
        conclusion=conclusion,
        steps=steps_out,
        failure_categories=failure_categories,
        evidence=evidence,
        flows=flows,
        technical_failure_taxonomy=technical_tax,
        stakeholder_failure_summary=stakeholder_summary,
        extensions=ext_base,
    )


def _step_to_report(index: int, s: StepResult) -> StepReport:
    detail = dict(s.detail)
    failure_category = None
    if isinstance(detail.get("failure_category"), str):
        failure_category = detail["failure_category"]
    evidence_refs: list[EvidenceItem] = []
    raw_ev = detail.get("evidence_refs")
    if isinstance(raw_ev, (list, tuple)):
        for e in raw_ev:
            if isinstance(e, dict):
                evidence_refs.append(
                    EvidenceItem(
                        kind=str(e.get("kind", "artifact")),
                        ref=str(e.get("ref", "")),
                        detail=dict(e.get("detail") or {}),
                        source="orchestrator_step",
                    )
                )
    fm = s.failure_mode.value if s.failure_mode is not None else None
    return StepReport(
        index=index,
        layer=s.layer,
        name=s.name,
        status=s.status.value,
        step_id=s.step_id,
        pass_fail=_step_pass_fail(s.status),
        failure_mode=fm,
        duration_ms=s.duration_ms,
        errors=list(s.errors),
        detail=detail,
        failure_category=failure_category,
        evidence_refs=evidence_refs,
    )
