"""Abstract contracts for QA pipeline layers — implement in subclasses or adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qa_agent.config.settings import AgentConfig
    from qa_agent.core.types import RunContext, StepResult


@runtime_checkable
class LayerProtocol(Protocol):
    """Structural contract for any layer callable from the orchestrator.

    Implementations must expose a non-empty string :attr:`name` (enforced when wiring
    :class:`~qa_agent.core.pipeline.OrchestratorLayers`).
    """

    name: str

    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class BaseLayer(ABC):
    """Base class for layers with shared helpers."""

    name: str

    def __init__(self, name: Optional[str] = None) -> None:
        self.name = name or self.__class__.__name__

    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        """Execute layer logic; must not mutate global process state beyond context."""


class PlannerLayer(BaseLayer):
    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class DiscoveryLayer(BaseLayer):
    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class ExecutionLayer(BaseLayer):
    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class ValidationLayer(BaseLayer):
    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class StepAssertionsLayer(ValidationLayer):
    """Assertions tied to individual executor/platform steps (fine-grained)."""

    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class FlowAssertionsLayer(ValidationLayer):
    """Assertions across a whole flow or multi-step scenario (coarse-grained)."""

    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class AnalysisLayer(BaseLayer):
    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class ReportingLayer(BaseLayer):
    @abstractmethod
    def run(self, context: RunContext, config: AgentConfig) -> StepResult: ...


class PluginHost(ABC):
    """Optional extension point for UI, API, data, and report plugins."""

    @abstractmethod
    def register(self, key: str, plugin: Any) -> None:
        """Register a plugin by stable key."""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Retrieve plugin or None."""
