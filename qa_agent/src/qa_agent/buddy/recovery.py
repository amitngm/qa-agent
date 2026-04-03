"""Recovery engine — snapshot state before writes, restore on rollback."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from qa_agent.buddy.tool import ToolResult

log = logging.getLogger("qa_agent.buddy.recovery")

_SNAPSHOT_DIR = Path("buddy_snapshots")


class RecoveryEngine:
    def __init__(self, snapshot_dir: Path | None = None) -> None:
        self._dir = snapshot_dir or _SNAPSHOT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self, tool_name: str, params: dict, state: dict) -> str:
        """Save current state before a write. Returns snapshot_id."""
        snap_id = f"snap_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        payload = {
            "snap_id": snap_id,
            "tool_name": tool_name,
            "params": params,
            "state": state,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._dir / f"{snap_id}.json"
        path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
        log.info("snapshot saved: %s", snap_id)
        return snap_id

    # ------------------------------------------------------------------
    # Load / list
    # ------------------------------------------------------------------

    def load(self, snap_id: str) -> dict | None:
        path = self._dir / f"{snap_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def recent_snapshots(self, n: int = 20) -> list[dict]:
        snaps = []
        for p in sorted(self._dir.glob("snap_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:n]:
            try:
                snaps.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        return snaps

    # ------------------------------------------------------------------
    # Rollback helpers (tool-specific rollback is implemented in each tool)
    # ------------------------------------------------------------------

    def rollback_info(self, snap_id: str) -> ToolResult:
        snap = self.load(snap_id)
        if not snap:
            return ToolResult(ok=False, error=f"snapshot {snap_id} not found")
        return ToolResult(
            ok=True,
            data={
                "snap_id": snap_id,
                "tool": snap["tool_name"],
                "params": snap["params"],
                "state": snap["state"],
                "created_at": snap["created_at"],
            },
        )
