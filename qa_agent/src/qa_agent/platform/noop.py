"""Inert driver for tests and offline skeleton runs."""

from __future__ import annotations

from typing import Any, Mapping

from qa_agent.platform.driver import NavigateTarget
from qa_agent.platform.types import DriverResult


class NoOpPlatformDriver:
    """Returns successful empty results; does not perform real I/O."""

    def navigate(self, target: NavigateTarget) -> DriverResult:
        return DriverResult(ok=True, detail={"noop": True, "op": "navigate"})

    def interact(self, action: Mapping[str, Any]) -> DriverResult:
        return DriverResult(ok=True, detail={"noop": True, "op": "interact"})

    def read(self, spec: Mapping[str, Any]) -> DriverResult:
        return DriverResult(ok=True, detail={"noop": True, "op": "read"})

    def wait(self, spec: Mapping[str, Any]) -> DriverResult:
        return DriverResult(ok=True, detail={"noop": True, "op": "wait"})
