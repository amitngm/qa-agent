"""Base provider protocol and normalized response types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContentBlock:
    """Normalized content block — provider-agnostic."""
    type: str                    # "text" | "tool_use"
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Normalized LLM response — provider-agnostic."""
    content: list[ContentBlock]
    stop_reason: str             # "end_turn" | "tool_use"
    raw: Any = None              # original provider response (for debugging)


class BaseProvider(ABC):
    """Abstract LLM provider. Implement one subclass per provider."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send messages to the LLM and return a normalized response."""
        ...

    @abstractmethod
    def format_tool_result(self, tool_use_id: str, content: str) -> dict:
        """
        Format a tool result for appending to the message history.
        Different providers use different formats for tool results.
        """
        ...

    @abstractmethod
    def format_tool_definitions(self, tools: list[dict]) -> list[dict]:
        """
        Convert tool definitions to provider-specific format.
        Anthropic uses 'input_schema', OpenAI uses 'parameters'.
        """
        ...
