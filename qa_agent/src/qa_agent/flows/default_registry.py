"""Default :class:`~qa_agent.flows.registry.FlowRegistry` wired for local runs and the UI."""

from __future__ import annotations

from qa_agent.flows.config_driven import load_config_driven_flows
from qa_agent.flows.registry import FlowRegistry
from qa_agent.flows.sample_generic_crud import GenericCrudLifecycleFlow
from qa_agent.flows.stub import LinearTwoStepFlow, NoOpFlow


def default_flow_registry() -> FlowRegistry:
    """Built-in sample flows + config-driven flows from config/flows/*.yaml."""
    registry = FlowRegistry(
        (
            NoOpFlow(),
            LinearTwoStepFlow(),
            GenericCrudLifecycleFlow(),
        )
    )
    for flow in load_config_driven_flows():
        registry.register(flow)
    return registry
