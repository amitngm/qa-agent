"""Session management — per-user conversation state with pending approval support."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class PendingApproval:
    tool_use_id: str
    tool_name: str
    params: dict
    risk_level: str
    description: str          # Human-readable what-will-happen
    snap_id: str | None = None  # Pre-taken snapshot id


@dataclass
class Session:
    session_id: str
    user: str
    role: str                                    # viewer/tester/operator/admin
    messages: list[dict] = field(default_factory=list)    # Anthropic messages format
    pending_approval: PendingApproval | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Intent + RAG metadata (populated per-message by IntentRouter + RAGEngine)
    intent: str = ""                             # last classified intent
    intent_confidence: str = ""                  # HIGH | MEDIUM | LOW
    rag_sources: list[str] = field(default_factory=list)  # knowledge sources used
    rag_confidence: float = 0.0                  # 0.0–1.0 retrieval quality score

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def append_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.touch()

    def append_assistant(self, content: Any) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self.touch()

    def append_tool_result(self, tool_use_id: str, content: str) -> None:
        self.messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
        })
        self.touch()


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, user: str = "user", role: str = "tester") -> Session:
        sid = str(uuid.uuid4())
        session = Session(session_id=sid, user=user, role=role)
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def all_sessions(self) -> list[Session]:
        return list(self._sessions.values())

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
