"""Register flows by stable key for execution-layer or standalone runners."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from qa_agent.flows.base import FlowProtocol


class FlowRegistry:
    def __init__(self, flows: Optional[Iterable[FlowProtocol]] = None) -> None:
        self._flows: Dict[str, FlowProtocol] = {}
        if flows:
            for f in flows:
                self.register(f)

    def register(self, flow: FlowProtocol) -> None:
        self._flows[flow.flow_key] = flow

    def unregister(self, flow_key: str) -> None:
        self._flows.pop(flow_key, None)

    def get(self, flow_key: str) -> Optional[FlowProtocol]:
        return self._flows.get(flow_key)

    def require(self, flow_key: str) -> FlowProtocol:
        flow = self.get(flow_key)
        if flow is None:
            raise KeyError(f"unknown flow_key: {flow_key}")
        return flow

    def keys(self) -> Iterable[str]:
        return tuple(self._flows.keys())
