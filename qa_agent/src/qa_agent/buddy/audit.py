"""Audit log — immutable append-only JSONL record of every tool execution."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from qa_agent.buddy.tool import ToolResult

log = logging.getLogger("qa_agent.buddy.audit")

_DEFAULT_PATH = Path("buddy_audit.jsonl")


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_PATH

    def record(
        self,
        session_id: str,
        user: str,
        tool_name: str,
        params: dict,
        result: ToolResult,
        approved_by: str | None = None,
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user": user,
            "tool": tool_name,
            "params": params,
            "ok": result.ok,
            "error": result.error or None,
            "approved_by": approved_by,
        }
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            log.warning("audit write failed: %s", exc)

    def record_request(
        self,
        session_id: str,
        user: str,
        message: str,
        intent: str,
        intent_confidence: str,
        features: list,
        environment: str,
        urgency: str,
        rag_confidence: float,
        rag_sources: list,
        self_check_decision: str,
        system_prompt_key: str,
    ) -> None:
        """Record one entry per user request — the full reasoning lifecycle."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "request",
            "session_id": session_id,
            "user": user,
            "message_preview": message[:120],
            "intent": intent,
            "intent_confidence": intent_confidence,
            "features": features,
            "environment": environment,
            "urgency": urgency,
            "rag_confidence": rag_confidence,
            "rag_sources": rag_sources,
            "self_check_decision": self_check_decision,
            "system_prompt_key": system_prompt_key,
        }
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            log.warning("audit write failed: %s", exc)

    def recent(self, n: int = 50) -> list[dict]:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            return [json.loads(l) for l in lines[-n:] if l.strip()]
        except (OSError, json.JSONDecodeError):
            return []
