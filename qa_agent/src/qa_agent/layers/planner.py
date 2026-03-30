"""Planning layer — offline only; must not touch live application surfaces."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.layers.base import PlannerLayer
from qa_agent.planning.contract import OfflinePlanArtifact
from qa_agent.store.protocol import RunStore


class DefaultPlanner(PlannerLayer):
    """
    Produces :class:`OfflinePlanArtifact` and persists via Run Store.

    **Boundary:** no discoverer, executor, or driver calls — configuration
    and static structure only.
    """

    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        start = time.perf_counter()
        plan_id = str(uuid4())
        prior_digests: list[dict[str, Any]] = []
        reader = context.run_history_reader
        limit = config.planner.prior_run_digest_limit
        if reader is not None and limit > 0:
            prior_digests = [dict(d) for d in reader.read_recent_digests(limit=limit)]

        artifact = OfflinePlanArtifact(
            plan_id=plan_id,
            environment=config.environment,
            steps_outline=[],
            constraints={"layers_enabled": {k: v.enabled for k, v in config.layers.items()}},
            detail={
                "source": "DefaultPlanner",
                "prior_run_digest_count": len(prior_digests),
            },
        )
        store = context.run_store
        if isinstance(store, RunStore):
            store.put_extra("offline_plan", artifact.model_dump(mode="json"))
        planner_meta: dict[str, Any] = {
            "offline_plan_id": plan_id,
            "plan": {"plan_id": plan_id, "offline_only": True},
        }
        if prior_digests:
            planner_meta["prior_run_digests"] = prior_digests
        context.merge_metadata({"planner": planner_meta})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="planner",
            name=self.name,
            status=StepStatus.SUCCEEDED,
            duration_ms=duration_ms,
            detail={"plan_id": plan_id, "boundary": "offline_planner"},
        )
