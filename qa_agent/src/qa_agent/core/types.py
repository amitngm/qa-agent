"""Shared domain types for runs, steps, and outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field

from qa_agent.capabilities.future import RunHistoryReader
from qa_agent.core.run_metadata import RunMetadata
from qa_agent.core.status import RunLifecycleStatus, StepExecutionStatus, StepFailureMode

# Backward-compatible aliases
RunStatus = RunLifecycleStatus
StepStatus = StepExecutionStatus


class StepResult(BaseModel):
    layer: str
    name: str
    status: StepExecutionStatus
    step_id: Optional[str] = Field(
        default=None,
        description="Stable id for this invocation within the run.",
    )
    duration_ms: Optional[float] = None
    detail: Mapping[str, Any] = Field(default_factory=dict)
    errors: Sequence[str] = Field(default_factory=list)
    failure_mode: Optional[StepFailureMode] = Field(
        default=None,
        description="When status is failed, controls orchestrator branching; None uses config default.",
    )


class RunResult(BaseModel):
    run_id: str
    status: RunLifecycleStatus
    started_at: datetime
    finished_at: datetime
    steps: list[StepResult] = Field(default_factory=list)
    summary: Mapping[str, Any] = Field(default_factory=dict)


def derive_run_lifecycle_status(steps: Sequence[StepResult]) -> RunLifecycleStatus:
    """Derive aggregate run status from completed step outcomes (orchestrator and reporting snapshots)."""
    if any(s.status == StepExecutionStatus.FAILED for s in steps):
        return RunLifecycleStatus.FAILED
    if steps and all(s.status == StepExecutionStatus.SKIPPED for s in steps):
        return RunLifecycleStatus.PARTIAL
    return RunLifecycleStatus.SUCCEEDED


class RunContext(BaseModel):
    """Mutable bag for cross-layer data; avoid product-specific keys in core code."""

    model_config = {"extra": "allow", "arbitrary_types_allowed": True}

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: RunMetadata = Field(default_factory=RunMetadata)
    run_store: Optional[Any] = Field(
        default=None,
        description="RunStore implementation; set by orchestrator if not provided.",
    )
    run_history_reader: Optional[RunHistoryReader] = Field(
        default=None,
        description="Optional reader for prior run digests (e.g. closed-loop planning).",
    )
    pipeline_steps: list[StepResult] = Field(
        default_factory=list,
        description=(
            "Snapshot of steps completed immediately before the current stage's ``run()`` is invoked; "
            "used for interim reporting. The orchestrator sets this per stage, then finalizes after the run."
        ),
    )
    executed_pipeline_steps: list[StepResult] = Field(
        default_factory=list,
        description="Authoritative list of steps recorded so far in the run (mirrors orchestrator state after each step).",
    )
    plugin_secrets: dict[str, Any] = Field(
        default_factory=dict,
        description="Ephemeral values for plugins (e.g. passwords); never written to run metadata JSON.",
    )

    def metadata_as_dict(self) -> dict[str, Any]:
        """JSON-friendly snapshot for plugins and persistence (stable wire keys via aliases)."""
        return self.metadata.model_dump(mode="json", by_alias=True)

    def merge_metadata(self, updates: Mapping[str, Any]) -> None:
        """Shallow merge into :class:`RunMetadata` (same semantics as former ``dict.update``)."""
        self.metadata = self.metadata.merged(updates)
