"""Provider factory — builds the right LLM provider from environment config."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from qa_agent.buddy.providers.base import BaseProvider

log = logging.getLogger("qa_agent.buddy.providers.factory")

# Default models per provider (free-tier where available)
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-4-6",
    "groq":      "llama-3.3-70b-versatile",
    "ollama":    "llama3.2",
    "openai":    "gpt-4o-mini",
    "gemini":    "gemini-2.0-flash",
}

DEFAULT_BASE_URLS: dict[str, str] = {
    "groq":   "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
}


def _read_secret(file_env: str, file_default: str, env_fallback: str) -> str | None:
    file_path = os.environ.get(file_env, file_default)
    try:
        key = Path(file_path).read_text(encoding="utf-8").strip()
        if key:
            return key
    except (OSError, IOError):
        pass
    return os.environ.get(env_fallback) or None


def build_provider(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> BaseProvider:
    """
    Build a provider from config. Falls back to environment variables:
      BUDDY_PROVIDER — anthropic | groq | ollama | openai | gemini  (default: anthropic)
      BUDDY_MODEL    — model name (defaults per provider if not set)
      BUDDY_BASE_URL — override API base URL (useful for Ollama)
    """
    provider = (provider or os.environ.get("BUDDY_PROVIDER", "anthropic")).lower()
    model = model or os.environ.get("BUDDY_MODEL") or DEFAULT_MODELS.get(provider, "")
    base_url = base_url or os.environ.get("BUDDY_BASE_URL") or DEFAULT_BASE_URLS.get(provider)

    log.info("building provider: %s / model: %s", provider, model)

    if provider == "anthropic":
        from qa_agent.buddy.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(model=model)

    if provider in ("groq", "openai", "ollama"):
        from qa_agent.buddy.providers.openai_compat import OpenAICompatProvider

        if provider == "groq":
            api_key = _read_secret(
                "GROQ_API_KEY_FILE", "/app/secrets/groq_api_key", "GROQ_API_KEY"
            )
            if not api_key:
                raise RuntimeError(
                    "Groq API key not found. Get a free key at https://console.groq.com\n"
                    "Then: export GROQ_API_KEY=gsk_..."
                )
        elif provider == "openai":
            api_key = _read_secret(
                "OPENAI_API_KEY_FILE", "/app/secrets/openai_api_key", "OPENAI_API_KEY"
            )
            if not api_key:
                raise RuntimeError("OpenAI API key not found. export OPENAI_API_KEY=sk-...")
        else:  # ollama — no key needed
            api_key = "ollama"

        return OpenAICompatProvider(model=model, base_url=base_url, api_key=api_key)

    if provider == "gemini":
        from qa_agent.buddy.providers.gemini_provider import GeminiProvider
        return GeminiProvider(model=model)

    raise ValueError(
        f"Unknown provider '{provider}'. "
        f"Supported: anthropic, groq, ollama, openai, gemini"
    )
