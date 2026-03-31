"""Config-driven flows — load a YAML step definition and execute via PlatformDriver."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from qa_agent.config.settings import AgentConfig
from qa_agent.flows.base import BaseFlow
from qa_agent.flows.types import (
    FailureClassification,
    FailureSignal,
    FlowContext,
    FlowEngineOutcome,
    FlowPhase,
    FlowPhaseResult,
    FlowStepOutcome,
    PhaseOutcome,
)
from qa_agent.platform.playwright_driver import PlaywrightPlatformDriver

logger = logging.getLogger(__name__)


def _flows_dir() -> Path:
    env = os.environ.get("QA_AGENT_CONFIG_PATH")
    if env:
        return Path(env).expanduser().resolve().parent / "flows"
    here = Path(__file__).resolve()
    return here.parents[3] / "config" / "flows"


def _substitute(value: Any, vars: Dict[str, str]) -> Any:
    if isinstance(value, str):
        for k, v in vars.items():
            value = value.replace(f"{{{{{k}}}}}", v)
        return value
    if isinstance(value, dict):
        return {k: _substitute(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(i, vars) for i in value]
    return value


def _run_config_step(
    driver: PlaywrightPlatformDriver,
    step: Dict[str, Any],
    vars: Dict[str, str],
    page: Any,
) -> tuple[bool, str, List[str]]:
    """Run one config step. Returns (ok, detail, errors)."""
    step = _substitute(step, vars)
    op = (step.get("op") or "").lower()
    label = step.get("label") or step.get("key") or op
    errors: List[str] = []

    try:
        if op == "navigate":
            url = step.get("url") or step.get("goto") or ""
            if not url:
                return False, label, ["navigate step missing url"]
            res = driver.navigate({
                "url": url,
                "wait_until": step.get("wait_until", "domcontentloaded"),
                "timeout_ms": step.get("timeout_ms", 30000),
            })
            if not res.ok:
                return False, label, list(res.errors)
            return True, label, []

        if op == "interact":
            params = {k: v for k, v in step.items() if k not in ("op", "key", "label", "optional", "screenshot")}
            res = driver.interact(params)
            if not res.ok:
                return False, label, list(res.errors)
            return True, label, []

        if op == "wait":
            params = {k: v for k, v in step.items() if k not in ("op", "key", "label", "optional", "screenshot")}
            res = driver.wait(params)
            if not res.ok:
                return False, label, list(res.errors)
            return True, label, []

        if op == "read":
            params = {k: v for k, v in step.items() if k not in ("op", "key", "label", "optional", "screenshot")}
            res = driver.read(params)
            if not res.ok:
                return False, label, list(res.errors)
            return True, label, []

        if op == "assert_visible":
            selector = step.get("selector") or ""
            timeout_ms = step.get("timeout_ms", 10000)
            res = driver.read({"selector": selector, "property": "is_visible", "timeout_ms": timeout_ms})
            if not res.ok:
                return False, label, list(res.errors)
            if not res.detail.get("value"):
                return False, label, [f"element not visible: {selector}"]
            return True, label, []

        if op == "assert_text":
            selector = step.get("selector") or ""
            contains = str(step.get("contains") or "")
            timeout_ms = step.get("timeout_ms", 10000)
            res = driver.read({"selector": selector, "property": "inner_text", "timeout_ms": timeout_ms})
            if not res.ok:
                return False, label, list(res.errors)
            text = str(res.detail.get("value") or "")
            if contains and contains.lower() not in text.lower():
                return False, label, [f"expected {contains!r} in {text[:200]!r}"]
            return True, label, []

        if op == "assert_url":
            contains = str(step.get("contains") or "")
            res = driver.read({"evaluate": "() => window.location.href"})
            if not res.ok:
                return False, label, list(res.errors)
            url = str(res.detail.get("value") or "")
            if contains and contains not in url:
                return False, label, [f"URL {url!r} does not contain {contains!r}"]
            return True, label, []

        return False, label, [f"unknown op: {op!r}"]

    except Exception as exc:
        return False, label, [str(exc)]


class ConfigDrivenFlow(BaseFlow):
    """A flow loaded from a YAML file in config/flows/<flow_key>.yaml."""

    def __init__(self, flow_def: Dict[str, Any]) -> None:
        self.flow_key: str = str(flow_def.get("flow_key") or "config_flow")
        self.flow_version: str = str(flow_def.get("flow_version") or "1.0.0")
        self._description: str = str(flow_def.get("description") or "")
        self._steps: List[Dict[str, Any]] = list(flow_def.get("steps") or [])
        self._browser: str = str(flow_def.get("browser") or "chromium")
        self._headless: bool = bool(flow_def.get("headless", True))

    def execute_steps(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult:
        if not self._steps:
            return FlowPhaseResult.ok(FlowPhase.EXECUTE, step_outcomes=[])

        # Build variable map from baggage + config
        ae = {}
        ext = {}
        try:
            ext = dict(config.plugins.model_dump().get("auto_explore_ui") or {})
            baggage_ae = dict(ctx.baggage.get("auto_explore") or {})
            ae = baggage_ae
        except Exception:
            pass

        vars: Dict[str, str] = {
            "base_url": str(ctx.baggage.get("base_url") or ae.get("target_url") or ""),
            "username": str(ctx.baggage.get("username") or ae.get("username") or ""),
            "password": str(ctx.baggage.get("password") or ctx.baggage.get("auto_explore_password") or ""),
            "environment": str(ctx.baggage.get("environment") or ""),
        }
        # Also expose any baggage key directly
        for k, v in ctx.baggage.items():
            if isinstance(v, str) and k not in vars:
                vars[k] = v

        # Get base_url from application profile if available and not already set
        if not vars["base_url"]:
            try:
                from qa_agent.config.application_profiles import load_application_profile_optional
                app_slug = str(ctx.baggage.get("application") or "")
                if app_slug:
                    profile = load_application_profile_optional(app_slug)
                    if profile and profile.base_url:
                        vars["base_url"] = profile.base_url
            except Exception:
                pass

        browser = str(ctx.baggage.get("browser") or self._browser)
        headless_raw = ctx.baggage.get("headless")
        headless = bool(headless_raw) if headless_raw is not None else self._headless

        driver = PlaywrightPlatformDriver(browser=browser, headless=headless, ignore_https_errors=True)
        outcomes: List[FlowStepOutcome] = []

        try:
            driver.start()
            page = driver.get_page()
            for step in self._steps:
                key = str(step.get("key") or step.get("op") or "step")
                optional = bool(step.get("optional", False))
                ok, label, errors = _run_config_step(driver, step, vars, page)
                logger.info(
                    "config_flow %s step=%s label=%r ok=%s errors=%s",
                    self.flow_key, key, label, ok, errors,
                )
                outcome = PhaseOutcome.SUCCEEDED if ok else (PhaseOutcome.SKIPPED if optional else PhaseOutcome.FAILED)
                outcomes.append(FlowStepOutcome(
                    step_key=key,
                    outcome=outcome,
                    detail={"label": label, "op": step.get("op", "")},
                    errors=errors,
                ))
                if not ok and not optional:
                    return FlowPhaseResult(
                        phase=FlowPhase.EXECUTE,
                        outcome=PhaseOutcome.FAILED,
                        errors=errors or [f"step {key!r} failed"],
                        detail={"failed_step": key},
                        step_outcomes=outcomes,
                    )
        except Exception as exc:
            return FlowPhaseResult(
                phase=FlowPhase.EXECUTE,
                outcome=PhaseOutcome.FAILED,
                errors=[str(exc)],
                step_outcomes=outcomes,
            )
        finally:
            try:
                driver.close()
            except Exception:
                pass

        return FlowPhaseResult.ok(FlowPhase.EXECUTE, step_outcomes=outcomes)

    def classify_failure(self, ctx: FlowContext, signal: FailureSignal, config: AgentConfig) -> FailureClassification:
        return FailureClassification(
            category="ui_flow",
            detail={"phase": signal.phase.value, "message": signal.message, "flow_key": self.flow_key},
        )

    def summarize(self, ctx: FlowContext, config: AgentConfig, outcome: FlowEngineOutcome) -> Mapping[str, Any]:
        failed = any(p.outcome == PhaseOutcome.FAILED for p in outcome.phases)
        return {
            "flow_key": self.flow_key,
            "description": self._description,
            "ok": not failed,
            "phase_count": len(outcome.phases),
            "steps_defined": len(self._steps),
        }


def load_config_driven_flows() -> List[ConfigDrivenFlow]:
    """Load all YAML flow definitions from config/flows/."""
    flows_dir = _flows_dir()
    if not flows_dir.is_dir():
        return []
    flows = []
    for path in sorted(flows_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                continue
            flow = ConfigDrivenFlow(raw)
            if flow.flow_key and flow._steps:
                flows.append(flow)
                logger.info("config_driven_flow: loaded flow_key=%s from %s", flow.flow_key, path.name)
        except Exception as exc:
            logger.warning("config_driven_flow: failed to load %s: %s", path, exc)
    return flows
