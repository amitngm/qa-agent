"""Explicit run and step lifecycle statuses and failure-handling modes."""

from __future__ import annotations

from enum import Enum


class RunLifecycleStatus(str, Enum):
    """Terminal and in-progress states for an orchestrated run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class StepExecutionStatus(str, Enum):
    """Lifecycle of a single orchestrator step (layer or plugin invocation)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepFailureMode(str, Enum):
    """How the orchestrator proceeds after a failed step."""

    STOP = "stop"
    CONTINUE = "continue"
    SKIP_TO_CLEANUP = "skip_to_cleanup"
