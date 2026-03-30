"""FastAPI routes — thin trigger layer over the orchestrator."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from qa_agent.api.run_bootstrap import apply_run_request_to_context
from qa_agent.api.schemas import RunRequest, RunResponse, StepResultResponse
from qa_agent.config.settings import AgentConfig, AppSettings, load_agent_config
from qa_agent.core.orchestrator import QAOrchestrator, default_orchestrator
from qa_agent.core.types import RunResult

router = APIRouter()


def get_settings() -> AppSettings:
    return AppSettings()


def get_agent_config(settings: AppSettings = Depends(get_settings)) -> AgentConfig:
    return load_agent_config(settings)


def get_orchestrator() -> QAOrchestrator:
    return default_orchestrator()


def _to_response(result: RunResult) -> RunResponse:
    return RunResponse(
        run_id=result.run_id,
        status=result.status,
        started_at=result.started_at,
        finished_at=result.finished_at,
        steps=[
            StepResultResponse(
                layer=s.layer,
                name=s.name,
                status=s.status,
                step_id=s.step_id,
                duration_ms=s.duration_ms,
                detail=dict(s.detail),
                errors=list(s.errors),
                failure_mode=s.failure_mode,
            )
            for s in result.steps
        ],
        summary=dict(result.summary),
    )


@router.post("/run", response_model=RunResponse)
async def trigger_run(
    body: Optional[RunRequest] = None,
    orchestrator: QAOrchestrator = Depends(get_orchestrator),
    agent_config: AgentConfig = Depends(get_agent_config),
) -> RunResponse:
    body = body or RunRequest()
    meta = body.metadata
    if body.run_mode != "auto_explore" and agent_config.suite.flow_keys:
        ex = meta.executor
        if ex is None or ex.flow_keys is None:
            meta = meta.merged({"executor": {"flow_keys": list(agent_config.suite.flow_keys)}})
    body = body.model_copy(update={"metadata": meta})
    context, run_config = apply_run_request_to_context(body, agent_config)
    result = await orchestrator.arun(context=context, config=run_config)
    return _to_response(result)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
