"""Permission engine — 3-tier READ / WRITE / DESTRUCTIVE gating with role checks."""

from __future__ import annotations

from enum import Enum

from qa_agent.buddy.tool import BaseTool, RiskLevel


class UserRole(str, Enum):
    VIEWER = "viewer"       # READ only
    TESTER = "tester"       # READ + WRITE (with approval)
    OPERATOR = "operator"   # READ + WRITE + DESTRUCTIVE (with approval)
    ADMIN = "admin"         # All, no approval required


class PermissionDecision(str, Enum):
    ALLOW = "allow"                   # Execute immediately
    REQUIRE_APPROVAL = "require_approval"  # Show to user, wait for confirm
    DENY = "deny"                     # Blocked for this role


# Which roles can attempt each risk tier (with approval)
_ROLE_WRITE_ACCESS = {UserRole.TESTER, UserRole.OPERATOR, UserRole.ADMIN}
_ROLE_DESTRUCTIVE_ACCESS = {UserRole.OPERATOR, UserRole.ADMIN}
_ROLE_NO_APPROVAL = {UserRole.ADMIN}


class PermissionEngine:
    def check(self, tool: BaseTool, role: str) -> PermissionDecision:
        try:
            r = UserRole(role)
        except ValueError:
            r = UserRole.VIEWER

        if tool.risk_level == RiskLevel.READ:
            return PermissionDecision.ALLOW

        if tool.risk_level == RiskLevel.WRITE:
            if r not in _ROLE_WRITE_ACCESS:
                return PermissionDecision.DENY
            if r in _ROLE_NO_APPROVAL:
                return PermissionDecision.ALLOW
            return PermissionDecision.REQUIRE_APPROVAL

        if tool.risk_level == RiskLevel.DESTRUCTIVE:
            if r not in _ROLE_DESTRUCTIVE_ACCESS:
                return PermissionDecision.DENY
            if r in _ROLE_NO_APPROVAL:
                return PermissionDecision.ALLOW
            return PermissionDecision.REQUIRE_APPROVAL

        return PermissionDecision.DENY

    def describe_required_role(self, tool: BaseTool) -> str:
        if tool.risk_level == RiskLevel.READ:
            return "viewer"
        if tool.risk_level == RiskLevel.WRITE:
            return "tester"
        return "operator"
