"""Lightweight plugin registry for optional adapters."""

from __future__ import annotations

from typing import Any, Optional

from qa_agent.layers.base import PluginHost


class SimplePluginHost(PluginHost):
    def __init__(self) -> None:
        self._registry: dict[str, Any] = {}

    def register(self, key: str, plugin: Any) -> None:
        self._registry[key] = plugin

    def get(self, key: str) -> Optional[Any]:
        return self._registry.get(key)
