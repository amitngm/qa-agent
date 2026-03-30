"""Optional filesystem persistence alongside in-memory state."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from qa_agent.discovery.contract import DiscoveryReport
from qa_agent.store.digest_listing import scan_runs_directory_for_digests
from qa_agent.store.layer_timing import MajorLayer
from qa_agent.store.memory import InMemoryRunStore


class FileRunStore(InMemoryRunStore):
    """
    Persists step records and metadata to ``root_dir`` (JSON + JSONL).

    Reads served from the in-memory mirror for simplicity.
    """

    def __init__(self, root_dir: Path) -> None:
        super().__init__()
        self._root = Path(root_dir)

    @property
    def runs_root(self) -> Path:
        """Directory containing one subdirectory per ``run_id`` (same as the constructor path)."""
        return self._root

    def list_recent_run_digests(
        self,
        *,
        limit: int = 20,
        exclude_run_id: Optional[str] = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Implement :class:`~qa_agent.store.digest_listing.SupportsRunDigestListing` for prior-run digests."""
        return scan_runs_directory_for_digests(
            self._root,
            limit=limit,
            exclude_run_id=exclude_run_id,
        )

    def open_run(
        self,
        run_id: str,
        started_at: datetime,
        metadata: Mapping[str, Any],
    ) -> None:
        super().open_run(run_id, started_at, metadata)
        run_dir = self._root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "metadata": dict(metadata),
        }
        (run_dir / "meta.json").write_text(json.dumps(meta, default=str, indent=2), encoding="utf-8")
        (run_dir / "steps.jsonl").write_text("", encoding="utf-8")
        (run_dir / "layer_timings.jsonl").write_text("", encoding="utf-8")

    def record_step(self, sequence_index: int, step_payload: Mapping[str, Any]) -> None:
        super().record_step(sequence_index, step_payload)
        rid = self.run_id
        if not rid:
            return
        path = self._root / rid / "steps.jsonl"
        line = json.dumps({"sequence_index": sequence_index, **dict(step_payload)}, default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def record_layer_timing(
        self,
        phase: MajorLayer,
        duration_ms: float,
        *,
        pipeline_key: str,
        sequence_index: int,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().record_layer_timing(
            phase,
            duration_ms,
            pipeline_key=pipeline_key,
            sequence_index=sequence_index,
            detail=detail,
        )
        rid = self.run_id
        if not rid:
            return
        payload = {
            "phase": phase.value,
            "duration_ms": duration_ms,
            "pipeline_key": pipeline_key,
            "sequence_index": sequence_index,
            "detail": dict(detail or {}),
        }
        path = self._root / rid / "layer_timings.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")

    def set_discovery_report(self, report: DiscoveryReport) -> None:
        super().set_discovery_report(report)
        rid = self.run_id
        if not rid:
            return
        path = self._root / rid / "discovery.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
