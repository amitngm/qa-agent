"""
Later-phase capability stubs — contracts plus no-op defaults for injection.

Implemented elsewhere today where noted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

# Mid-flow failure: see qa_agent.core.status.StepFailureMode (orchestrator; implemented).


@runtime_checkable
class RunHistoryReader(Protocol):
    """Read recent run digests for closed-loop planning or dashboards (future hook)."""

    def read_recent_digests(self, *, limit: int = 20) -> Sequence[Mapping[str, Any]]:
        """Return up to ``limit`` recent run summaries (newest first)."""
        ...


@runtime_checkable
class IdempotencyRollbackValidator(Protocol):
    """Validate idempotent writes and rollback semantics."""

    def validate(self, spec: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class PerformanceRegressionDetector(Protocol):
    """Compare latency/throughput baselines."""

    def compare(self, metrics: Mapping[str, Any], baseline: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class CrossFlowContractValidator(Protocol):
    """Assert invariants spanning multiple flows."""

    def validate(self, flow_summaries: list[Mapping[str, Any]]) -> Mapping[str, Any]: ...


@runtime_checkable
class CustomAssertionPlugin(Protocol):
    """Register domain-specific assertions without forking core validators."""

    assertion_id: str

    def run(self, context_payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class NoOpRunHistoryReader:
    def read_recent_digests(self, *, limit: int = 20) -> Sequence[Mapping[str, Any]]:
        return ()


class NoOpIdempotencyRollbackValidator:
    def validate(self, spec: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"noop": True, "ok": True}


class NoOpPerformanceRegressionDetector:
    def compare(self, metrics: Mapping[str, Any], baseline: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"noop": True, "regression": False}


class NoOpCrossFlowContractValidator:
    def validate(self, flow_summaries: list[Mapping[str, Any]]) -> Mapping[str, Any]:
        return {"noop": True, "ok": True}


class NoOpCustomAssertionPlugin:
    assertion_id = "noop"

    def run(self, context_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"noop": True, "ok": True}


@dataclass
class FutureCapabilityStubs:
    """Namespace holder for optional dependency injection of future hooks."""

    run_history: Optional[RunHistoryReader] = None
    idempotency: Optional[IdempotencyRollbackValidator] = None
    performance: Optional[PerformanceRegressionDetector] = None
    cross_flow: Optional[CrossFlowContractValidator] = None
    custom_assertion: Optional[CustomAssertionPlugin] = None

    @classmethod
    def all_noop(cls) -> FutureCapabilityStubs:
        """Default wired no-ops for deterministic skeleton behavior."""
        return cls(
            run_history=NoOpRunHistoryReader(),
            idempotency=NoOpIdempotencyRollbackValidator(),
            performance=NoOpPerformanceRegressionDetector(),
            cross_flow=NoOpCrossFlowContractValidator(),
            custom_assertion=NoOpCustomAssertionPlugin(),
        )
