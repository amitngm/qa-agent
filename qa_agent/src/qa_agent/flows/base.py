"""Flow contracts — implement or subclass to plug new flows into the engine."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from qa_agent.config.settings import AgentConfig
from qa_agent.flows.types import (
    FailureClassification,
    FailureSignal,
    FlowContext,
    FlowEngineOutcome,
    FlowPhase,
    FlowPhaseResult,
    PhaseOutcome,
)


@runtime_checkable
class FlowProtocol(Protocol):
    """Structural contract for flows consumed by FlowEngine."""

    flow_key: str
    flow_version: str

    def precheck(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult: ...

    def execute_steps(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult: ...

    def validate(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult: ...

    def capture_evidence(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult: ...

    def classify_failure(
        self,
        ctx: FlowContext,
        signal: FailureSignal,
        config: AgentConfig,
    ) -> FailureClassification: ...

    def cleanup(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult: ...

    def summarize(
        self,
        ctx: FlowContext,
        config: AgentConfig,
        outcome: FlowEngineOutcome,
    ) -> Mapping[str, Any]: ...


class BaseFlow:
    """Concrete base with safe defaults; override `flow_key` and any phase."""

    flow_key: str = "base"
    flow_version: str = "1.0.0"

    def precheck(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult:
        return FlowPhaseResult.ok(FlowPhase.PRECHECK)

    def execute_steps(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult:
        """Run the execute phase. For declarative :class:`~qa_agent.flows.step_spec.FlowStepSpec` graphs, call :func:`~qa_agent.flows.step_runner.standard_run_execute_phase`."""
        return FlowPhaseResult.ok(FlowPhase.EXECUTE)

    def validate(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult:
        return FlowPhaseResult.ok(FlowPhase.VALIDATE)

    def capture_evidence(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult:
        return FlowPhaseResult.ok(FlowPhase.EVIDENCE)

    def classify_failure(
        self,
        ctx: FlowContext,
        signal: FailureSignal,
        config: AgentConfig,
    ) -> FailureClassification:
        return FailureClassification(
            category="unknown",
            detail={"phase": signal.phase.value, "message": signal.message},
        )

    def cleanup(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult:
        return FlowPhaseResult.ok(FlowPhase.CLEANUP)

    def summarize(
        self,
        ctx: FlowContext,
        config: AgentConfig,
        outcome: FlowEngineOutcome,
    ) -> Mapping[str, Any]:
        return {
            "flow_key": outcome.flow_key,
            "ok": outcome.aborted_after is None
            and not any(p.outcome == PhaseOutcome.FAILED for p in outcome.phases),
            "phase_count": len(outcome.phases),
            "classification_count": len(outcome.failure_classifications),
        }
