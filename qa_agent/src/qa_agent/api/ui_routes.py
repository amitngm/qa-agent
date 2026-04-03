"""Minimal HTML UI (Jinja2) over the QA agent orchestrator."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from qa_agent.api.run_bootstrap import apply_run_request_to_context
from qa_agent.api.run_disk import list_disk_run_rows, load_disk_run_snapshot
from qa_agent.api.schemas import AutoExploreRequest, RunRequest
from qa_agent.config.settings import AgentConfig, AppSettings, load_agent_config
from qa_agent.core.orchestrator import QAOrchestrator, default_orchestrator
from qa_agent.core.run_metadata import ExecutorMetadata, RunMetadata
from qa_agent.core.status import RunLifecycleStatus
from qa_agent.core.types import RunContext, RunResult
from qa_agent.paths import TEMPLATES_DIR
from qa_agent.reporting.builder import build_report
router = APIRouter(prefix="/ui", tags=["ui"])

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Ephemeral cache for the last run when runs_storage_root is unset (dev-only convenience).
_ui_run_cache: dict[str, tuple[RunResult, RunMetadata]] = {}


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(microsecond=0).isoformat()
        except ValueError:
            return value
    return str(value)


templates.env.filters["fmt_dt"] = _fmt_dt
templates.env.filters["tojson"] = lambda v: json.dumps(v, indent=2, default=str)


def get_settings() -> AppSettings:
    return AppSettings()


def get_agent_config(settings: AppSettings = Depends(get_settings)) -> AgentConfig:
    return load_agent_config(settings)


def get_orchestrator() -> QAOrchestrator:
    return default_orchestrator()


def _runs_root(config: AgentConfig) -> Optional[Path]:
    if not config.runs_storage_root:
        return None
    return Path(config.runs_storage_root).expanduser().resolve()


def _extensions_dict(metadata: Optional[RunMetadata]) -> dict[str, Any]:
    """Normalize ``RunMetadata.extensions`` to a dict (Pydantic may use a non-``dict`` mapping)."""
    if metadata is None:
        return {}
    ext = metadata.extensions
    if ext is None:
        return {}
    if isinstance(ext, dict):
        return ext
    try:
        return dict(ext)
    except Exception:
        return {}


def _flow_label(metadata: Optional[RunMetadata]) -> str:
    if metadata is None:
        return "—"
    ex = metadata.executor
    if ex is not None and ex.flow_keys:
        return str(ex.flow_keys[0])
    ext = _extensions_dict(metadata)
    if ext.get("flow"):
        return str(ext["flow"])
    return "—"


def _elapsed_seconds(result: RunResult) -> Optional[float]:
    try:
        delta = result.finished_at - result.started_at
        return round(delta.total_seconds(), 1)
    except Exception:
        return None


def _passed_count(result: RunResult) -> Optional[int]:
    total = result.summary.get("step_count")
    failed = int(result.summary.get("failed", 0) or 0)
    skipped = int(result.summary.get("skipped", 0) or 0)
    if isinstance(total, int):
        return max(0, total - failed - skipped)
    return None


def _report_timing(report_dict: dict[str, Any]) -> dict[str, Any]:
    """Wall clock and summed step durations for the report UI."""
    out: dict[str, Any] = {"wall_seconds": None, "step_duration_ms_total": None, "step_rows": []}
    run_block = report_dict.get("run") or {}
    started_s = run_block.get("started_at")
    finished_s = run_block.get("finished_at")
    try:
        if isinstance(started_s, str) and isinstance(finished_s, str):
            sa = datetime.fromisoformat(started_s.replace("Z", "+00:00"))
            fb = datetime.fromisoformat(finished_s.replace("Z", "+00:00"))
            out["wall_seconds"] = round((fb - sa).total_seconds(), 2)
    except (TypeError, ValueError):
        pass
    steps = report_dict.get("steps") or []
    total_ms = 0.0
    rows: list[dict[str, Any]] = []
    for s in steps:
        dm = s.get("duration_ms")
        if dm is not None:
            try:
                total_ms += float(dm)
            except (TypeError, ValueError):
                pass
        rows.append(
            {
                "index": s.get("index"),
                "layer": s.get("layer"),
                "name": s.get("name"),
                "duration_ms": dm,
            }
        )
    out["step_duration_ms_total"] = round(total_ms, 2) if total_ms else None
    out["step_rows"] = rows
    return out


def _run_result_from_snapshot(snap: Any) -> RunResult:
    return RunResult(
        run_id=snap.run_id,
        status=RunLifecycleStatus(snap.status),
        started_at=snap.started_at,
        finished_at=snap.finished_at,
        steps=snap.steps,
        summary=dict(snap.summary),
    )


def _resolve_run(
    run_id: str,
    agent_config: AgentConfig,
) -> tuple[Optional[RunResult], Optional[RunMetadata], Optional[str]]:
    """
    Load a completed run from the file store (if configured) or the in-process UI cache.

    Returns (result, metadata, error). ``error`` is None when the run was found.
    """
    root = _runs_root(agent_config)
    if root is not None and root.is_dir():
        snap = load_disk_run_snapshot(root, run_id)
        if snap is not None:
            return _run_result_from_snapshot(snap), snap.metadata, None
    if run_id in _ui_run_cache:
        r, m = _ui_run_cache[run_id]
        return r, m, None
    return (
        None,
        None,
        "Run not found. Check the run id, or start a new run. "
        "Runs only persist across restarts when runs_storage_root is set in configuration.",
    )


@router.get("/")
def ui_home() -> RedirectResponse:
    """Landing: send users to the run form."""
    return RedirectResponse(url="/ui/run", status_code=302)


def _all_flow_keys() -> list[str]:
    """Return all registered flow keys (built-in + config-driven)."""
    try:
        from qa_agent.flows.default_registry import default_flow_registry
        reg = default_flow_registry()
        return list(reg.keys())
    except Exception:
        return ["generic_crud_lifecycle", "linear_two_step", "noop"]


@router.get("/run")
def ui_run_form(request: Request, agent_config: AgentConfig = Depends(get_agent_config)) -> Any:
    return templates.TemplateResponse(
        "run_form.html",
        {
            "request": request,
            "title": "Run QA",
            "default_environment": agent_config.environment,
            "suite_flow_keys": list(agent_config.suite.flow_keys),
            "all_flow_keys": _all_flow_keys(),
            "form_error": None,
            "form_values": None,
        },
    )


def _form_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@router.post("/run")
async def ui_run_submit(
    request: Request,
    run_mode: str = Form("known_flow"),
    application: str = Form(""),
    environment: str = Form(""),
    flow: str = Form(""),
    run_type: str = Form(""),
    role_profile: str = Form(""),
    target_url: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    login_strategy: str = Form("auto_detect"),
    max_pages: int = Form(10),
    safe_mode: str = Form("true"),
    headless: str = Form("true"),
    username_selector: str = Form(""),
    password_selector: str = Form(""),
    login_button_selector: str = Form(""),
    success_marker: str = Form(""),
    explore_mode: str = Form("full"),
    selected_features: str = Form(""),
    kf_base_url: str = Form(""),
    kf_username: str = Form(""),
    kf_password: str = Form(""),
    kf_application: str = Form(""),
    orchestrator: QAOrchestrator = Depends(get_orchestrator),
    agent_config: AgentConfig = Depends(get_agent_config),
) -> Any:
    from urllib.parse import quote

    if run_mode not in ("known_flow", "auto_explore"):
        run_mode = "known_flow"

    # Footgun: form defaults to Known flow; users often fill Target URL + credentials but forget the radio.
    tu_quick = target_url.strip()
    if run_mode == "known_flow" and tu_quick.startswith(("http://", "https://")):
        logger.info(
            "ui/run: using auto_explore because target_url is set (http(s)) while Known flow was selected; "
            "choose the Auto explore radio explicitly if you prefer."
        )
        run_mode = "auto_explore"

    ext: dict[str, Any] = {}
    if application.strip():
        ext["application"] = application.strip()
    if run_type.strip():
        ext["run_type"] = run_type.strip()
    if role_profile.strip():
        ext["role_profile"] = role_profile.strip()
    # Known flow credentials (config-driven flows) — stored in extensions, password handled in bootstrap
    if kf_base_url.strip():
        ext["kf_base_url"] = kf_base_url.strip()
    if kf_username.strip():
        ext["kf_username"] = kf_username.strip()
    if kf_password.strip():
        ext["kf_password"] = kf_password.strip()
    if kf_application.strip():
        ext["kf_application"] = kf_application.strip()

    def _error_response(msg: str, fv: dict[str, Any]) -> Any:
        return templates.TemplateResponse(
            "run_form.html",
            {
                "request": request,
                "title": "Run QA",
                "default_environment": agent_config.environment,
                "suite_flow_keys": list(agent_config.suite.flow_keys),
                "all_flow_keys": _all_flow_keys(),
                "form_error": msg,
                "form_values": fv,
            },
            status_code=200,
        )

    try:
        if run_mode == "auto_explore":
            ls_raw = login_strategy.strip().lower()
            ls_cast: Literal["auto_detect", "manual_hints"] = (
                "manual_hints" if ls_raw == "manual_hints" else "auto_detect"
            )
            try:
                em_raw = explore_mode.strip().lower()
                em_use = cast(Literal["full", "selective"], "selective" if em_raw == "selective" else "full")
                ae = AutoExploreRequest(
                    target_url=target_url.strip(),
                    application=application.strip() or None,
                    username=username,
                    password=password,
                    login_strategy=ls_cast,
                    max_pages=max_pages,
                    safe_mode=_form_bool(safe_mode),
                    headless=_form_bool(headless),
                    explore_mode=em_use,
                    selected_features=selected_features,
                    username_selector=username_selector.strip() or None,
                    password_selector=password_selector.strip() or None,
                    login_button_selector=login_button_selector.strip() or None,
                    success_marker=success_marker.strip() or None,
                )
            except ValidationError as ve:
                return _error_response(
                    "Auto explore: " + str(ve.errors()[0].get("msg", ve.errors()[0])),
                    {
                        "run_mode": "auto_explore",
                        "application": application,
                        "environment": environment,
                        "flow": flow,
                        "run_type": run_type,
                        "role_profile": role_profile,
                        "target_url": target_url,
                        "username": username,
                        "login_strategy": login_strategy,
                        "max_pages": max_pages,
                        "safe_mode": safe_mode,
                        "headless": headless,
                        "username_selector": username_selector,
                        "password_selector": password_selector,
                        "login_button_selector": login_button_selector,
                        "success_marker": success_marker,
                        "explore_mode": explore_mode,
                        "selected_features": selected_features,
                        "kf_base_url": kf_base_url,
                        "kf_username": kf_username,
                        "kf_application": kf_application,
                    },
                )
            meta = RunMetadata(
                environment=environment.strip() or None,
                extensions=ext,
            )
            req = RunRequest(run_mode="auto_explore", metadata=meta, auto_explore=ae)
        else:
            executor = ExecutorMetadata(flow_keys=[flow.strip()]) if flow.strip() else None
            meta = RunMetadata(
                environment=environment.strip() or None,
                executor=executor,
                extensions=ext,
            )
            if agent_config.suite.flow_keys and (meta.executor is None or not meta.executor.flow_keys):
                meta = meta.merged({"executor": {"flow_keys": list(agent_config.suite.flow_keys)}})
            req = RunRequest(run_mode="known_flow", metadata=meta)

        context, run_config = apply_run_request_to_context(req, agent_config)
        result = await orchestrator.arun(context=context, config=run_config)
        _ui_run_cache[result.run_id] = (result, context.metadata)
    except Exception as ex:
        return _error_response(
            str(ex),
            {
                "run_mode": run_mode,
                "application": application,
                "environment": environment,
                "flow": flow,
                "run_type": run_type,
                "role_profile": role_profile,
                "target_url": target_url,
                "username": username,
                "login_strategy": login_strategy,
                "max_pages": max_pages,
                "safe_mode": safe_mode,
                "headless": headless,
                "username_selector": username_selector,
                "password_selector": password_selector,
                "login_button_selector": login_button_selector,
                "success_marker": success_marker,
                "explore_mode": explore_mode,
                "selected_features": selected_features,
                "kf_base_url": kf_base_url,
                "kf_username": kf_username,
                "kf_application": kf_application,
            },
        )

    return RedirectResponse(
        url=f"/ui/runs/{quote(result.run_id, safe='')}/status",
        status_code=303,
    )


@router.get("/runs/{run_id}/status")
def ui_run_status(
    request: Request,
    run_id: str,
    agent_config: AgentConfig = Depends(get_agent_config),
) -> Any:
    root = _runs_root(agent_config)
    result, meta, error = _resolve_run(run_id, agent_config)

    last_step = None
    severity = None
    elapsed = None
    passed = None
    recent_rows: list[dict[str, Any]] = []
    flow_label = "—"
    if result:
        for s in reversed(result.steps):
            if s.layer == "reporting" and s.detail:
                severity = s.detail.get("qa_report_severity")
                break
    if result and result.steps:
        last_step = result.steps[-1]
        trim = result.steps[-25:]
        base = len(result.steps) - len(trim)
        for i, s in enumerate(trim):
            recent_rows.append(
                {
                    "seq": base + i,
                    "layer": s.layer,
                    "name": s.name,
                    "status": s.status.value,
                    "duration_ms": s.duration_ms,
                }
            )
        elapsed = _elapsed_seconds(result)
        passed = _passed_count(result)
    auto_explore_summary: Optional[dict[str, Any]] = None
    page_validation_summary: Optional[dict[str, Any]] = None
    run_mode: str = "known_flow"
    if meta is not None:
        flow_label = _flow_label(meta)
        ext_m = _extensions_dict(meta)
        run_mode = str(ext_m.get("run_mode") or "known_flow")
        if run_mode == "auto_explore":
            ae = ext_m.get("auto_explore")
            if isinstance(ae, dict) and ae.get("target_url"):
                tu = str(ae["target_url"])
                flow_label = "auto: " + (tu if len(tu) <= 96 else tu[:93] + "…")
            else:
                flow_label = "auto_explore"
        ex = meta.executor
        if ex is not None and ex.auto_explore_ui is not None:
            a = ex.auto_explore_ui
            auto_explore_summary = {
                "status": a.status,
                "target_url": a.target_url,
                "login_ok": a.login_ok,
                "login_detail": a.login_detail,
                "pages_visited": a.pages_visited,
                "pages_discovered": a.pages_discovered,
                "failed": a.failed,
                "browser": a.browser,
                "headless": a.headless,
                "explore_mode": a.explore_mode,
                "selected_features": list(a.selected_features or []),
                "app_structure_summary": a.app_structure_summary or "",
                "selective_feature_summary": a.selective_feature_summary or "",
                "feature_wise": [fw.model_dump(mode="json") for fw in (a.feature_wise or [])],
            }
        v = meta.validator
        if v is not None and v.page_validation is not None:
            pv = v.page_validation
            page_validation_summary = {
                "status": pv.status,
                "pages_total": pv.pages_total,
                "pages_passed": pv.pages_passed,
                "pages_failed": pv.pages_failed,
                "pages_warned": pv.pages_warned,
                "checks_run": pv.checks_run,
                "checks_passed": pv.checks_passed,
                "failed": pv.failed,
                "features": [f.model_dump(mode="json") for f in (pv.features or [])],
                "untagged_count": len(pv.untagged_pages or []),
                "skip_reason": pv.skip_reason,
            }

    return templates.TemplateResponse(
        "run_status.html",
        {
            "request": request,
            "title": f"Run {run_id}",
            "run_id": run_id,
            "error": error,
            "result": result,
            "metadata": meta,
            "last_step": last_step,
            "severity": severity,
            "has_disk": root is not None and root.is_dir(),
            "flow_label": flow_label,
            "elapsed_seconds": elapsed,
            "passed_count": passed,
            "recent_rows": recent_rows,
            "recent_row_count": len(recent_rows),
            "run_mode": run_mode,
            "auto_explore_summary": auto_explore_summary,
            "page_validation_summary": page_validation_summary,
        },
    )


@router.get("/runs/{run_id}/report")
def ui_run_report(
    request: Request,
    run_id: str,
    agent_config: AgentConfig = Depends(get_agent_config),
) -> Any:
    error: Optional[str] = None
    report_dict: Optional[dict[str, Any]] = None

    rr, meta, resolve_err = _resolve_run(run_id, agent_config)
    if resolve_err:
        error = resolve_err
    elif rr is not None and meta is not None:
        ctx = RunContext(metadata=meta)
        try:
            report = build_report(rr, context=ctx, agent_config=agent_config)
            report_dict = report.model_dump(mode="json")
        except Exception as ex:
            error = f"Report could not be built: {ex}"

    timing: Optional[dict[str, Any]] = None
    if report_dict is not None:
        timing = _report_timing(report_dict)

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "title": f"Report · {run_id}",
            "run_id": run_id,
            "error": error,
            "report": report_dict,
            "timing": timing,
        },
    )


@router.get("/history")
def ui_history(
    request: Request,
    agent_config: AgentConfig = Depends(get_agent_config),
) -> Any:
    root = _runs_root(agent_config)
    rows: list[dict[str, Any]] = []
    empty_reason: Optional[str] = None
    if root is None:
        empty_reason = "Set runs_storage_root in configuration to list runs from the file store."
    elif not root.is_dir():
        empty_reason = f"Configured storage path is not a directory: {root}"
    else:
        rows = list_disk_run_rows(root, limit=50)
        if not rows:
            empty_reason = "No runs yet. Start a run while persistence is enabled to see history here."

    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "title": "Run history",
            "rows": rows,
            "empty_reason": empty_reason,
            "has_disk": root is not None,
        },
    )


@router.get("/buddy")
def ui_buddy(request: Request) -> Any:
    return templates.TemplateResponse("buddy.html", {"request": request})


