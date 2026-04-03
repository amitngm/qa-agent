"""ToolRegistry — holds all registered tools and produces Claude tool definitions."""

from __future__ import annotations

from qa_agent.buddy.tool import BaseTool, RiskLevel


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def register_many(self, tools: list[BaseTool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def to_claude_tools(self) -> list[dict]:
        return [t.to_claude_tool() for t in self._tools.values()]

    def by_risk(self, risk: RiskLevel) -> list[BaseTool]:
        return [t for t in self._tools.values() if t.risk_level == risk]
