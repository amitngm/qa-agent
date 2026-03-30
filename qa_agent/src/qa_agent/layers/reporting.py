"""Reporting layer — generator + dispatcher composition."""

from __future__ import annotations

import time
from typing import Any, Optional

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.layers.base import ReportingLayer
from qa_agent.reporting.builder import build_interim_run_result
from qa_agent.reporting.dispatcher import ReportDispatcher
from qa_agent.reporting.generator import ReportGenerator
from qa_agent.reporting.schema import QaReport
from qa_agent.store.protocol import RunStore


class DefaultReporting(ReportingLayer):
    """
    Uses :class:`ReportGenerator` to build structured output and
    :class:`ReportDispatcher` for sink fan-out.

    Sinks are registered on the dispatcher (e.g. via constructor injection for tests).
    The orchestrator sets :attr:`RunContext.pipeline_steps` immediately before each stage's
    ``run()`` to the steps completed so far (excluding the stage about to run), so interim
    reports match actual execution progress. This layer builds an interim
    :class:`~qa_agent.core.types.RunResult` from that snapshot, generates a
    :class:`~qa_agent.reporting.schema.QaReport`, and dispatches
    JSON using :attr:`AgentConfig.severity_routing`.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        *,
        generator: Optional[ReportGenerator] = None,
        dispatcher: Optional[ReportDispatcher] = None,
    ) -> None:
        super().__init__(name=name)
        self._generator = generator or ReportGenerator()
        self._dispatcher = dispatcher or ReportDispatcher()

    @property
    def dispatcher(self) -> ReportDispatcher:
        return self._dispatcher

    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        start = time.perf_counter()
        sink_cfg = dict(config.plugins.reporting_sink)
        context.merge_metadata(
            {
                "reporter": {
                    "reporting": {
                        "sink": sink_cfg,
                        "generator": "ReportGenerator",
                        "dispatcher": "ReportDispatcher",
                    }
                }
            }
        )

        report: Optional[QaReport] = None
        err: Optional[str] = None
        try:
            interim_run = build_interim_run_result(context)
            report = self._generator.generate(
                interim_run,
                context=context,
                agent_config=config,
            )
            self._dispatcher.dispatch_json(
                report,
                routing=config.severity_routing,
            )
            store = context.run_store
            if isinstance(store, RunStore):
                store.put_extra("qa_report", report.model_dump(mode="json"))
        except Exception as ex:
            err = str(ex)

        duration_ms = (time.perf_counter() - start) * 1000
        if err is not None:
            return StepResult(
                layer="reporting",
                name=self.name,
                status=StepStatus.FAILED,
                duration_ms=duration_ms,
                detail={
                    "format": sink_cfg.get("format", "structured"),
                    "pattern": "generator_dispatcher",
                    "error": err,
                },
                errors=[err],
            )

        assert report is not None
        detail: dict[str, Any] = {
            "format": sink_cfg.get("format", "structured"),
            "pattern": "generator_dispatcher",
            "qa_report_schema_version": report.schema_version,
            "qa_report_severity": report.severity,
        }
        return StepResult(
            layer="reporting",
            name=self.name,
            status=StepStatus.SUCCEEDED,
            duration_ms=duration_ms,
            detail=detail,
        )
