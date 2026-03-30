"""Discovery layer — live application/system surfaces only."""

from __future__ import annotations

import time

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.discovery.contract import DiscoveryReport
from qa_agent.layers.base import DiscoveryLayer
from qa_agent.store.protocol import RunStore


class DefaultDiscovery(DiscoveryLayer):
    """
    Populates :class:`DiscoveryReport` using live probes (placeholders here).

    **Boundary:** may read ``offline_plan`` / ``plan_id`` from the Run Store
    but must not delegate live work to the planner.
    """

    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        start = time.perf_counter()
        plan_ref = None
        store = context.run_store
        if isinstance(store, RunStore):
            raw = store.get_extra("offline_plan")
            if isinstance(raw, dict):
                plan_ref = raw.get("plan_id")
        report = DiscoveryReport(
            discoverer_id=self.name,
            plan_reference_id=plan_ref,
            targets=[],
            sources=[],
            detail={"placeholder": True, "boundary": "live_discoverer"},
        )
        if isinstance(store, RunStore):
            store.set_discovery_report(report)
        context.merge_metadata({"discovery": report.model_dump(mode="json")})
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="discovery",
            name=self.name,
            status=StepStatus.SUCCEEDED,
            duration_ms=duration_ms,
            detail={"target_count": 0, "discoverer_id": report.discoverer_id},
        )
