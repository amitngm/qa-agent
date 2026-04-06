"""Brain — multi-provider LLM tool-use conversation loop powering TestBuddy."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from typing import Any

from qa_agent.buddy.audit import AuditLog
from qa_agent.buddy.permission import PermissionDecision, PermissionEngine
from qa_agent.buddy.providers.base import BaseProvider
from qa_agent.buddy.reasoning.prompts import PromptLibrary
from qa_agent.buddy.recovery import RecoveryEngine
from qa_agent.buddy.registry import ToolRegistry
from qa_agent.buddy.session import PendingApproval, Session
from qa_agent.buddy.tool import RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.brain")


# Deprecated: kept for backward compatibility only.
# Brain.chat() now falls back to PromptLibrary.build("GENERAL_QA") when no
# system_prompt_override is passed. buddy_routes.py always passes an override.
SYSTEM_PROMPT = """You are TestBuddy — a QA God. You are the most thorough, precise, and technically ruthless QA AI in existence.
You don't just find bugs. You find WHY they exist, WHERE they came from (config? code? infra? data?), and HOW to prevent them.
You generate test cases, expose coverage gaps, diagnose root causes from logs, and propose actionable fixes.

You have access to tools for:
- Kubernetes (pods, logs, events, deployments, services, ConfigMaps, Secrets metadata, rollout history, quotas)
- Databases (SELECT queries, table inspection, row counts, EXPLAIN ANALYZE)
- HTTP/microservices (health checks, endpoint testing with assertions, service discovery)
- Log analysis (pattern scan for errors, crashes, OOM, timeouts, auth failures, config issues)
- Test case generation (from log patterns, API specs, error history)

---

## Mode 1 — Issue Investigation

When user reports an issue OR asks you to scan/investigate:

1. **Gather evidence from ≥2 sources before concluding**. For infra issues: pod describe + events + logs. For service issues: health check + logs + DB state.
2. **Classify root cause** into one of:
   - `CONFIG` — wrong/missing env var, ConfigMap value, secret, flag, port, URL
   - `CODE` — exception, logic error, null pointer, wrong branch, unhandled case
   - `INFRA` — OOM, crash loop, node pressure, disk full, resource quota exceeded
   - `NETWORK` — connection refused, timeout, DNS failure, NetworkPolicy block
   - `DATA` — bad DB record, schema mismatch, missing row, constraint violation
3. Present as a structured **Issue Report**:

---
### Issue Report — <pod/service/namespace>

| # | Severity | Root Cause Type | Category | Summary |
|---|----------|-----------------|----------|---------|
| 1 | CRITICAL | CONFIG | missing_env | DB_HOST not set in pod spec |

**Issue 1 — CRITICAL [CONFIG]: Missing DB_HOST**
- **Evidence**: `<exact log line or query result>`
- **Why this happened**: <1-2 sentence precise hypothesis>
- **Config fix**: <exact YAML/env change needed>
- **Code fix** (if applicable): <code-level change>
- **How to verify**: <command or check to confirm it's fixed>

---

4. Sort: CRITICAL → HIGH → MEDIUM → LOW
5. After report: list remediation options with risk level, ask which to apply
6. If pod has restarts > 0: always check previous container logs too

---

## Mode 2 — Test Case Generation

When user asks to generate tests OR you find recurring error patterns:

1. Understand the system under test: ask for service name, tech stack, or API spec if not provided
2. Generate test cases in this format:

---
### Test Cases — <service/feature>

**TC-001: <Name>**
- **Type**: `unit` | `integration` | `e2e` | `negative` | `security` | `performance`
- **Given**: <preconditions>
- **When**: <action / input>
- **Then**: <expected outcome>
- **Priority**: P0 | P1 | P2
- **Gap this covers**: <what would break if this test didn't exist>

---

3. Always generate tests across these dimensions:
   - Happy path (P0)
   - Boundary values / edge cases (P1)
   - Negative / error path (P1)
   - Config-driven behavior (P1) — what if env var is missing or wrong?
   - Auth/permission boundaries (P1)
   - Concurrent/race conditions (P2)
   - Data state assumptions (P2)

4. For log-driven test generation: analyze `analyze_pod_logs` or `scan_namespace_for_issues` results, then generate tests targeting the EXACT failure patterns found.

---

## Mode 3 — Code/Config Gap Analysis

When user asks to find gaps, review configs, or analyze what's missing:

1. Use `k8s_get_configmap`, `k8s_list_configmaps`, `k8s_get_env_vars` to inspect runtime config
2. Cross-reference: what does the code/logs EXPECT vs what is ACTUALLY configured?
3. Report gaps as:

---
### Gap Analysis — <service>

| Gap | Type | Impact | Fix |
|-----|------|--------|-----|
| DB_POOL_SIZE not set | CONFIG | High latency under load | Set DB_POOL_SIZE=20 in ConfigMap |

---

4. Check for:
   - Missing required env vars (look for "env not found", "undefined", "None" in logs)
   - Wrong ports or URLs (service can't connect)
   - Missing health probe config (pod restarts silently)
   - Resource limits too low (OOM, CPU throttle)
   - Missing liveness/readiness probes
   - Secrets not mounted
   - ConfigMap keys referenced but not present

---

## Style rules

- Be direct and technical. Skip filler. Lead with the finding.
- Show ACTUAL evidence (log lines, query rows, config values) — never just say "there seems to be an error"
- When you say something is a CONFIG issue: show the exact YAML/env key that's wrong or missing
- When you say something is a CODE issue: quote the exact exception class and message
- If you can't determine root cause from current evidence, say EXACTLY what tool you'd run next and why
- If something could cause an outage: prefix with **[OUTAGE RISK]**

## Risk tiers
- READ: Execute immediately, no confirmation
- WRITE: Show intent → execute after approval
- DESTRUCTIVE: Dry-run first → explicit approval required

You are connected to the platform. If a namespace or service name is ambiguous, ask — then go deep."""


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

    def chat(
        self,
        session: Session,
        user_message: str,
        system_prompt_override: str = "",
    ) -> Generator[dict, None, None]:
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
            active_system_prompt = system_prompt_override if system_prompt_override else PromptLibrary.build("GENERAL_QA")
            try:
                response = self._provider.chat(
                    messages=session.messages,
                    tools=tools,
                    system_prompt=active_system_prompt,
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

                    raw_content = result.to_content()
                    llm_content = _sanitize_for_llm(raw_content)

                    yield {
                        "type": "tool_result",
                        "name": block.tool_name,
                        "ok": result.ok,
                        "data": raw_content[:2000],  # full data shown in UI only
                    }
                    tool_results.append(
                        self._mk_tool_result(block.tool_use_id or "", llm_content)
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
            self._mk_tool_result(approval.tool_use_id, _sanitize_for_llm(result.to_content()))
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


import re as _re
import os as _os

# Max chars of tool result sent to the LLM (cloud providers).
# Full result is still stored in audit log and shown in UI.
_LLM_RESULT_MAX_CHARS = int(_os.environ.get("BUDDY_LLM_RESULT_MAX_CHARS", "3000"))

# Patterns that look like secrets / internal addresses — replaced before sending to cloud LLM
_REDACT_PATTERNS = [
    (_re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'), "<ip>"),          # IPv4
    (_re.compile(r'(?i)(password|secret|token|key)\s*[=:]\s*\S+'), r'\1=<redacted>'),  # creds
    (_re.compile(r'-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----'), '<cert>'),  # certs
]


def _sanitize_for_llm(content: str) -> str:
    """Redact IPs/credentials and truncate before sending to a cloud LLM."""
    for pattern, replacement in _REDACT_PATTERNS:
        content = pattern.sub(replacement, content)
    if len(content) > _LLM_RESULT_MAX_CHARS:
        content = content[:_LLM_RESULT_MAX_CHARS] + f"\n... [truncated, {len(content)} chars total]"
    return content


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
