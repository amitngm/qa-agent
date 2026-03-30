"""Pipeline composition — decoupled from orchestrator execution loop."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Dict, List, Protocol

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, StepResult
from qa_agent.layers.base import LayerProtocol
from qa_agent.plugins.api_validation import run_api_validation
from qa_agent.plugins.data_validation import run_data_validation
from qa_agent.plugins.report_sink import emit_report_sink
from qa_agent.plugins.security_validation import run_security_validation
from qa_agent.plugins.auto_explore_ui import run_auto_explore_ui
from qa_agent.plugins.ui_automation import run_ui_automation


@dataclass(frozen=True)
class PipelineItem:
    """One ordered stage: stable key, cleanup flag, and callable producing a ``StepResult``."""

    key: str
    cleanup: bool
    run: Callable[[], StepResult]


@dataclass
class OrchestratorLayers:
    """Injectable layer implementations for standard pipeline construction."""

    planner: LayerProtocol
    discovery: LayerProtocol
    execution: LayerProtocol
    step_assertions: LayerProtocol
    flow_assertions: LayerProtocol
    analysis: LayerProtocol
    reporting: LayerProtocol

    def __post_init__(self) -> None:
        """Enforce :class:`~qa_agent.layers.base.LayerProtocol` ``name`` so the orchestrator never hits AttributeError."""
        for fname in (
            "planner",
            "discovery",
            "execution",
            "step_assertions",
            "flow_assertions",
            "analysis",
            "reporting",
        ):
            layer = getattr(self, fname)
            if not hasattr(layer, "name"):
                raise TypeError(
                    f"OrchestratorLayers.{fname} must define a non-empty 'name' attribute (LayerProtocol contract)."
                )
            n = getattr(layer, "name", None)
            if not isinstance(n, str) or not n.strip():
                raise TypeError(
                    f"OrchestratorLayers.{fname}.name must be a non-empty str, got {n!r}."
                )


class PipelineComposer(Protocol):
    def compose(self, context: RunContext, config: AgentConfig) -> Sequence[PipelineItem]:
        ...


class StandardPipelineComposer:
    """Default linear pipeline — order comes from :attr:`AgentConfig.pipeline_order`."""

    def __init__(self, layers: OrchestratorLayers) -> None:
        self._layers = layers

    def _enabled(self, config: AgentConfig, key: str) -> bool:
        toggle = config.layers.get(key)
        return toggle.enabled if toggle else True

    def _run_layer(
        self,
        layer: LayerProtocol,
        context: RunContext,
        config: AgentConfig,
        key: str,
    ) -> StepResult:
        from qa_agent.core.status import StepExecutionStatus

        if not self._enabled(config, key):
            return StepResult(
                layer=key,
                name=getattr(layer, "name", key),
                status=StepExecutionStatus.SKIPPED,
                detail={"reason": "layer_disabled"},
            )
        return layer.run(context, config)

    def _layer_item(
        self,
        key: str,
        layer: LayerProtocol,
        context: RunContext,
        config: AgentConfig,
        cleanup_keys: set[str],
    ) -> PipelineItem:
        return PipelineItem(
            key=key,
            cleanup=key in cleanup_keys,
            run=lambda k=key, lyr=layer: self._run_layer(lyr, context, config, k),
        )

    def _build_stage_registry(
        self,
        context: RunContext,
        config: AgentConfig,
        cleanup_keys: set[str],
    ) -> Dict[str, PipelineItem]:
        layers = self._layers
        ck = cleanup_keys

        def layer_item(key: str, layer: LayerProtocol) -> PipelineItem:
            return self._layer_item(key, layer, context, config, ck)

        return {
            "planner": layer_item("planner", layers.planner),
            "discovery": layer_item("discovery", layers.discovery),
            "execution": layer_item("execution", layers.execution),
            "ui_automation": PipelineItem(
                key="ui_automation",
                cleanup="ui_automation" in ck,
                run=lambda: run_ui_automation(context, dict(config.plugins.ui_automation)),
            ),
            "auto_explore_ui": PipelineItem(
                key="auto_explore_ui",
                cleanup="auto_explore_ui" in ck,
                run=lambda: run_auto_explore_ui(context, dict(config.plugins.auto_explore_ui)),
            ),
            "step_assertions": layer_item("step_assertions", layers.step_assertions),
            "flow_assertions": layer_item("flow_assertions", layers.flow_assertions),
            "api_validation": PipelineItem(
                key="api_validation",
                cleanup="api_validation" in ck,
                run=lambda: run_api_validation(context, dict(config.plugins.api_validation)),
            ),
            "data_validation": PipelineItem(
                key="data_validation",
                cleanup="data_validation" in ck,
                run=lambda: run_data_validation(context, dict(config.plugins.data_validation)),
            ),
            "security_validation": PipelineItem(
                key="security_validation",
                cleanup="security_validation" in ck,
                run=lambda: run_security_validation(context, dict(config.plugins.security_validation)),
            ),
            "analysis": layer_item("analysis", layers.analysis),
            "reporting": layer_item("reporting", layers.reporting),
            "report_sink": PipelineItem(
                key="report_sink",
                cleanup="report_sink" in ck,
                run=lambda: emit_report_sink(context, dict(config.plugins.reporting_sink)),
            ),
        }

    def compose(self, context: RunContext, config: AgentConfig) -> List[PipelineItem]:
        cleanup_keys = set(config.orchestration.cleanup_layer_keys)
        registry = self._build_stage_registry(context, config, cleanup_keys)
        out: list[PipelineItem] = []
        for key in config.pipeline_order:
            item = registry.get(key)
            if item is not None:
                out.append(item)
        return out
