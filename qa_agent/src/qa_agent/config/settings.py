"""Load layered YAML config and environment overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from qa_agent.core.status import StepFailureMode
from qa_agent.suite.model import SuiteDefinition

# Default pipeline order (standard linear run). Used as default config and as allow-list for keys.
PIPELINE_STAGE_KEYS: tuple[str, ...] = (
    "planner",
    "discovery",
    "execution",
    "ui_automation",
    "auto_explore_ui",
    "step_assertions",
    "flow_assertions",
    "api_validation",
    "data_validation",
    "security_validation",
    "analysis",
    "reporting",
    "report_sink",
)


class LayerToggle(BaseModel):
    enabled: bool = True


class OrchestrationConfig(BaseModel):
    stop_on_first_failure: bool = False
    default_step_failure_mode: StepFailureMode = StepFailureMode.STOP
    cleanup_layer_keys: list[str] = Field(
        default_factory=lambda: ["reporting", "report_sink"],
        description="Layer or plugin keys treated as cleanup when skipping ahead.",
    )


class FlowEngineConfig(BaseModel):
    """Branching rules when phases fail — no application semantics."""

    skip_execute_after_precheck_failure: bool = True
    skip_validate_after_execute_failure: bool = True
    skip_evidence_after_execute_failure: bool = False
    capture_evidence_after_validate_failure: bool = True


class PluginsConfig(BaseModel):
    ui_automation: dict[str, Any] = Field(default_factory=dict)
    auto_explore_ui: dict[str, Any] = Field(default_factory=dict)
    api_validation: dict[str, Any] = Field(default_factory=dict)
    data_validation: dict[str, Any] = Field(default_factory=dict)
    security_validation: dict[str, Any] = Field(default_factory=dict)
    reporting_sink: dict[str, Any] = Field(default_factory=dict)


class PlannerConfig(BaseModel):
    """Optional planner integration — all fields are framework-generic."""

    prior_run_digest_limit: int = Field(
        0,
        ge=0,
        description="Max prior run digests to expose to the planner via RunHistoryReader; 0 disables.",
    )


class SeverityRoutingConfig(BaseModel):
    """
    Maps report outcomes to routing keys and those keys to report sink ids.

    Sink ids are registered on :class:`~qa_agent.reporting.dispatcher.ReportDispatcher`;
    verdict strings are ``conclusion.verdict`` values from :class:`~qa_agent.reporting.schema.QaReport`.
    """

    verdict_to_severity: dict[str, str] = Field(
        default_factory=lambda: {
            "pass": "low",
            "fail": "high",
            "partial": "medium",
            "unknown": "info",
        },
        description="Maps conclusion.verdict string values to routing severity keys.",
    )
    route_by_severity: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "low": ["default"],
            "medium": ["default"],
            "high": ["default"],
            "info": ["default"],
            "default": ["default"],
        },
        description="Routing key -> ordered sink ids to invoke for that report.",
    )
    unmatched_severity_sink_ids: list[str] = Field(
        default_factory=lambda: ["default"],
        description="Sink ids when report.severity has no entry in route_by_severity.",
    )

    @field_validator("unmatched_severity_sink_ids", mode="before")
    @classmethod
    def _non_empty_unmatched_sink_ids(cls, v: Any) -> Any:
        """An empty list would make severity routing resolve to no sinks; normalize to a safe default."""
        if v == [] or v is None:
            return ["default"]
        return v


class AgentConfig(BaseModel):
    environment: str = "local"
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    runs_storage_root: Optional[str] = Field(
        default=None,
        description="If set, orchestrator uses FileRunStore under this path when no run_store is "
        "injected; prior-run digests attach when the store implements SupportsRunDigestListing "
        "(FileRunStore does) and prior_run_digest_limit > 0.",
    )
    pipeline_order: list[str] = Field(
        default_factory=lambda: list(PIPELINE_STAGE_KEYS),
        description="Ordered pipeline stage keys; unknown keys are dropped; empty falls back to default order.",
    )
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    flow_engine: FlowEngineConfig = Field(default_factory=FlowEngineConfig)
    suite: SuiteDefinition = Field(default_factory=SuiteDefinition)
    severity_routing: SeverityRoutingConfig = Field(default_factory=SeverityRoutingConfig)
    layers: dict[str, LayerToggle] = Field(
        default_factory=lambda: {
            "planner": LayerToggle(),
            "discovery": LayerToggle(),
            "execution": LayerToggle(),
            "step_assertions": LayerToggle(),
            "flow_assertions": LayerToggle(),
            "analysis": LayerToggle(),
            "reporting": LayerToggle(),
        }
    )
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)

    @field_validator("pipeline_order", mode="after")
    @classmethod
    def _normalize_pipeline_order(cls, v: list[str]) -> list[str]:
        allowed = frozenset(PIPELINE_STAGE_KEYS)
        seen: set[str] = set()
        out: list[str] = []
        for k in v:
            if k not in allowed or k in seen:
                continue
            seen.add(k)
            out.append(k)
        return out if out else list(PIPELINE_STAGE_KEYS)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QA_AGENT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    config_path: Optional[str] = Field(
        default=None,
        description="Path to YAML config file; falls back to packaged default.",
    )


def _default_config_path() -> Path:
    env = os.environ.get("QA_AGENT_CONFIG_PATH")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    # Editable / source layout: repo_root/config/default.yaml
    src_layout = here.parents[3] / "config" / "default.yaml"
    if src_layout.is_file():
        return src_layout
    cwd_layout = Path.cwd() / "config" / "default.yaml"
    if cwd_layout.is_file():
        return cwd_layout
    return src_layout


def load_agent_config(settings: Optional[AppSettings] = None) -> AgentConfig:
    settings = settings or AppSettings()
    path = Path(settings.config_path).expanduser() if settings.config_path else _default_config_path()
    if not path.is_file():
        return AgentConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AgentConfig.model_validate(raw)
