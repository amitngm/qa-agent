"""Platform driver interface — navigation and interaction without app semantics."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Union, runtime_checkable

from qa_agent.platform.types import DriverResult

NavigateTarget = Union[str, Mapping[str, Any]]


@runtime_checkable
class PlatformDriver(Protocol):
    """
    Minimal surface for UI or device automation backends.

    Arguments are intentionally opaque maps or strings so hosts can encode
    URLs, selectors, coordinates, or custom payloads without the core knowing.
    """

    def navigate(self, target: NavigateTarget) -> DriverResult:
        """Move context to a location or screen described by ``target``."""

    def interact(self, action: Mapping[str, Any]) -> DriverResult:
        """Perform an action (click, type, tap, etc.) described by ``action``."""

    def read(self, spec: Mapping[str, Any]) -> DriverResult:
        """Read state or content; result details are driver-defined."""

    def wait(self, spec: Mapping[str, Any]) -> DriverResult:
        """Wait for a condition described by ``spec``."""
