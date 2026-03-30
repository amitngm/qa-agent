"""Optional bridge from the QA execution layer to the flow engine."""

from __future__ import annotations

import time
from typing import Any, List, Optional, Sequence

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.flows.engine import FlowEngine
from qa_agent.flows.registry import FlowRegistry
from qa_agent.flows.types import FlowContext
from qa_agent.layers.base import ExecutionLayer


class FlowEngineExecutionLayer(ExecutionLayer):
    """
    Runs one or more registered flows inside the execution layer.

    Drive via run context metadata, e.g. ``metadata.executor.flow_keys = ["smoke", "regression"]``
    (JSON: ``{"executor": {"flow_keys": ["smoke", "regression"]}}``).
    """

    def __init__(
        self,
        registry: FlowRegistry,
        *,
        engine: Optional[FlowEngine] = None,
        metadata_key: str = "flow_keys",
    ) -> None:
        super().__init__(name="FlowEngineExecutionLayer")
        self._registry = registry
        self._engine = engine or FlowEngine()
        self._metadata_key = metadata_key

    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        start = time.perf_counter()
        ex = context.metadata.executor
        raw = None
        if ex is not None:
            raw = getattr(ex, self._metadata_key, None)
        if raw is None:
            raw = context.metadata_as_dict().get(self._metadata_key)
        keys = self._normalize_keys(raw)
        if not keys:
            duration_ms = (time.perf_counter() - start) * 1000
            return StepResult(
                layer="execution",
                name=self.name,
                status=StepStatus.SUCCEEDED,
                duration_ms=duration_ms,
                detail={"flows_run": 0, "reason": "no_flow_keys"},
            )

        results: List[Any] = []
        failures = 0
        for key in keys:
            flow = self._registry.require(key)
            fctx = FlowContext(parent_run_id=context.run_id)
            fr = self._engine.run(flow, fctx, config, parent_run_id=context.run_id)
            results.append(fr.model_dump(mode="json"))
            if not fr.ok:
                failures += 1
                if config.orchestration.stop_on_first_failure:
                    break

        context.merge_metadata({"executor": {"flow_engine_results": results}})
        duration_ms = (time.perf_counter() - start) * 1000
        status = StepStatus.FAILED if failures else StepStatus.SUCCEEDED
        return StepResult(
            layer="execution",
            name=self.name,
            status=status,
            duration_ms=duration_ms,
            detail={
                "flows_run": len(results),
                "flows_failed": failures,
                "flow_keys": list(keys),
            },
        )

    @staticmethod
    def _normalize_keys(raw: Any) -> Sequence[str]:
        if raw is None:
            return ()
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, (list, tuple)):
            return tuple(str(x) for x in raw)
        return (str(raw),)
