"""Prior-run digest listing — optional capability for :class:`~qa_agent.store.protocol.RunStore` implementations."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from qa_agent.store.jsonl_utils import count_nonempty_lines


@runtime_checkable
class SupportsRunDigestListing(Protocol):
    """
    Optional store capability: list recent run digests (newest first) for planners and dashboards.

    Implementations may scan disk, query a database, or call a remote index — the orchestrator only
    checks this protocol, not :class:`~qa_agent.store.file_store.FileRunStore`.
    """

    def list_recent_run_digests(
        self,
        *,
        limit: int = 20,
        exclude_run_id: Optional[str] = None,
    ) -> Sequence[Mapping[str, Any]]:
        ...


def scan_runs_directory_for_digests(
    runs_root: Path,
    *,
    limit: int = 20,
    exclude_run_id: Optional[str] = None,
) -> Sequence[Mapping[str, Any]]:
    """
    Shared layout: ``<runs_root>/<run_id>/meta.json`` plus optional ``steps.jsonl`` (same as :class:`~qa_agent.store.file_store.FileRunStore`).

    Used by :class:`~qa_agent.store.file_store.FileRunStore` and :class:`~qa_agent.store.history_reader.StoreBackedRunHistoryReader`.
    """
    if limit <= 0:
        return ()
    root = Path(runs_root)
    if not root.is_dir():
        return ()

    candidates: list[tuple[datetime, str, Path, dict[str, Any]]] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        rid = sub.name
        if rid == exclude_run_id:
            continue
        meta_path = sub / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        started = raw.get("started_at")
        if not isinstance(started, str):
            continue
        try:
            dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            continue
        candidates.append((dt, rid, sub, raw))

    candidates.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for _, rid, sub, raw in candidates[:limit]:
        step_count = count_nonempty_lines(sub / "steps.jsonl")
        md = raw.get("metadata")
        if not isinstance(md, dict):
            md = {}
        out.append(
            {
                "run_id": str(raw.get("run_id", rid)),
                "started_at": raw.get("started_at"),
                "metadata": md,
                "step_count": step_count,
            }
        )
    return tuple(out)


class DigestListingRunHistoryReader:
    """
    Adapts any :class:`SupportsRunDigestListing` to :class:`~qa_agent.capabilities.future.RunHistoryReader`.

    The orchestrator wires this when the active :class:`~qa_agent.store.protocol.RunStore` exposes digest listing,
    so custom stores do not need to subclass :class:`~qa_agent.store.file_store.FileRunStore`.
    """

    def __init__(self, source: SupportsRunDigestListing, *, exclude_run_id: Optional[str] = None) -> None:
        self._source = source
        self._exclude_run_id = exclude_run_id

    def read_recent_digests(self, *, limit: int = 20) -> Sequence[Mapping[str, Any]]:
        return self._source.list_recent_run_digests(limit=limit, exclude_run_id=self._exclude_run_id)
