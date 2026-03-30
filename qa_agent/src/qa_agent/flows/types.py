"""Flow engine domain types — application-agnostic."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, MutableMapping, Optional, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field


class FlowPhase(str, Enum):
    PRECHECK = "precheck"
    EXECUTE = "execute"
    VALIDATE = "validate"
    EVIDENCE = "evidence"
    CLEANUP = "cleanup"
    SUMMARY = "summary"


class PhaseOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class FlowContext(BaseModel):
    """Isolated state for one flow run; link to a parent QA run via parent_run_id only."""

    model_config = {"extra": "allow"}

    flow_version: str = Field(
        default="1.0.0",
        description="Semantic version of the flow definition for this run.",
    )
    flow_instance_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_run_id: Optional[str] = None
    baggage: MutableMapping[str, Any] = Field(default_factory=dict)

    def merge_baggage(self, updates: Mapping[str, Any]) -> None:
        self.baggage.update(dict(updates))


class FlowStepOutcome(BaseModel):
    """Single logical step inside the execute phase (optional granularity)."""

    step_key: str
    outcome: PhaseOutcome
    step_kind: Optional[str] = Field(
        default=None,
        description="FlowStepKind value when recorded by standard_run_execute_phase (or set manually).",
    )
    branch_taken: Optional[str] = Field(
        default=None,
        description="For BRANCH/CONDITIONAL: opaque label of path chosen.",
    )
    poll_iterations: Optional[int] = Field(
        default=None,
        description="For POLL: how many wait iterations ran.",
    )
    detail: Mapping[str, Any] = Field(default_factory=dict)
    errors: Sequence[str] = Field(default_factory=list)


class FlowPhaseResult(BaseModel):
    phase: FlowPhase
    outcome: PhaseOutcome
    duration_ms: Optional[float] = None
    detail: Mapping[str, Any] = Field(default_factory=dict)
    errors: Sequence[str] = Field(default_factory=list)
    step_outcomes: Sequence[FlowStepOutcome] = Field(default_factory=list)

    @classmethod
    def ok(
        cls,
        phase: FlowPhase,
        *,
        duration_ms: Optional[float] = None,
        detail: Optional[Mapping[str, Any]] = None,
        step_outcomes: Optional[Sequence[FlowStepOutcome]] = None,
    ) -> FlowPhaseResult:
        return cls(
            phase=phase,
            outcome=PhaseOutcome.SUCCEEDED,
            duration_ms=duration_ms,
            detail=dict(detail or {}),
            step_outcomes=list(step_outcomes or ()),
        )

    @classmethod
    def fail(
        cls,
        phase: FlowPhase,
        message: str,
        *,
        duration_ms: Optional[float] = None,
        detail: Optional[Mapping[str, Any]] = None,
        errors: Optional[Sequence[str]] = None,
    ) -> FlowPhaseResult:
        errs = [message]
        if errors:
            errs = list(errors)
        return cls(
            phase=phase,
            outcome=PhaseOutcome.FAILED,
            duration_ms=duration_ms,
            detail=dict(detail or {}),
            errors=errs,
        )

    @classmethod
    def skipped(
        cls,
        phase: FlowPhase,
        *,
        reason: str = "not_applicable",
        duration_ms: Optional[float] = None,
    ) -> FlowPhaseResult:
        return cls(
            phase=phase,
            outcome=PhaseOutcome.SKIPPED,
            duration_ms=duration_ms,
            detail={"reason": reason},
        )


class FailureSignal(BaseModel):
    """Input to failure classification — built by the engine when a phase fails."""

    phase: FlowPhase
    message: str
    errors: Sequence[str] = Field(default_factory=list)
    detail: Mapping[str, Any] = Field(default_factory=dict)


class FailureClassification(BaseModel):
    """Output of a flow's failure hook — taxonomy is entirely flow-defined."""

    category: str = "unknown"
    recoverable: Optional[bool] = None
    detail: Mapping[str, Any] = Field(default_factory=dict)


class FlowEvidenceRef(BaseModel):
    """Opaque evidence handle (path, URI, object key, etc.) — engine does not interpret."""

    kind: str = "artifact"
    ref: str
    detail: Mapping[str, Any] = Field(default_factory=dict)


class FlowEngineOutcome(BaseModel):
    """Snapshot passed to summarize after cleanup."""

    flow_key: str
    flow_version: str = "1.0.0"
    flow_instance_id: str
    phases: Sequence[FlowPhaseResult] = Field(default_factory=list)
    failure_classifications: Sequence[FailureClassification] = Field(default_factory=list)
    evidence: Sequence[FlowEvidenceRef] = Field(default_factory=list)
    aborted_after: Optional[FlowPhase] = None


class FlowEngineResult(BaseModel):
    """Result of running one flow through the engine."""

    flow_key: str
    flow_version: str = "1.0.0"
    flow_instance_id: str
    parent_run_id: Optional[str] = None
    ok: bool
    aborted_after: Optional[FlowPhase] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    phases: list[FlowPhaseResult] = Field(default_factory=list)
    failure_classifications: list[FailureClassification] = Field(default_factory=list)
    evidence: list[FlowEvidenceRef] = Field(default_factory=list)
    summary: Mapping[str, Any] = Field(default_factory=dict)
