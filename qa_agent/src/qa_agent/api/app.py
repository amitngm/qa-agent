"""ASGI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from qa_agent.api.routes import router as api_router
from qa_agent.api.ui_routes import router as ui_router
from qa_agent.paths import STATIC_DIR

_qa_agent_logging_configured = False


def _configure_qa_agent_logging() -> None:
    """
    Uvicorn often leaves the root logger at WARNING, which hides INFO from ``qa_agent.*``
    (e.g. Auto Explore / Playwright path). Attach one INFO stream handler to the package
    logger so execution diagnostics are visible without extra CLI flags.
    """
    global _qa_agent_logging_configured
    if _qa_agent_logging_configured:
        return
    _qa_agent_logging_configured = True
    log = logging.getLogger("qa_agent")
    log.setLevel(logging.INFO)
    if log.handlers:
        return
    h = logging.StreamHandler()
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    log.addHandler(h)
    log.propagate = False


def create_app() -> FastAPI:
    _configure_qa_agent_logging()
    app = FastAPI(
        title="QA Agent API",
        version="0.1.0",
        description="Trigger layer for generic QA orchestration runs.",
    )
    app.include_router(api_router)
    app.include_router(ui_router)
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
