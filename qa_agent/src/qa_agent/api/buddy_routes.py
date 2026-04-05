"""TestBuddy API routes — chat, approval, history, audit."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from qa_agent.buddy.audit import AuditLog
from qa_agent.buddy.brain import Brain
from qa_agent.buddy.default_registry import build_default_registry
from qa_agent.buddy.domain.platform import PlatformDomain
from qa_agent.buddy.intent.router import IntentRouter
from qa_agent.buddy.permission import PermissionEngine
from qa_agent.buddy.providers.factory import build_provider
from qa_agent.buddy.reasoning.prompts import PromptLibrary
from qa_agent.buddy.recovery import RecoveryEngine
from qa_agent.buddy.session import SessionStore

log = logging.getLogger("qa_agent.buddy.routes")

router = APIRouter(prefix="/buddy", tags=["buddy"])

# ── Singletons ─────────────────────────────────────────────────────────────
_registry = build_default_registry()
_permission = PermissionEngine()
_recovery = RecoveryEngine()
_audit = AuditLog()
_sessions = SessionStore()
_provider = build_provider()  # reads BUDDY_PROVIDER / BUDDY_MODEL from env
_brain = Brain(
    registry=_registry,
    permission=_permission,
    recovery=_recovery,
    audit=_audit,
    provider=_provider,
)
# Intent router — LLM fallback enabled (uses same provider)
_intent_router = IntentRouter(provider=_provider)


# ── Schemas ─────────────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    user: str = "user"
    role: str = "tester"  # viewer / tester / operator / admin


class ChatRequest(BaseModel):
    message: str


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str = ""


# ── Session management ───────────────────────────────────────────────────────

@router.post("/sessions")
def create_session(req: CreateSessionRequest):
    session = _sessions.create(user=req.user, role=req.role)
    return {
        "session_id": session.session_id,
        "user": session.user,
        "role": session.role,
        "created_at": session.created_at.isoformat(),
    }


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.session_id,
        "user": session.user,
        "role": session.role,
        "message_count": len(session.messages),
        "has_pending_approval": session.pending_approval is not None,
        "pending_approval": (
            {
                "tool_name": session.pending_approval.tool_name,
                "params": session.pending_approval.params,
                "risk_level": session.pending_approval.risk_level,
                "description": session.pending_approval.description,
                "snap_id": session.pending_approval.snap_id,
            }
            if session.pending_approval else None
        ),
    }


@router.get("/sessions/{session_id}/history")
def get_history(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # Return only text messages for UI display
    history = []
    for msg in session.messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            history.append({"role": role, "content": content})
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    history.append({"role": role, "content": block["text"]})
    return {"history": history}


# ── Chat ─────────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/chat")
def chat(session_id: str, req: ChatRequest):
    """
    Send a message and get a streaming SSE response.
    Event types: text | tool_call | tool_result | approval_required | error
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.pending_approval:
        raise HTTPException(
            status_code=409,
            detail="Action pending approval. Use POST /approve or /deny first.",
        )

    # ── Intent classification + prompt selection ──────────────────────────────
    intent_result = _intent_router.classify(req.message)
    session.intent = intent_result.intent
    session.intent_confidence = intent_result.confidence

    # Enrich prompt with domain knowledge for the detected feature
    domain_ctx = ""
    if intent_result.primary_feature != "unknown":
        domain_ctx = PlatformDomain.domain_context(intent_result.primary_feature)

    system_prompt = PromptLibrary.build(
        intent=intent_result.intent,
        **intent_result.to_prompt_vars(),
        rag_context=domain_ctx,   # domain knowledge acts as RAG until full RAG is wired
    )

    log.info(
        "intent=%s confidence=%s features=%s urgency=%s",
        intent_result.intent,
        intent_result.confidence,
        intent_result.features,
        intent_result.urgency,
    )

    def event_stream():
        import queue as _queue
        q: _queue.Queue = _queue.Queue()

        def _run():
            try:
                # Emit intent event so UI can show what mode buddy is in
                q.put(f"data: {json.dumps({'type': 'intent', 'intent': intent_result.intent, 'confidence': intent_result.confidence, 'features': intent_result.features})}\n\n")
                for event in _brain.chat(session, req.message, system_prompt_override=system_prompt):
                    q.put(f"data: {json.dumps(event)}\n\n")
            except Exception as exc:
                log.exception("chat stream error")
                q.put(f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n")
            finally:
                q.put(None)  # sentinel

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        while True:
            try:
                chunk = q.get(timeout=15)
            except _queue.Empty:
                # Send SSE comment to keep connection alive during slow inference
                yield ": keepalive\n\n"
                continue
            if chunk is None:
                break
            yield chunk
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Approval ─────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/approve")
def approve_action(session_id: str, req: ApprovalRequest):
    """Approve or deny a pending action, then stream the resumed conversation."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.pending_approval:
        raise HTTPException(status_code=409, detail="No pending approval")

    def event_stream():
        import queue as _queue
        q: _queue.Queue = _queue.Queue()

        def _run():
            try:
                for event in _brain.resume_after_approval(session, req.approved):
                    if event.get("type") == "text" and not event.get("content"):
                        continue
                    q.put(f"data: {json.dumps(event)}\n\n")
            except Exception as exc:
                log.exception("approval resume error")
                q.put(f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n")
            finally:
                q.put(None)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        while True:
            try:
                chunk = q.get(timeout=15)
            except _queue.Empty:
                yield ": keepalive\n\n"
                continue
            if chunk is None:
                break
            yield chunk
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Audit ─────────────────────────────────────────────────────────────────────

@router.get("/audit")
def get_audit(n: Annotated[int, Query(ge=1, le=500)] = 50):
    return {"entries": _audit.recent(n)}


# ── Snapshots ─────────────────────────────────────────────────────────────────

@router.get("/snapshots")
def list_snapshots():
    return {"snapshots": _recovery.recent_snapshots()}


@router.get("/snapshots/{snap_id}")
def get_snapshot(snap_id: str):
    snap = _recovery.load(snap_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap


# ── Tools info ───────────────────────────────────────────────────────────────

@router.get("/tools")
def list_tools():
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "risk_level": t.risk_level.value,
            }
            for t in _registry.all_tools()
        ]
    }
