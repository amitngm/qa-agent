"""Executor driver — four-operation automation boundary (distinct from UI PlatformDriver)."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ExecutorResult(BaseModel):
    ok: bool
    detail: Mapping[str, Any] = Field(default_factory=dict)
    errors: tuple[str, ...] = Field(default_factory=tuple)


@runtime_checkable
class ExecutorDriver(Protocol):
    """
    Host-agnostic execution surface (jobs, workflows, remote runners).

    Four operations: ``execute`` (run work), ``probe`` (lightweight status),
    ``cancel`` (stop), ``finalize`` (tear down / release resources).
    """

    def execute(self, spec: Mapping[str, Any]) -> ExecutorResult:
        """Run a unit of work described by ``spec``."""

    def probe(self, spec: Mapping[str, Any]) -> ExecutorResult:
        """Non-mutating status or health check."""

    def cancel(self, spec: Mapping[str, Any]) -> ExecutorResult:
        """Request cancellation of an in-flight execution."""

    def finalize(self, spec: Mapping[str, Any]) -> ExecutorResult:
        """Release resources or confirm completion."""


class NoOpExecutorDriver:
    """Successful no-ops for skeleton runs."""

    def execute(self, spec: Mapping[str, Any]) -> ExecutorResult:
        return ExecutorResult(ok=True, detail={"noop": True, "op": "execute"})

    def probe(self, spec: Mapping[str, Any]) -> ExecutorResult:
        return ExecutorResult(ok=True, detail={"noop": True, "op": "probe"})

    def cancel(self, spec: Mapping[str, Any]) -> ExecutorResult:
        return ExecutorResult(ok=True, detail={"noop": True, "op": "cancel"})

    def finalize(self, spec: Mapping[str, Any]) -> ExecutorResult:
        return ExecutorResult(ok=True, detail={"noop": True, "op": "finalize"})
