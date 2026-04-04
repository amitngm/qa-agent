"""Brain — Claude tool-use conversation loop powering TestBuddy."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from typing import Any

from qa_agent.buddy.audit import AuditLog
from qa_agent.buddy.permission import PermissionDecision, PermissionEngine
from qa_agent.buddy.recovery import RecoveryEngine
from qa_agent.buddy.registry import ToolRegistry
from qa_agent.buddy.session import PendingApproval, Session
from qa_agent.buddy.tool import RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.brain")

# ── Secret loading: file-first, env fallback ──────────────────────────────────
# In K8s: key is mounted as a file at /run/secrets/anthropic_api_key
# Locally: falls back to ANTHROPIC_API_KEY env var
# NEVER log or expose the key value itself.

_SECRET_FILE_ENV = "ANTHROPIC_API_KEY_FILE"          # points to file path
_SECRET_FILE_DEFAULT = "/app/secrets/anthropic_api_key"
_SECRET_ENV_FALLBACK = "ANTHROPIC_API_KEY"           # local dev only


def _read_api_key() -> str:
    import os
    # 1. File path from env var (set by K8s ConfigMap → ANTHROPIC_API_KEY_FILE)
    file_path = os.environ.get(_SECRET_FILE_ENV, _SECRET_FILE_DEFAULT)
    try:
        from pathlib import Path
        key = Path(file_path).read_text(encoding="utf-8").strip()
        if key:
            log.debug("anthropic key loaded from file")
            return key
    except (OSError, IOError):
        pass
    # 2. Env var fallback — for local development only
    key = os.environ.get(_SECRET_ENV_FALLBACK, "")
    if key:
        log.debug("anthropic key loaded from env (local dev)")
        return key
    raise RuntimeError(
        "Anthropic API key not found.\n"
        f"  In K8s: mount secret at {_SECRET_FILE_DEFAULT}\n"
        f"  Locally: export {_SECRET_ENV_FALLBACK}=sk-ant-..."
    )


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
    """Drives the Claude tool-use loop for a single conversation session."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission: PermissionEngine,
        recovery: RecoveryEngine,
        audit: AuditLog,
        model: str = "claude-opus-4-6",
        max_tool_rounds: int = 10,
    ) -> None:
        self._registry = registry
        self._permission = permission
        self._recovery = recovery
        self._audit = audit
        self._model = model
        self._max_rounds = max_tool_rounds

    def _get_client(self):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        api_key = _read_api_key()
        return anthropic.Anthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Public: stream chat response
    # ------------------------------------------------------------------

    def chat(self, session: Session, user_message: str) -> Generator[dict, None, None]:
        """
        Process a user message.  Yields event dicts:
          {"type": "text",     "content": str}          — assistant text chunk
          {"type": "tool_call","name": str, "params": dict}  — tool being called
          {"type": "tool_result","name": str, "ok": bool, "data": str} — tool result
          {"type": "approval_required", "approval": dict}   — needs user approval
          {"type": "error",    "content": str}           — fatal error
        """
        client = self._get_client()
        session.append_user(user_message)
        tools = self._registry.to_claude_tools()

        for _round in range(self._max_rounds):
            try:
                response = client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=session.messages,
                )
            except Exception as exc:
                yield {"type": "error", "content": str(exc)}
                return

            session.append_assistant(response.content)

            # Extract text blocks to yield
            for block in response.content:
                if block.type == "text" and block.text:
                    yield {"type": "text", "content": block.text}

            # If Claude is done, stop
            if response.stop_reason == "end_turn":
                return

            # Process tool calls
            if response.stop_reason == "tool_use":
                tool_results_content: list[dict] = []

                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool = self._registry.get(block.name)
                    if tool is None:
                        tool_results_content.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"ERROR: unknown tool '{block.name}'",
                        })
                        continue

                    params: dict = block.input or {}
                    decision = self._permission.check(tool, session.role)

                    yield {"type": "tool_call", "name": block.name, "params": params,
                           "risk": tool.risk_level.value}

                    if decision == PermissionDecision.DENY:
                        msg = (f"Permission denied: tool '{block.name}' requires "
                               f"role '{self._permission.describe_required_role(tool)}', "
                               f"current role is '{session.role}'")
                        tool_results_content.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": msg,
                        })
                        yield {"type": "tool_result", "name": block.name, "ok": False, "data": msg}
                        continue

                    if decision == PermissionDecision.REQUIRE_APPROVAL:
                        # Take snapshot if write tool
                        snap_id: str | None = None
                        if tool.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                            try:
                                snap_id = self._recovery.snapshot(block.name, params, {})
                            except Exception as e:
                                log.warning("snapshot failed: %s", e)

                        approval = PendingApproval(
                            tool_use_id=block.id,
                            tool_name=block.name,
                            params=params,
                            risk_level=tool.risk_level.value,
                            description=_describe_action(block.name, params),
                            snap_id=snap_id,
                        )
                        session.pending_approval = approval
                        yield {"type": "approval_required", "approval": {
                            "tool_use_id": block.id,
                            "tool_name": block.name,
                            "params": params,
                            "risk_level": tool.risk_level.value,
                            "description": approval.description,
                            "snap_id": snap_id,
                        }}
                        return  # Pause — wait for /approve or /deny

                    # ALLOW — execute immediately
                    result = _safe_execute(tool, params)
                    self._audit.record(session.session_id, session.user, block.name, params, result)

                    yield {
                        "type": "tool_result",
                        "name": block.name,
                        "ok": result.ok,
                        "data": result.to_content()[:2000],  # truncate for UI
                    }

                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result.to_content(),
                    })

                if tool_results_content:
                    session.messages.append({"role": "user", "content": tool_results_content})

        yield {"type": "error", "content": "max tool rounds exceeded"}

    # ------------------------------------------------------------------
    # Resume after approval
    # ------------------------------------------------------------------

    def resume_after_approval(self, session: Session, approved: bool) -> Generator[dict, None, None]:
        """Called after user approves or denies a pending action."""
        approval = session.pending_approval
        if approval is None:
            yield {"type": "error", "content": "no pending approval"}
            return

        session.pending_approval = None

        if not approved:
            session.append_tool_result(approval.tool_use_id, "User denied this action.")
            yield {"type": "text", "content": "Action cancelled by user."}
            return

        tool = self._registry.get(approval.tool_name)
        if tool is None:
            session.append_tool_result(approval.tool_use_id, f"Tool '{approval.tool_name}' not found.")
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

        session.append_tool_result(approval.tool_use_id, result.to_content())

        # Continue the conversation
        yield from self.chat(session, "")  # empty = let Claude process tool result


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _safe_execute(tool: Any, params: dict) -> ToolResult:
    try:
        return tool.execute(params)
    except Exception as exc:
        log.exception("tool %s raised", tool.name)
        return ToolResult(ok=False, error=str(exc))


def _describe_action(tool_name: str, params: dict) -> str:
    """Generate a human-readable one-liner for the approval card."""
    descs = {
        "k8s_restart_pod": lambda p: f"Delete pod {p.get('pod_name')} in namespace {p.get('namespace')} (triggers restart)",
        "k8s_scale_deployment": lambda p: f"Scale {p.get('deployment')} in {p.get('namespace')} to {p.get('replicas')} replicas",
        "k8s_exec": lambda p: f"Run '{p.get('command')}' in pod {p.get('pod_name')} ({p.get('namespace')})",
        "db_execute": lambda p: f"Execute SQL: {str(p.get('sql', ''))[:120]}",
        "db_snapshot_restore": lambda p: f"Restore snapshot {p.get('snap_id')}",
    }
    fn = descs.get(tool_name)
    if fn:
        try:
            return fn(params)
        except Exception:
            pass
    return f"Execute {tool_name} with params: {json.dumps(params, default=str)[:200]}"
