"""OpenAI-compatible provider — handles Groq, Ollama, and OpenAI.

Both Groq and Ollama expose an OpenAI-compatible REST API, so one
provider class handles all three by varying base_url and api_key.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from qa_agent.buddy.providers.base import BaseProvider, ContentBlock, LLMResponse

log = logging.getLogger("qa_agent.buddy.providers.openai_compat")


def _read_key(env_file: str, default_file: str, env_fallback: str) -> str | None:
    file_path = os.environ.get(env_file, default_file)
    try:
        key = Path(file_path).read_text(encoding="utf-8").strip()
        if key:
            return key
    except (OSError, IOError):
        pass
    return os.environ.get(env_fallback) or None


class OpenAICompatProvider(BaseProvider):
    """
    Provider for any OpenAI-compatible API:
      - Groq:   base_url=https://api.groq.com/openai/v1, needs GROQ_API_KEY
      - Ollama: base_url=http://localhost:11434/v1, no key needed
      - OpenAI: base_url=https://api.openai.com/v1, needs OPENAI_API_KEY
    """

    def __init__(self, model: str, base_url: str, api_key: str | None = None) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key or "ollama"  # Ollama accepts any non-empty string

    def _client(self):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("Run: pip install openai")
        return OpenAI(api_key=self._api_key, base_url=self._base_url)

    def format_tool_definitions(self, tools: list[dict]) -> list[dict]:
        """Convert Anthropic-style tools (input_schema) to OpenAI-style (parameters)."""
        converted = []
        for t in tools:
            converted.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return converted

    def format_tool_result(self, tool_use_id: str, content: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_use_id,
            "content": content,
        }

    def chat(self, messages: list[dict], tools: list[dict], system_prompt: str, max_tokens: int = 4096) -> LLMResponse:
        client = self._client()

        # Convert message history from Anthropic format to OpenAI format
        oai_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    oai_messages.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Could be tool results or text blocks
                    tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
                    text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    for tr in tool_results:
                        oai_messages.append({
                            "role": "tool",
                            "tool_call_id": tr.get("tool_use_id", ""),
                            "content": str(tr.get("content", "")),
                        })
                    for tb in text_blocks:
                        oai_messages.append({"role": "user", "content": tb.get("text", "")})

            elif role == "tool":
                # Tool result messages stored by format_tool_result — pass through as-is
                oai_messages.append(msg)
                continue

            elif role == "assistant":
                if isinstance(content, str):
                    oai_messages.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text" and block.get("text"):
                                text_parts.append(block["text"])
                            elif block.get("type") == "tool_use":
                                tool_calls.append({
                                    "id": block.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": block.get("name", ""),
                                        "arguments": json.dumps(block.get("input", {})),
                                    },
                                })
                        else:
                            # Anthropic SDK objects
                            btype = getattr(block, "type", None)
                            if btype == "text":
                                text_parts.append(getattr(block, "text", ""))
                            elif btype == "tool_use":
                                tool_calls.append({
                                    "id": getattr(block, "id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": getattr(block, "name", ""),
                                        "arguments": json.dumps(getattr(block, "input", {})),
                                    },
                                })
                    assistant_msg: dict = {"role": "assistant", "content": " ".join(text_parts) or None}
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls
                    oai_messages.append(assistant_msg)

        # Remove empty messages (keep tool messages and assistant messages with tool_calls)
        oai_messages = [
            m for m in oai_messages
            if m.get("content") or m.get("tool_calls") or m.get("role") == "tool"
        ]

        oai_tools = self.format_tool_definitions(tools) if tools else []

        kwargs: dict = {
            "model": self._model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        blocks: list[ContentBlock] = []

        if message.content:
            blocks.append(ContentBlock(type="text", text=message.content))

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append(ContentBlock(
                    type="tool_use",
                    tool_use_id=tc.id,
                    tool_name=tc.function.name,
                    tool_input=args,
                ))

        finish = choice.finish_reason
        stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"

        return LLMResponse(content=blocks, stop_reason=stop_reason, raw=response)
