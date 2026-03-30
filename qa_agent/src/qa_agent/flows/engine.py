"""
Generic flow engine — runs pluggable flows through a fixed **phase** lifecycle.

Execute-phase **step** structure (:class:`~qa_agent.flows.step_spec.FlowStepSpec`) is not
interpreted here; see :func:`~qa_agent.flows.step_runner.standard_run_execute_phase`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import List, Optional

from qa_agent.config.settings import AgentConfig
from qa_agent.flows.base import FlowProtocol
from qa_agent.flows.types import (
    FailureClassification,
    FailureSignal,
    FlowContext,
    FlowEngineOutcome,
    FlowEngineResult,
    FlowEvidenceRef,
    FlowPhase,
    FlowPhaseResult,
    PhaseOutcome,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_evidence_refs(phase_result: FlowPhaseResult) -> List[FlowEvidenceRef]:
    raw = phase_result.detail.get("evidence_refs")
    if not raw or not isinstance(raw, (list, tuple)):
        return []
    out: List[FlowEvidenceRef] = []
    for item in raw:
        if isinstance(item, FlowEvidenceRef):
            out.append(item)
        elif isinstance(item, dict):
            out.append(FlowEvidenceRef.model_validate(item))
    return out


def _wrap_phase_call(
    phase: FlowPhase,
    fn: Callable[[], FlowPhaseResult],
) -> FlowPhaseResult:
    start = time.perf_counter()
    try:
        result = fn()
        if not isinstance(result, FlowPhaseResult):
            return FlowPhaseResult.fail(
                phase,
                "flow returned non-FlowPhaseResult",
                duration_ms=(time.perf_counter() - start) * 1000,
                detail={"return_type": type(result).__name__},
            )
        if result.duration_ms is None:
            result = result.model_copy(
                update={"duration_ms": (time.perf_counter() - start) * 1000},
            )
        return result
    except Exception as exc:  # noqa: BLE001 — engine boundary; keep run alive
        return FlowPhaseResult.fail(
            phase,
            str(exc),
            duration_ms=(time.perf_counter() - start) * 1000,
            detail={"exception_type": type(exc).__name__},
        )


class FlowEngine:
    """Runs one flow instance: precheck → execute → validate → evidence → cleanup → summarize."""

    def run(
        self,
        flow: FlowProtocol,
        ctx: Optional[FlowContext] = None,
        config: Optional[AgentConfig] = None,
        *,
        parent_run_id: Optional[str] = None,
    ) -> FlowEngineResult:
        from qa_agent.config.settings import load_agent_config

        config = config or load_agent_config()
        fe = config.flow_engine
        ctx = ctx or FlowContext()
        if parent_run_id is not None:
            ctx.parent_run_id = parent_run_id
        fv = getattr(flow, "flow_version", None)
        if fv and isinstance(fv, str):
            ctx = ctx.model_copy(update={"flow_version": fv})

        started_at = _now()
        phases: list[FlowPhaseResult] = []
        classifications: list[FailureClassification] = []
        evidence_accum: list[FlowEvidenceRef] = []
        aborted_after: Optional[FlowPhase] = None

        def classify_from(result: FlowPhaseResult) -> None:
            signal = FailureSignal(
                phase=result.phase,
                message=result.errors[0] if result.errors else "phase_failed",
                errors=list(result.errors),
                detail=dict(result.detail),
            )
            try:
                classifications.append(flow.classify_failure(ctx, signal, config))
            except Exception as exc:  # noqa: BLE001
                classifications.append(
                    FailureClassification(
                        category="classification_error",
                        detail={"error": str(exc), "phase": result.phase.value},
                    )
                )

        # --- precheck ---
        pre = _wrap_phase_call(FlowPhase.PRECHECK, lambda: flow.precheck(ctx, config))
        phases.append(pre)
        if pre.outcome == PhaseOutcome.FAILED:
            classify_from(pre)
            if fe.skip_execute_after_precheck_failure:
                aborted_after = FlowPhase.PRECHECK
                return self._finalize(
                    flow,
                    ctx,
                    config,
                    started_at,
                    phases,
                    classifications,
                    evidence_accum,
                    aborted_after,
                )

        # --- execute ---
        ex = _wrap_phase_call(FlowPhase.EXECUTE, lambda: flow.execute_steps(ctx, config))
        phases.append(ex)
        execute_failed = ex.outcome == PhaseOutcome.FAILED
        if execute_failed:
            classify_from(ex)
            if fe.skip_validate_after_execute_failure:
                aborted_after = FlowPhase.EXECUTE
                if not fe.skip_evidence_after_execute_failure:
                    self._run_evidence_phase(flow, ctx, config, phases, evidence_accum, classify_from)
                return self._finalize(
                    flow,
                    ctx,
                    config,
                    started_at,
                    phases,
                    classifications,
                    evidence_accum,
                    aborted_after,
                )

        # --- validate ---
        va = _wrap_phase_call(FlowPhase.VALIDATE, lambda: flow.validate(ctx, config))
        phases.append(va)
        validate_failed = va.outcome == PhaseOutcome.FAILED
        if validate_failed:
            classify_from(va)
            aborted_after = FlowPhase.VALIDATE
            if not fe.capture_evidence_after_validate_failure:
                return self._finalize(
                    flow,
                    ctx,
                    config,
                    started_at,
                    phases,
                    classifications,
                    evidence_accum,
                    aborted_after,
                )

        # --- evidence (happy path or after validate failure with capture enabled) ---
        if execute_failed and fe.skip_evidence_after_execute_failure:
            phases.append(
                FlowPhaseResult.skipped(
                    FlowPhase.EVIDENCE,
                    reason="execute_failed_skip_evidence",
                )
            )
        else:
            self._run_evidence_phase(flow, ctx, config, phases, evidence_accum, classify_from)

        return self._finalize(
            flow,
            ctx,
            config,
            started_at,
            phases,
            classifications,
            evidence_accum,
            aborted_after,
        )

    @staticmethod
    def _run_evidence_phase(
        flow: FlowProtocol,
        ctx: FlowContext,
        config: AgentConfig,
        phases: list[FlowPhaseResult],
        evidence_accum: list[FlowEvidenceRef],
        classify_from: Callable[[FlowPhaseResult], None],
    ) -> None:
        ev = _wrap_phase_call(FlowPhase.EVIDENCE, lambda: flow.capture_evidence(ctx, config))
        phases.append(ev)
        if ev.outcome == PhaseOutcome.FAILED:
            classify_from(ev)
        evidence_accum.extend(_extract_evidence_refs(ev))

    def _finalize(
        self,
        flow: FlowProtocol,
        ctx: FlowContext,
        config: AgentConfig,
        started_at: datetime,
        phases: list[FlowPhaseResult],
        classifications: list[FailureClassification],
        evidence_accum: list[FlowEvidenceRef],
        aborted_after: Optional[FlowPhase],
    ) -> FlowEngineResult:
        cleanup_res = _wrap_phase_call(
            FlowPhase.CLEANUP,
            lambda: flow.cleanup(ctx, config),
        )
        phases.append(cleanup_res)
        if cleanup_res.outcome == PhaseOutcome.FAILED:
            signal = FailureSignal(
                phase=FlowPhase.CLEANUP,
                message=cleanup_res.errors[0] if cleanup_res.errors else "cleanup_failed",
                errors=list(cleanup_res.errors),
                detail=dict(cleanup_res.detail),
            )
            try:
                classifications.append(flow.classify_failure(ctx, signal, config))
            except Exception as exc:  # noqa: BLE001
                classifications.append(
                    FailureClassification(
                        category="classification_error",
                        detail={"error": str(exc), "phase": "cleanup"},
                    )
                )

        outcome = FlowEngineOutcome(
            flow_key=flow.flow_key,
            flow_version=ctx.flow_version,
            flow_instance_id=ctx.flow_instance_id,
            phases=list(phases),
            failure_classifications=list(classifications),
            evidence=list(evidence_accum),
            aborted_after=aborted_after,
        )
        try:
            summary = dict(flow.summarize(ctx, config, outcome))
        except Exception as exc:  # noqa: BLE001
            summary = {"error": "summarize_failed", "message": str(exc)}

        finished_at = _now()
        ok = aborted_after is None and not any(
            p.outcome == PhaseOutcome.FAILED for p in phases
        )
        return FlowEngineResult(
            flow_key=flow.flow_key,
            flow_version=ctx.flow_version,
            flow_instance_id=ctx.flow_instance_id,
            parent_run_id=ctx.parent_run_id,
            ok=ok,
            aborted_after=aborted_after,
            started_at=started_at,
            finished_at=finished_at,
            phases=phases,
            failure_classifications=classifications,
            evidence=evidence_accum,
            summary=summary,
        )
