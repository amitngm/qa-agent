"""Default :class:`~qa_agent.flows.registry.FlowRegistry` wired for local runs and the UI."""

from __future__ import annotations

from qa_agent.flows.registry import FlowRegistry
from qa_agent.flows.sample_generic_crud import GenericCrudLifecycleFlow
from qa_agent.flows.stub import LinearTwoStepFlow, NoOpFlow


def default_flow_registry() -> FlowRegistry:
    """Built-in sample flows (keys: ``noop``, ``linear_two_step``, ``generic_crud_lifecycle``)."""
    return FlowRegistry(
        (
            NoOpFlow(),
            LinearTwoStepFlow(),
            GenericCrudLifecycleFlow(),
        )
    )
