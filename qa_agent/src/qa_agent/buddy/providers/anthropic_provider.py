"""Anthropic Claude provider."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from qa_agent.buddy.providers.base import BaseProvider, ContentBlock, LLMResponse

log = logging.getLogger("qa_agent.buddy.providers.anthropic")

_SECRET_FILE_ENV = "ANTHROPIC_API_KEY_FILE"
_SECRET_FILE_DEFAULT = "/app/secrets/anthropic_api_key"
_SECRET_ENV_FALLBACK = "ANTHROPIC_API_KEY"


def _read_key() -> str:
    file_path = os.environ.get(_SECRET_FILE_ENV, _SECRET_FILE_DEFAULT)
    try:
        key = Path(file_path).read_text(encoding="utf-8").strip()
        if key:
            return key
    except (OSError, IOError):
        pass
    key = os.environ.get(_SECRET_ENV_FALLBACK, "")
    if key:
        return key
    raise RuntimeError(
        "Anthropic API key not found.\n"
        f"  In K8s: mount secret at {_SECRET_FILE_DEFAULT}\n"
        f"  Locally: export {_SECRET_ENV_FALLBACK}=sk-ant-..."
    )


class AnthropicProvider(BaseProvider):
    """Anthropic Claude via the anthropic SDK."""

    def __init__(self, model: str = "claude-opus-4-6") -> None:
        self._model = model

    def _client(self):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("Run: pip install anthropic")
        return anthropic.Anthropic(api_key=_read_key())

    def format_tool_definitions(self, tools: list[dict]) -> list[dict]:
        # Anthropic format uses 'input_schema' — tools already in this format
        return tools

    def format_tool_result(self, tool_use_id: str, content: str) -> dict:
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
        }

    def chat(self, messages: list[dict], tools: list[dict], system_prompt: str, max_tokens: int = 4096) -> LLMResponse:
        client = self._client()
        response = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=self.format_tool_definitions(tools),
            messages=messages,
        )

        blocks: list[ContentBlock] = []
        for block in response.content:
            if block.type == "text":
                blocks.append(ContentBlock(type="text", text=block.text))
            elif block.type == "tool_use":
                blocks.append(ContentBlock(
                    type="tool_use",
                    tool_use_id=block.id,
                    tool_name=block.name,
                    tool_input=block.input or {},
                ))

        stop_reason = "end_turn" if response.stop_reason == "end_turn" else "tool_use"
        return LLMResponse(content=blocks, stop_reason=stop_reason, raw=response)
