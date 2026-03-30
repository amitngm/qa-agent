"""Hooks for :class:`FlowStepSpec` execution — flow-specific work; kinds are interpreted by the runner."""

from __future__ import annotations

from abc import ABC, abstractmethod

from qa_agent.config.settings import AgentConfig
from qa_agent.flows.step_spec import FlowStepKind, FlowStepSpec
from qa_agent.flows.types import FlowContext, FlowStepOutcome


class UnsupportedFlowStepKind(RuntimeError):
    """Raised when a step kind requires a hook the flow did not implement."""

    def __init__(self, kind: FlowStepKind, message: str) -> None:
        self.kind = kind
        super().__init__(message)


class FlowStepHooks(ABC):
    """
    Per-step behavior for a declarative execute phase.

    :func:`qa_agent.flows.step_runner.standard_run_execute_phase` owns control flow for
    :class:`~qa_agent.flows.step_spec.FlowStepKind`; implementors supply domain actions.

    Override only the methods required by the :class:`~qa_agent.flows.step_spec.FlowStepSpec`
    rows your flow uses; the default implementations raise :class:`UnsupportedFlowStepKind`.
    """

    @abstractmethod
    def execute_step(
        self,
        ctx: FlowContext,
        config: AgentConfig,
        spec: FlowStepSpec,
    ) -> FlowStepOutcome:
        """Run the body for a SEQUENTIAL step (and as the action inside POLL iterations)."""

    def branch_value(self, ctx: FlowContext, config: AgentConfig, spec: FlowStepSpec) -> str:
        raise UnsupportedFlowStepKind(
            FlowStepKind.BRANCH,
            "BRANCH steps require FlowStepHooks.branch_value",
        )

    def evaluate_condition(self, ctx: FlowContext, config: AgentConfig, spec: FlowStepSpec) -> bool:
        raise UnsupportedFlowStepKind(
            FlowStepKind.CONDITIONAL,
            "CONDITIONAL steps require FlowStepHooks.evaluate_condition",
        )

    def poll_tick(self, ctx: FlowContext, config: AgentConfig, spec: FlowStepSpec) -> bool:
        raise UnsupportedFlowStepKind(
            FlowStepKind.POLL,
            "POLL steps require FlowStepHooks.poll_tick",
        )
