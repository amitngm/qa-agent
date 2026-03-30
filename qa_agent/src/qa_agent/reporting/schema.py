"""Generic QA report document model — JSON-serializable, application-agnostic."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Mapping, Optional, Sequence

from pydantic import BaseModel, Field


SCHEMA_VERSION = "1.0"


class PassFail(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    RUNNING = "running"
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class EvidenceItem(BaseModel):
    kind: str = "artifact"
    ref: str
    detail: Mapping[str, Any] = Field(default_factory=dict)
    source: str = "unspecified"
    flow_key: Optional[str] = None
    phase: Optional[str] = None


class FailureCategoryItem(BaseModel):
    category: str
    source: str = "unspecified"
    flow_key: Optional[str] = None
    phase: Optional[str] = None
    recoverable: Optional[bool] = None
    detail: Mapping[str, Any] = Field(default_factory=dict)


class StepReport(BaseModel):
    index: int
    layer: str
    name: str
    status: str
    step_id: Optional[str] = None
    pass_fail: PassFail
    failure_mode: Optional[str] = None
    duration_ms: Optional[float] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    errors: Sequence[str] = Field(default_factory=list)
    detail: Mapping[str, Any] = Field(default_factory=dict)
    failure_category: Optional[str] = None
    evidence_refs: List[EvidenceItem] = Field(default_factory=list)


class FlowPhaseReport(BaseModel):
    phase: str
    outcome: str
    duration_ms: Optional[float] = None
    errors: Sequence[str] = Field(default_factory=list)
    detail: Mapping[str, Any] = Field(default_factory=dict)


class FlowRunReport(BaseModel):
    flow_key: str
    flow_version: Optional[str] = None
    flow_instance_id: str
    ok: bool
    aborted_after: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    phases: List[FlowPhaseReport] = Field(default_factory=list)
    failure_classifications: List[FailureCategoryItem] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    summary: Mapping[str, Any] = Field(default_factory=dict)
    parse_notes: List[str] = Field(
        default_factory=list,
        description="Warnings when a row could not be validated as FlowEngineResult (partial dict mapping used).",
    )


class RunSummary(BaseModel):
    run_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    environment: Optional[str] = None
    orchestrator_summary: Mapping[str, Any] = Field(default_factory=dict)


class Conclusion(BaseModel):
    verdict: Verdict
    message: str
    failed_step_count: int = 0
    total_step_count: int = 0
    skipped_step_count: int = 0


class StakeholderFailureGroup(BaseModel):
    """Roll-up of technical failures for stakeholder-facing views."""

    stakeholder_category: str
    stakeholder_label: str
    count: int
    technical_taxonomies: List[str] = Field(default_factory=list)


class QaReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: str = Field(
        default="default",
        description="Routing key for dispatchers; usually derived from verdict via AgentConfig.severity_routing.",
    )
    run: RunSummary
    conclusion: Conclusion
    steps: List[StepReport] = Field(default_factory=list)
    failure_categories: List[FailureCategoryItem] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    flows: List[FlowRunReport] = Field(default_factory=list)
    technical_failure_taxonomy: List[Mapping[str, Any]] = Field(
        default_factory=list,
        description="Technical buckets (engine/internal); same shape as analysis.failure_taxonomy.",
    )
    stakeholder_failure_summary: List[StakeholderFailureGroup] = Field(
        default_factory=list,
        description="Mapped groups for reporting and UI.",
    )
    extensions: Mapping[str, Any] = Field(default_factory=dict)
