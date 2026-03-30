"""In-memory run store default for local and test execution."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from qa_agent.discovery.contract import DiscoveryReport
from qa_agent.store.layer_timing import MajorLayer


class InMemoryRunStore:
    """Keeps run data in process; safe for single-threaded orchestration."""

    def __init__(self) -> None:
        self._run_id: Optional[str] = None
        self._started_at: Optional[datetime] = None
        self._run_metadata: Dict[str, Any] = {}
        self._steps: List[Mapping[str, Any]] = []
        self._discovery: Optional[DiscoveryReport] = None
        self._extras: Dict[str, Any] = {}
        self._layer_timings: List[Mapping[str, Any]] = []

    @property
    def run_id(self) -> Optional[str]:
        return self._run_id

    def open_run(
        self,
        run_id: str,
        started_at: datetime,
        metadata: Mapping[str, Any],
    ) -> None:
        self._run_id = run_id
        self._started_at = started_at
        self._run_metadata = dict(metadata)
        self._steps = []
        self._discovery = None
        self._extras = {}
        self._layer_timings = []

    def record_step(self, sequence_index: int, step_payload: Mapping[str, Any]) -> None:
        self._steps.append({"sequence_index": sequence_index, **dict(step_payload)})

    def set_discovery_report(self, report: DiscoveryReport) -> None:
        self._discovery = report

    def get_discovery_report(self) -> Optional[DiscoveryReport]:
        return self._discovery

    def put_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def get_extra(self, key: str, default: Any = None) -> Any:
        return self._extras.get(key, default)

    def list_step_records(self) -> Sequence[Mapping[str, Any]]:
        return tuple(deepcopy(s) for s in self._steps)

    def record_layer_timing(
        self,
        phase: MajorLayer,
        duration_ms: float,
        *,
        pipeline_key: str,
        sequence_index: int,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        self._layer_timings.append(
            {
                "phase": phase.value,
                "duration_ms": duration_ms,
                "pipeline_key": pipeline_key,
                "sequence_index": sequence_index,
                "detail": dict(detail or {}),
            }
        )
