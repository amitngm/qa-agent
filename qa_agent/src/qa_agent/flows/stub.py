"""Generic placeholder flows for wiring tests — no domain behavior."""

from __future__ import annotations

from qa_agent.config.settings import AgentConfig
from qa_agent.flows.base import BaseFlow
from qa_agent.flows.step_hooks import FlowStepHooks
from qa_agent.flows.step_runner import standard_run_execute_phase
from qa_agent.flows.step_spec import FlowStepKind, FlowStepSpec
from qa_agent.flows.types import FlowContext, FlowPhaseResult, FlowStepOutcome, PhaseOutcome


class NoOpFlow(BaseFlow):
    """Succeeds all phases; use as a template or registry default."""

    flow_key = "noop"


class LinearTwoStepFlow(BaseFlow):
    """
    Minimal declarative execute phase: two SEQUENTIAL steps via ``standard_run_execute_phase``.

    Illustrates platform-owned kind dispatch + hook-based step bodies (extension point).
    """

    flow_key = "linear_two_step"

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
            FlowStepSpec(step_key="first", kind=FlowStepKind.SEQUENTIAL),
            FlowStepSpec(step_key="second", kind=FlowStepKind.SEQUENTIAL),
        )
        return standard_run_execute_phase(specs, ctx, config, self._hooks)
