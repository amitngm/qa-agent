"""
Sample application-agnostic flow: a linear CRUD-style lifecycle with verification gates.

Steps are generic labels only; hooks succeed without domain I/O so the framework can run end-to-end.
"""

from __future__ import annotations

from qa_agent.config.settings import AgentConfig
from qa_agent.flows.base import BaseFlow
from qa_agent.flows.step_hooks import FlowStepHooks
from qa_agent.flows.step_runner import standard_run_execute_phase
from qa_agent.flows.step_spec import FlowStepKind, FlowStepSpec
from qa_agent.flows.types import FlowContext, FlowPhaseResult, FlowStepOutcome, PhaseOutcome


class GenericCrudLifecycleFlow(BaseFlow):
    """
    Declarative execute phase: Login → Search → Create → Verify → Edit → Verify → Delete → Verify.

    Stable key for suite metadata and UI: ``generic_crud_lifecycle``.
    """

    flow_key = "generic_crud_lifecycle"
    flow_version = "1.0.0"

    class _Hooks(FlowStepHooks):
        def execute_step(self, ctx: FlowContext, config: AgentConfig, spec: FlowStepSpec) -> FlowStepOutcome:
            return FlowStepOutcome(
                step_key=spec.step_key,
                outcome=PhaseOutcome.SUCCEEDED,
                step_kind=spec.kind.value,
            )

    _hooks = _Hooks()

    def execute_steps(self, ctx: FlowContext, config: AgentConfig) -> FlowPhaseResult:
        specs = (
            FlowStepSpec(
                step_key="login",
                kind=FlowStepKind.SEQUENTIAL,
                detail={"label": "Login", "order": 1},
            ),
            FlowStepSpec(
                step_key="search",
                kind=FlowStepKind.SEQUENTIAL,
                detail={"label": "Search", "order": 2},
            ),
            FlowStepSpec(
                step_key="create",
                kind=FlowStepKind.SEQUENTIAL,
                detail={"label": "Create", "order": 3},
            ),
            FlowStepSpec(
                step_key="verify_after_create",
                kind=FlowStepKind.SEQUENTIAL,
                detail={"label": "Verify", "order": 4},
            ),
            FlowStepSpec(
                step_key="edit",
                kind=FlowStepKind.SEQUENTIAL,
                detail={"label": "Edit", "order": 5},
            ),
            FlowStepSpec(
                step_key="verify_after_edit",
                kind=FlowStepKind.SEQUENTIAL,
                detail={"label": "Verify", "order": 6},
            ),
            FlowStepSpec(
                step_key="delete",
                kind=FlowStepKind.SEQUENTIAL,
                detail={"label": "Delete", "order": 7},
            ),
            FlowStepSpec(
                step_key="verify_after_delete",
                kind=FlowStepKind.SEQUENTIAL,
                detail={"label": "Verify", "order": 8},
            ),
        )
        return standard_run_execute_phase(specs, ctx, config, self._hooks)
