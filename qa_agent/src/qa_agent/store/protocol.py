"""Run store abstraction — shared persistence surface for all layers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from qa_agent.discovery.contract import DiscoveryReport
from qa_agent.store.layer_timing import MajorLayer


@runtime_checkable
class RunStore(Protocol):
    """
    Layers read and write through a run store instead of ad-hoc globals.

    Implementations may be in-memory, database-backed, or remote; the core
    only depends on this protocol.

    Optional cross-run features (e.g. prior-run digests for the planner) use a separate
    :class:`~qa_agent.store.digest_listing.SupportsRunDigestListing` capability so stores are not
    forced to expose history or depend on a filesystem layout.
    """

    @property
    def run_id(self) -> Optional[str]:
        """Active run identifier once ``open_run`` has been called."""

    def open_run(
        self,
        run_id: str,
        started_at: datetime,
        metadata: Mapping[str, Any],
    ) -> None:
        """Initialize storage for a new run."""

    def record_step(self, sequence_index: int, step_payload: Mapping[str, Any]) -> None:
        """Persist one step outcome (typically a serialized ``StepResult``)."""

    def set_discovery_report(self, report: DiscoveryReport) -> None:
        """Replace the current discovery artifact for this run."""

    def get_discovery_report(self) -> Optional[DiscoveryReport]:
        """Return the last discovery report, if any."""

    def put_extra(self, key: str, value: Any) -> None:
        """Attach arbitrary run-scoped data (evidence handles, plans, etc.)."""

    def get_extra(self, key: str, default: Any = None) -> Any:
        """Read arbitrary run-scoped data."""

    def list_step_records(self) -> Sequence[Mapping[str, Any]]:
        """Ordered step records as generic mappings for inspection or reporting."""

    def record_layer_timing(
        self,
        phase: MajorLayer,
        duration_ms: float,
        *,
        pipeline_key: str,
        sequence_index: int,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one timing segment for a major phase (planner, discoverer, executor, etc.)."""
