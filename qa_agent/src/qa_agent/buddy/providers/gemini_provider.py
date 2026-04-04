"""Google Gemini provider via the google-generativeai SDK."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from qa_agent.buddy.providers.base import BaseProvider, ContentBlock, LLMResponse

log = logging.getLogger("qa_agent.buddy.providers.gemini")

_SECRET_FILE_ENV = "GEMINI_API_KEY_FILE"
_SECRET_FILE_DEFAULT = "/app/secrets/gemini_api_key"
_SECRET_ENV_FALLBACK = "GEMINI_API_KEY"


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
        "Gemini API key not found.\n"
        f"  Locally: export {_SECRET_ENV_FALLBACK}=AIza...\n"
        "  Get a free key at: https://aistudio.google.com/apikey"
    )


class GeminiProvider(BaseProvider):
    """Google Gemini via google-generativeai SDK (free tier available)."""

    def __init__(self, model: str = "gemini-2.0-flash") -> None:
        self._model = model

    def _client(self):
        try:
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError("Run: pip install google-generativeai")
        genai.configure(api_key=_read_key())
        return genai

    def format_tool_definitions(self, tools: list[dict]) -> list:
        """Convert to Gemini FunctionDeclaration format."""
        try:
            from google.generativeai.types import FunctionDeclaration, Tool
        except ImportError:
            raise RuntimeError("Run: pip install google-generativeai")

        declarations = []
        for t in tools:
            schema = t.get("input_schema", {})
            declarations.append(FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=schema,
            ))
        return [Tool(function_declarations=declarations)]

    def format_tool_result(self, tool_use_id: str, content: str) -> dict:
        # For Gemini we store as a user message with function_response part
        # Brain will use this dict directly in session.messages
        return {
            "role": "user",
            "parts": [{"function_response": {"name": tool_use_id, "response": {"result": content}}}],
        }

    def chat(self, messages: list[dict], tools: list[dict], system_prompt: str, max_tokens: int = 4096) -> LLMResponse:
        genai = self._client()

        model = genai.GenerativeModel(
            model_name=self._model,
            system_instruction=system_prompt,
            tools=self.format_tool_definitions(tools) if tools else None,
        )

        # Convert message history to Gemini format
        gemini_history = []
        for msg in messages[:-1]:  # all but last
            role = msg.get("role")
            content = msg.get("content")
            gemini_role = "user" if role == "user" else "model"

            if isinstance(content, str):
                gemini_history.append({"role": gemini_role, "parts": [content]})
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_result":
                            parts.append({"function_response": {
                                "name": block.get("tool_use_id", ""),
                                "response": {"result": block.get("content", "")},
                            }})
                        elif block.get("type") == "tool_use":
                            parts.append({"function_call": {
                                "name": block.get("name", ""),
                                "args": block.get("input", {}),
                            }})
                    else:
                        btype = getattr(block, "type", None)
                        if btype == "text":
                            parts.append(getattr(block, "text", ""))
                        elif btype == "tool_use":
                            parts.append({"function_call": {
                                "name": getattr(block, "name", ""),
                                "args": getattr(block, "input", {}),
                            }})
                if parts:
                    gemini_history.append({"role": gemini_role, "parts": parts})

        # Last message is the new user input
        last_msg = messages[-1] if messages else {}
        last_content = last_msg.get("content", "")
        if isinstance(last_content, list):
            last_text = " ".join(
                b.get("text", "") for b in last_content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            last_text = str(last_content)

        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(last_text, generation_config={"max_output_tokens": max_tokens})

        blocks: list[ContentBlock] = []
        stop_reason = "end_turn"

        for part in response.parts:
            if hasattr(part, "text") and part.text:
                blocks.append(ContentBlock(type="text", text=part.text))
            elif hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                stop_reason = "tool_use"
                args = dict(fc.args) if fc.args else {}
                blocks.append(ContentBlock(
                    type="tool_use",
                    tool_use_id=fc.name,  # Gemini uses function name as ID
                    tool_name=fc.name,
                    tool_input=args,
                ))

        return LLMResponse(content=blocks, stop_reason=stop_reason, raw=response)
