"""Brain — multi-provider LLM tool-use conversation loop powering TestBuddy."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from typing import Any

from qa_agent.buddy.audit import AuditLog
from qa_agent.buddy.permission import PermissionDecision, PermissionEngine
from qa_agent.buddy.providers.base import BaseProvider
from qa_agent.buddy.recovery import RecoveryEngine
from qa_agent.buddy.registry import ToolRegistry
from qa_agent.buddy.session import PendingApproval, Session
from qa_agent.buddy.tool import RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.brain")


SYSTEM_PROMPT = """You are TestBuddy, an expert AI assistant for platform engineers and QA teams.

You have access to tools that let you inspect and interact with:
- Kubernetes clusters (pods, logs, events, deployments, services)
- Databases (SELECT queries, table inspection)
- Microservices (HTTP requests, health checks)
- Infrastructure (coming: Grafana, OpenStack, F5, MetalLB)

## How you work

**Investigation**: When the user reports an issue or asks a question, gather evidence
from multiple sources before drawing conclusions. Be specific — show actual log lines,
actual query results, actual metric values. After gathering evidence, explain the root
cause clearly and concisely.

**Actions**: When the user asks you to perform a write or destructive action:
1. Describe exactly what you're going to do (tool name, parameters)
2. State the risk level (WRITE = reversible, DESTRUCTIVE = hard to reverse)
3. The system will ask for user approval before you execute
4. After execution, verify the result and report back

**Style**:
- Be direct and technical. The user is a platform engineer.
- Show evidence (log lines, query results) not just summaries
- If you don't know something, say so and suggest what tool would help
- Prefer READ tools first (gather info), then propose WRITE if needed
- If something could cause an outage, warn explicitly

## Risk tiers
- READ: Always execute immediately (no confirmation needed)
- WRITE: Reversible — show what you'll do, execute after approval
- DESTRUCTIVE: Hard to reverse — show dry-run output + require explicit approval

## Log analysis and issue reporting

When the user asks you to scan logs, investigate a pod, or look for issues:
1. Use `scan_namespace_for_issues` to scan all pods at once, or `analyze_pod_logs` for a specific pod.
2. Always present findings as a structured **Issue Report** using this format:

---
### Issue Report — <pod or namespace>

| # | Severity | Category | Summary |
|---|----------|----------|---------|
| 1 | CRITICAL  | oom      | Java heap OOM on line 42 |

**Issue 1 — CRITICAL: OOM**
- **Log line**: `<exact log line>`
- **Root cause**: <1-2 sentence hypothesis>
- **Suggested fix**: <concrete action steps>

---

3. Sort issues: critical → high → medium → low.
4. After the report, list remediation options (e.g. restart pod, scale deployment, increase limits) and ask which the user wants to apply.
5. For pods with no issues, say so briefly — don't list every healthy pod.
6. If a pod has repeated restarts, always check previous container logs too (previous=true).

You are currently connected to the platform. Ask clarifying questions if the
namespace or service name is ambiguous."""


class Brain:
    """Drives the LLM tool-use loop for a single conversation session."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission: PermissionEngine,
        recovery: RecoveryEngine,
        audit: AuditLog,
        provider: BaseProvider,
        max_tool_rounds: int = 10,
    ) -> None:
        self._registry = registry
        self._permission = permission
        self._recovery = recovery
        self._audit = audit
        self._provider = provider
        self._max_rounds = max_tool_rounds

    # ------------------------------------------------------------------
    # Public: stream chat response
    # ------------------------------------------------------------------

    def chat(self, session: Session, user_message: str) -> Generator[dict, None, None]:
        """
        Process a user message.  Yields event dicts:
          {"type": "text",     "content": str}
          {"type": "tool_call","name": str, "params": dict, "risk": str}
          {"type": "tool_result","name": str, "ok": bool, "data": str}
          {"type": "approval_required", "approval": dict}
          {"type": "error",    "content": str}
        """
        if user_message:
            session.append_user(user_message)

        tools = self._registry.to_claude_tools()

        for _round in range(self._max_rounds):
            try:
                response = self._provider.chat(
                    messages=session.messages,
                    tools=tools,
                    system_prompt=SYSTEM_PROMPT,
                )
            except Exception as exc:
                yield {"type": "error", "content": str(exc)}
                return

            # Store assistant response in normalized dict form for session history
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text or ""})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.tool_use_id,
                        "name": block.tool_name,
                        "input": block.tool_input,
                    })
            session.append_assistant(assistant_content)

            # Yield text blocks to UI
            for block in response.content:
                if block.type == "text" and block.text:
                    yield {"type": "text", "content": block.text}

            if response.stop_reason == "end_turn":
                return

            # Process tool calls
            if response.stop_reason == "tool_use":
                tool_results: list[dict] = []

                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool = self._registry.get(block.tool_name or "")
                    params: dict = block.tool_input or {}

                    if tool is None:
                        msg = f"ERROR: unknown tool '{block.tool_name}'"
                        tool_results.append(
                            self._mk_tool_result(block.tool_use_id or "", msg)
                        )
                        continue

                    decision = self._permission.check(tool, session.role)
                    yield {"type": "tool_call", "name": block.tool_name, "params": params,
                           "risk": tool.risk_level.value}

                    if decision == PermissionDecision.DENY:
                        msg = (
                            f"Permission denied: tool '{block.tool_name}' requires "
                            f"role '{self._permission.describe_required_role(tool)}', "
                            f"current role is '{session.role}'"
                        )
                        tool_results.append(self._mk_tool_result(block.tool_use_id or "", msg))
                        yield {"type": "tool_result", "name": block.tool_name, "ok": False, "data": msg}
                        continue

                    if decision == PermissionDecision.REQUIRE_APPROVAL:
                        snap_id: str | None = None
                        if tool.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                            try:
                                snap_id = self._recovery.snapshot(block.tool_name or "", params, {})
                            except Exception as e:
                                log.warning("snapshot failed: %s", e)

                        approval = PendingApproval(
                            tool_use_id=block.tool_use_id or "",
                            tool_name=block.tool_name or "",
                            params=params,
                            risk_level=tool.risk_level.value,
                            description=_describe_action(block.tool_name or "", params),
                            snap_id=snap_id,
                        )
                        session.pending_approval = approval
                        yield {"type": "approval_required", "approval": {
                            "tool_use_id": block.tool_use_id,
                            "tool_name": block.tool_name,
                            "params": params,
                            "risk_level": tool.risk_level.value,
                            "description": approval.description,
                            "snap_id": snap_id,
                        }}
                        return

                    # ALLOW — execute immediately
                    result = _safe_execute(tool, params)
                    self._audit.record(session.session_id, session.user, block.tool_name or "", params, result)

                    yield {
                        "type": "tool_result",
                        "name": block.tool_name,
                        "ok": result.ok,
                        "data": result.to_content()[:2000],
                    }
                    tool_results.append(
                        self._mk_tool_result(block.tool_use_id or "", result.to_content())
                    )

                # Feed tool results back into session
                for tr in tool_results:
                    session.messages.append(tr)

        yield {"type": "error", "content": "max tool rounds exceeded"}

    # ------------------------------------------------------------------
    # Resume after approval
    # ------------------------------------------------------------------

    def resume_after_approval(self, session: Session, approved: bool) -> Generator[dict, None, None]:
        approval = session.pending_approval
        if approval is None:
            yield {"type": "error", "content": "no pending approval"}
            return

        session.pending_approval = None

        if not approved:
            session.messages.append(
                self._mk_tool_result(approval.tool_use_id, "User denied this action.")
            )
            yield {"type": "text", "content": "Action cancelled by user."}
            return

        tool = self._registry.get(approval.tool_name)
        if tool is None:
            session.messages.append(
                self._mk_tool_result(approval.tool_use_id, f"Tool '{approval.tool_name}' not found.")
            )
            yield {"type": "error", "content": f"tool '{approval.tool_name}' not found"}
            return

        yield {"type": "tool_call", "name": approval.tool_name, "params": approval.params,
               "risk": approval.risk_level}

        result = _safe_execute(tool, approval.params)
        self._audit.record(
            session.session_id, session.user,
            approval.tool_name, approval.params, result,
            approved_by=session.user,
        )

        yield {
            "type": "tool_result",
            "name": approval.tool_name,
            "ok": result.ok,
            "data": result.to_content()[:2000],
        }

        session.messages.append(
            self._mk_tool_result(approval.tool_use_id, result.to_content())
        )

        yield from self.chat(session, "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mk_tool_result(self, tool_use_id: str, content: str) -> dict:
        """Format a tool result for session history using the current provider's format."""
        return self._provider.format_tool_result(tool_use_id, content)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _safe_execute(tool: Any, params: dict) -> ToolResult:
    try:
        return tool.execute(params)
    except Exception as exc:
        log.exception("tool %s raised", tool.name)
        return ToolResult(ok=False, error=str(exc))


def _describe_action(tool_name: str, params: dict) -> str:
    descs = {
        "k8s_restart_pod": lambda p: f"Delete pod {p.get('pod_name')} in namespace {p.get('namespace')} (triggers restart)",
        "k8s_scale_deployment": lambda p: f"Scale {p.get('deployment')} in {p.get('namespace')} to {p.get('replicas')} replicas",
        "k8s_exec": lambda p: f"Run '{p.get('command')}' in pod {p.get('pod_name')} ({p.get('namespace')})",
        "db_execute": lambda p: f"Execute SQL: {str(p.get('sql', ''))[:120]}",
    }
    fn = descs.get(tool_name)
    if fn:
        try:
            return fn(params)
        except Exception:
            pass
    return f"Execute {tool_name} with params: {json.dumps(params, default=str)[:200]}"
