"""Load run snapshots from FileRunStore on-disk layout (meta.json + steps.jsonl)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from qa_agent.core.run_metadata import RunMetadata
from qa_agent.core.types import StepResult, derive_run_lifecycle_status
from qa_agent.core.status import StepExecutionStatus


@dataclass
class DiskRunSnapshot:
    run_id: str
    started_at: datetime
    finished_at: datetime
    metadata: RunMetadata
    steps: list[StepResult]
    summary: Mapping[str, Any]
    status: str


def _parse_step_line(obj: Mapping[str, Any]) -> StepResult:
    d = dict(obj)
    d.pop("sequence_index", None)
    return StepResult.model_validate(d)


def load_disk_run_snapshot(runs_root: Path, run_id: str) -> Optional[DiskRunSnapshot]:
    """Return None if run directory or meta is missing."""
    if not runs_root.is_dir():
        return None
    run_dir = runs_root / run_id
    meta_path = run_dir / "meta.json"
    steps_path = run_dir / "steps.jsonl"
    if not meta_path.is_file():
        return None
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rid = str(raw.get("run_id", run_id))
    started_s = raw.get("started_at")
    if not isinstance(started_s, str):
        return None
    try:
        started_at = datetime.fromisoformat(started_s.replace("Z", "+00:00"))
    except ValueError:
        return None
    meta_raw = raw.get("metadata")
    if isinstance(meta_raw, dict):
        metadata = RunMetadata.model_validate(meta_raw)
    else:
        metadata = RunMetadata()

    steps: list[StepResult] = []
    if steps_path.is_file():
        try:
            with steps_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            steps.append(_parse_step_line(obj))
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError:
            pass

    finished_at = started_at
    try:
        st = steps_path.stat()
        finished_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    except OSError:
        if steps:
            finished_at = started_at

    status = derive_run_lifecycle_status(steps)
    summary = {
        "step_count": len(steps),
        "failed": sum(1 for s in steps if s.status == StepExecutionStatus.FAILED),
        "skipped": sum(1 for s in steps if s.status == StepExecutionStatus.SKIPPED),
    }
    return DiskRunSnapshot(
        run_id=rid,
        started_at=started_at,
        finished_at=finished_at,
        metadata=metadata,
        steps=steps,
        summary=summary,
        status=status.value,
    )


def list_disk_run_rows(runs_root: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Recent runs for history table (newest first)."""
    if not runs_root.is_dir():
        return []
    rows: list[tuple[datetime, dict[str, Any]]] = []
    for sub in runs_root.iterdir():
        if not sub.is_dir():
            continue
        mp = sub / "meta.json"
        if not mp.is_file():
            continue
        try:
            raw = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        started_s = raw.get("started_at")
        if not isinstance(started_s, str):
            continue
        try:
            dt = datetime.fromisoformat(started_s.replace("Z", "+00:00"))
        except ValueError:
            continue
        rid = str(raw.get("run_id", sub.name))
        md = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        ext = md.get("extensions") if isinstance(md.get("extensions"), dict) else {}
        application = str(ext.get("application", "") or "")
        environment = str(md.get("environment") or ext.get("environment") or "")
        ex = md.get("executor") if isinstance(md.get("executor"), dict) else {}
        fk = ex.get("flow_keys") if isinstance(ex.get("flow_keys"), list) else []
        flow = str(fk[0]) if fk else str(ext.get("flow", "") or "")
        steps_path = sub / "steps.jsonl"
        status = "unknown"
        if steps_path.is_file():
            snap = load_disk_run_snapshot(runs_root, sub.name)
            if snap:
                status = snap.status
        rows.append(
            (
                dt,
                {
                    "run_id": rid,
                    "application": application or "—",
                    "environment": environment or "—",
                    "flow": flow or "—",
                    "status": status,
                    "started_at": started_s,
                },
            )
        )
    rows.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in rows[:limit]]
