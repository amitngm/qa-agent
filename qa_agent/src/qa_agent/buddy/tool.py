"""Tool protocol, RiskLevel and ToolResult — base contracts for every buddy tool."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    READ = "read"           # Safe, no confirmation needed
    WRITE = "write"         # Reversible, needs acknowledgment
    DESTRUCTIVE = "destructive"  # Hard to reverse, needs dry-run + approval


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    evidence: dict = field(default_factory=dict)

    def to_content(self) -> str:
        if self.ok:
            if isinstance(self.data, (dict, list)):
                return json.dumps(self.data, default=str, indent=2)
            return str(self.data) if self.data is not None else "ok"
        return f"ERROR: {self.error}"


class BaseTool:
    """Concrete base every tool should extend instead of implementing the protocol raw."""

    name: str = ""
    description: str = ""
    risk_level: RiskLevel = RiskLevel.READ
    input_schema: dict = field(default_factory=dict)

    def execute(self, params: dict) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    def to_claude_tool(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
