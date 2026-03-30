"""Sequential pipeline orchestrator — config-driven layer and plugin execution."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from qa_agent.config.settings import AgentConfig
from qa_agent.core.layer_phase_map import major_layer_for_pipeline_key
from qa_agent.core.pipeline import (
    OrchestratorLayers,
    PipelineComposer,
    StandardPipelineComposer,
)
from qa_agent.core.status import StepExecutionStatus, StepFailureMode
from qa_agent.core.types import RunContext, RunResult, StepResult, derive_run_lifecycle_status
from qa_agent.store.digest_listing import DigestListingRunHistoryReader, SupportsRunDigestListing
from qa_agent.store.file_store import FileRunStore
from qa_agent.store.memory import InMemoryRunStore
from qa_agent.store.protocol import RunStore


class QAOrchestrator:
    def __init__(
        self,
        *,
        layers: OrchestratorLayers,
        composer: Optional[PipelineComposer] = None,
    ) -> None:
        self._layers = layers
        self._composer: PipelineComposer = composer or StandardPipelineComposer(layers)

    def _ensure_store(self, context: RunContext, config: AgentConfig) -> RunStore:
        store = context.run_store
        if store is not None:
            return store
        root = config.runs_storage_root
        if root:
            store = FileRunStore(Path(root))
            context.run_store = store
            return store
        store = InMemoryRunStore()
        context.run_store = store
        return store

    @staticmethod
    def _maybe_attach_run_history_reader(
        context: RunContext,
        config: AgentConfig,
        store: RunStore,
    ) -> None:
        if context.run_history_reader is not None:
            return
        if config.planner.prior_run_digest_limit <= 0:
            return
        if isinstance(store, SupportsRunDigestListing):
            context.run_history_reader = DigestListingRunHistoryReader(
                store,
                exclude_run_id=context.run_id,
            )

    @staticmethod
    def _merge_suite_flow_keys_if_needed(context: RunContext, config: AgentConfig) -> None:
        """When executor metadata omits ``flow_keys``, inherit :attr:`AgentConfig.suite.flow_keys`."""
        if not config.suite.flow_keys:
            return
        ex = context.metadata.executor
        if ex is not None and ex.flow_keys:
            return
        context.merge_metadata({"executor": {"flow_keys": list(config.suite.flow_keys)}})

    @staticmethod
    def _effective_failure_mode(step: StepResult, config: AgentConfig) -> StepFailureMode:
        if step.status != StepExecutionStatus.FAILED:
            return StepFailureMode.CONTINUE
        if step.failure_mode is not None:
            return step.failure_mode
        if config.orchestration.stop_on_first_failure:
            return StepFailureMode.STOP
        return config.orchestration.default_step_failure_mode

    @staticmethod
    def _record_layer_timing(
        store: RunStore,
        *,
        pipeline_key: str,
        sequence_index: int,
        step: StepResult,
    ) -> None:
        phase = major_layer_for_pipeline_key(pipeline_key)
        if phase is None:
            return
        duration_ms = step.duration_ms if step.duration_ms is not None else 0.0
        store.record_layer_timing(
            phase,
            duration_ms,
            pipeline_key=pipeline_key,
            sequence_index=sequence_index,
            detail={
                "step_status": step.status.value,
                "layer": step.layer,
                "name": step.name,
            },
        )

    def _execute_pipeline(self, context: RunContext, config: AgentConfig) -> RunResult:
        """Synchronous pipeline body (blocking I/O safe to run via :meth:`arun` in a worker thread)."""
        store = self._ensure_store(context, config)
        self._maybe_attach_run_history_reader(context, config, store)
        self._merge_suite_flow_keys_if_needed(context, config)
        store.open_run(context.run_id, context.started_at, context.metadata_as_dict())
        store.put_extra("suite", config.suite.model_dump(mode="json"))

        pipeline = list(self._composer.compose(context, config))
        steps: list[StepResult] = []
        context.pipeline_steps = []
        context.executed_pipeline_steps = []
        skip_to_cleanup = False
        stop_pipeline = False

        for idx, item in enumerate(pipeline):
            if stop_pipeline:
                break
            if skip_to_cleanup and not item.cleanup:
                skipped = StepResult(
                    layer=item.key,
                    name=item.key,
                    status=StepExecutionStatus.SKIPPED,
                    detail={"reason": "skip_to_cleanup"},
                )
                steps.append(skipped)
                context.executed_pipeline_steps = list(steps)
                seq = len(steps) - 1
                store.record_step(seq, skipped.model_dump(mode="json"))
                self._record_layer_timing(store, pipeline_key=item.key, sequence_index=seq, step=skipped)
                continue

            # Interim report snapshot: steps completed before this stage's run() (not after).
            context.pipeline_steps = list(steps)
            step = item.run()
            if step is None:
                context.executed_pipeline_steps = list(steps)
                continue
            steps.append(step)
            context.executed_pipeline_steps = list(steps)
            seq = len(steps) - 1
            store.record_step(seq, step.model_dump(mode="json"))
            self._record_layer_timing(store, pipeline_key=item.key, sequence_index=seq, step=step)

            if step.status != StepExecutionStatus.FAILED:
                continue

            mode = self._effective_failure_mode(step, config)
            if mode == StepFailureMode.STOP:
                stop_pipeline = True
            elif mode == StepFailureMode.CONTINUE:
                continue
            elif mode == StepFailureMode.SKIP_TO_CLEANUP:
                skip_to_cleanup = True

        context.pipeline_steps = list(steps)
        context.executed_pipeline_steps = list(steps)

        finished_at = datetime.now(timezone.utc)
        status = derive_run_lifecycle_status(steps)
        summary = {
            "step_count": len(steps),
            "failed": sum(1 for s in steps if s.status == StepExecutionStatus.FAILED),
            "skipped": sum(1 for s in steps if s.status == StepExecutionStatus.SKIPPED),
        }
        return RunResult(
            run_id=context.run_id,
            status=status,
            started_at=context.started_at,
            finished_at=finished_at,
            steps=steps,
            summary=summary,
        )

    def run(
        self,
        context: Optional[RunContext] = None,
        config: Optional[AgentConfig] = None,
    ) -> RunResult:
        """Execute the full pipeline in the current thread (blocking)."""
        context = context or RunContext()
        if config is None:
            from qa_agent.config.settings import load_agent_config

            config = load_agent_config()
        return self._execute_pipeline(context, config)

    async def arun(
        self,
        context: Optional[RunContext] = None,
        config: Optional[AgentConfig] = None,
    ) -> RunResult:
        """
        Execute the same pipeline as :meth:`run` in a worker thread.

        Use from async HTTP handlers so sync validators, drivers, and store I/O do not
        block the event loop. The pipeline implementation remains synchronous; native
        async layers/drivers can be introduced incrementally later.
        """
        ctx = context or RunContext()
        if config is None:
            from qa_agent.config.settings import load_agent_config

            config = load_agent_config()
        return await asyncio.to_thread(self._execute_pipeline, ctx, config)


def default_orchestrator() -> QAOrchestrator:
    from qa_agent.core.pipeline import OrchestratorLayers
    from qa_agent.flows.default_registry import default_flow_registry
    from qa_agent.flows.integration import FlowEngineExecutionLayer
    from qa_agent.layers import (
        DefaultAnalysis,
        DefaultDiscovery,
        DefaultFlowAssertions,
        DefaultPlanner,
        DefaultReporting,
        DefaultStepAssertions,
    )

    layers = OrchestratorLayers(
        planner=DefaultPlanner(),
        discovery=DefaultDiscovery(),
        execution=FlowEngineExecutionLayer(default_flow_registry()),
        step_assertions=DefaultStepAssertions(),
        flow_assertions=DefaultFlowAssertions(),
        analysis=DefaultAnalysis(),
        reporting=DefaultReporting(),
    )
    return QAOrchestrator(layers=layers)
