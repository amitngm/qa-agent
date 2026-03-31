"""Recorder API — start/stop recording sessions, stream events, save flow YAML."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from qa_agent.paths import TEMPLATES_DIR
from qa_agent.plugins.flow_recorder import RecordingSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/record", tags=["recorder"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# In-process session store (one recorder at a time is fine for dev)
_sessions: dict[str, RecordingSession] = {}


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def recorder_home(request: Request) -> Any:
    return templates.TemplateResponse("recorder.html", {
        "request": request,
        "title": "Flow Recorder",
        "session": None,
    })


# ── Session lifecycle ─────────────────────────────────────────────────────────

@router.post("/start")
def recorder_start(
    flow_name: str = Form("my-recorded-flow"),
    start_url: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    browser: str = Form("chromium"),
) -> JSONResponse:
    if not start_url.strip():
        return JSONResponse({"ok": False, "error": "start_url is required"}, status_code=400)

    session_id = str(uuid.uuid4())[:8]
    session = RecordingSession(
        session_id=session_id,
        flow_name=flow_name.strip() or "recorded-flow",
        start_url=start_url.strip(),
        username=username,
        password=password,
        browser=browser or "chromium",
    )
    _sessions[session_id] = session
    session.start()
    logger.info("recorder: started session %s flow=%s url=%s", session_id, flow_name, start_url)
    return JSONResponse({"ok": True, "session_id": session_id})


@router.post("/{session_id}/stop")
def recorder_stop(session_id: str) -> JSONResponse:
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"ok": False, "error": "session not found"}, status_code=404)
    saved_path = session.stop()
    flow_key = session.flow_name
    steps = len(session.get_events())
    return JSONResponse({
        "ok": True,
        "flow_key": flow_key,
        "saved_path": saved_path,
        "steps_captured": steps,
    })


@router.post("/{session_id}/assert")
def recorder_add_assertion(
    session_id: str,
    kind: str = Form("assert_url"),
    selector: str = Form(""),
    contains: str = Form(""),
    label: str = Form(""),
) -> JSONResponse:
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"ok": False, "error": "session not found"}, status_code=404)
    params: dict[str, Any] = {}
    if selector.strip():
        params["selector"] = selector.strip()
    if contains.strip():
        params["contains"] = contains.strip()
    if label.strip():
        params["label"] = label.strip()
    step = session.add_assertion(kind, params)
    return JSONResponse({"ok": True, "step": step})


@router.get("/{session_id}/events")
def recorder_events(session_id: str) -> JSONResponse:
    """Poll endpoint — returns all captured steps so far."""
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"ok": False, "error": "session not found"}, status_code=404)
    events = session.get_events()
    return JSONResponse({
        "ok": True,
        "status": session.status,
        "steps": events,
        "error": session.error,
    })


@router.get("/{session_id}/yaml", response_class=PlainTextResponse)
def recorder_yaml(session_id: str) -> str:
    session = _sessions.get(session_id)
    if not session:
        return "# session not found"
    return session.to_yaml()


@router.get("/{session_id}/stream")
def recorder_stream(session_id: str) -> StreamingResponse:
    """Server-Sent Events stream — pushes new steps to the browser UI in real time."""
    session = _sessions.get(session_id)

    def event_generator():
        if not session:
            yield "data: " + json.dumps({"error": "session not found"}) + "\n\n"
            return
        sent = 0
        while True:
            events = session.get_events()
            new = events[sent:]
            for ev in new:
                yield "data: " + json.dumps(ev) + "\n\n"
            sent = len(events)
            if session.status in ("stopped", "error"):
                yield "data: " + json.dumps({"__done": True, "status": session.status}) + "\n\n"
                break
            import time
            time.sleep(0.6)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
