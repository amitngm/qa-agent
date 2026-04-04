"""Log analysis tools — pattern-match pod logs for issues and suggest fixes."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any

from qa_agent.buddy.tool import BaseTool, RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.tools.log_analysis")

# ── Issue patterns ────────────────────────────────────────────────────────────
# Each entry: (compiled regex, category, severity)
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r'\b(OOMKill|OutOfMemory|out of memory|Killed process|memory limit)\b', re.I), "oom", "critical"),
    (re.compile(r'(CrashLoopBackOff|crash loop|container .* died|process exited with code [^0])', re.I), "crash_loop", "critical"),
    (re.compile(r'(Traceback \(most recent|panic:|fatal error:|runtime error:|segfault|SIGSEGV)', re.I), "exception", "critical"),
    (re.compile(r'\b(Exception|Error)\b.*', re.I), "exception", "high"),
    (re.compile(r'\b(FATAL|CRITICAL|SEVERE)\b', re.I), "fatal", "critical"),
    (re.compile(r'\bERROR\b', re.I), "error", "high"),
    (re.compile(r'(Connection refused|ECONNREFUSED|dial tcp.*refused|no route to host)', re.I), "connectivity", "high"),
    (re.compile(r'(timeout|Timeout|TIMEOUT|context deadline exceeded|i/o timeout)', re.I), "timeout", "high"),
    (re.compile(r'(403 Forbidden|401 Unauthorized|permission denied|Access Denied|not authorized)', re.I), "auth", "medium"),
    (re.compile(r'(404 Not Found|no such file|file not found|resource not found|NoSuchKey)', re.I), "not_found", "medium"),
    (re.compile(r'(no space left|disk full|Disk quota|ENOSPC|storage.*full)', re.I), "storage", "high"),
    (re.compile(r'\bWARN(ING)?\b', re.I), "warning", "low"),
]

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_FIX_HINTS: dict[str, str] = {
    "oom": (
        "Increase memory limits/requests in the pod spec. "
        "Profile the application for memory leaks. "
        "Consider enabling JVM heap dumps or Go pprof to find the leak."
    ),
    "crash_loop": (
        "Check the previous container logs (previous=true) for the crash reason. "
        "Verify liveness probe settings — too aggressive probes can cause false restarts. "
        "Ensure startup dependencies (DB, config) are available before the pod starts."
    ),
    "exception": (
        "Review the full stack trace to find the root call. "
        "Check recent deployments or config changes that may have introduced the regression. "
        "Add error handling or circuit breakers if the exception is from an external call."
    ),
    "fatal": (
        "This is a critical log level — the process may have exited. "
        "Check if the pod restarted (k8s_describe_pod). "
        "Review application config and secrets for misconfigurations."
    ),
    "error": (
        "Investigate the specific error message. "
        "Check if the error is transient (network blip) or persistent (config/code bug). "
        "Correlate with deployment history and recent config changes."
    ),
    "connectivity": (
        "Verify the target service is running (k8s_list_pods, http_health_check). "
        "Check NetworkPolicies that may block traffic. "
        "Confirm DNS resolution works inside the pod (k8s_exec with nslookup)."
    ),
    "timeout": (
        "Check if the downstream service is overloaded (high latency, pod restarts). "
        "Review timeout configuration — may need to increase client-side timeouts. "
        "Inspect resource limits; CPU throttling can cause apparent timeouts."
    ),
    "auth": (
        "Verify secrets and service account tokens are correctly mounted. "
        "Check if certificates have expired. "
        "Review RBAC policies — service account may be missing required permissions."
    ),
    "not_found": (
        "Confirm the resource (file, endpoint, ConfigMap key) exists. "
        "Check if a recent deployment removed or renamed the resource. "
        "Verify environment variables pointing to paths/URLs are correct."
    ),
    "storage": (
        "Run df -h inside the pod to confirm disk usage (k8s_exec). "
        "Delete old logs or temporary files. "
        "Consider expanding the PVC or adding a log rotation policy."
    ),
    "warning": (
        "Warnings often precede errors — monitor for escalation. "
        "Review warning context to determine if action is needed now."
    ),
}


def _get_context(lines: list[str], idx: int, window: int = 2) -> list[str]:
    start = max(0, idx - window)
    end = min(len(lines), idx + window + 1)
    return lines[start:end]


def _analyze_lines(lines: list[str]) -> list[dict]:
    """Scan log lines and return deduplicated issues sorted by severity."""
    issues: list[dict] = []
    seen_messages: set[str] = set()

    for i, line in enumerate(lines):
        for pattern, category, severity in _PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            # Deduplicate near-identical messages (same category + first 80 chars)
            dedup_key = f"{category}:{line[:80]}"
            if dedup_key in seen_messages:
                break
            seen_messages.add(dedup_key)
            issues.append({
                "severity": severity,
                "category": category,
                "line_number": i + 1,
                "message": line.strip(),
                "context": _get_context(lines, i),
                "suggested_fix": _FIX_HINTS.get(category, "Review the log context for more details."),
            })
            break  # one issue per line (highest-priority pattern wins)

    issues.sort(key=lambda x: _SEVERITY_ORDER.get(x["severity"], 99))
    return issues


def _fetch_pod_logs(namespace: str, pod_name: str, container: str | None,
                    tail_lines: int, previous: bool) -> str:
    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        v1 = client.CoreV1Api()
        kwargs: dict[str, Any] = {
            "namespace": namespace,
            "name": pod_name,
            "tail_lines": tail_lines,
        }
        if container:
            kwargs["container"] = container
        if previous:
            kwargs["previous"] = True
        return v1.read_namespaced_pod_log(**kwargs) or ""
    except ImportError:
        raise RuntimeError("kubernetes package not installed")


def _list_pods_in_namespace(namespace: str) -> list[str]:
    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        v1 = client.CoreV1Api()
        if namespace == "all":
            pods = v1.list_pod_for_all_namespaces()
        else:
            pods = v1.list_namespaced_pod(namespace=namespace)
        return [(p.metadata.namespace, p.metadata.name) for p in pods.items]
    except ImportError:
        raise RuntimeError("kubernetes package not installed")


# ── Tools ─────────────────────────────────────────────────────────────────────

class AnalyzePodLogsTool(BaseTool):
    name = "analyze_pod_logs"
    description = (
        "Fetch logs from a pod and automatically scan them for errors, crashes, OOM kills, "
        "timeouts, connectivity failures, auth issues, and other problems. "
        "Returns a structured list of issues with severity, category, the exact log line, "
        "context, and a concrete suggested fix for each issue."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "description": "Kubernetes namespace"},
            "pod_name": {"type": "string", "description": "Pod name"},
            "container": {"type": "string", "description": "Container name (optional, uses first container if omitted)"},
            "tail_lines": {"type": "integer", "description": "Number of recent log lines to scan (default 500)", "default": 500},
            "previous": {"type": "boolean", "description": "Analyze logs from previous (crashed) container instance", "default": False},
        },
        "required": ["namespace", "pod_name"],
    }

    def execute(self, params: dict) -> ToolResult:
        namespace = params["namespace"]
        pod_name = params["pod_name"]
        container = params.get("container")
        tail_lines = params.get("tail_lines", 500)
        previous = params.get("previous", False)
        try:
            raw_logs = _fetch_pod_logs(namespace, pod_name, container, tail_lines, previous)
        except Exception as e:
            return ToolResult(ok=False, error=str(e))

        lines = raw_logs.splitlines()
        issues = _analyze_lines(lines)

        return ToolResult(ok=True, data={
            "pod": pod_name,
            "namespace": namespace,
            "scanned_lines": len(lines),
            "issue_count": len(issues),
            "issues": issues,
            "summary": (
                f"Found {len(issues)} issue(s) in {len(lines)} log lines."
                if issues else
                f"No issues detected in {len(lines)} log lines."
            ),
        })


class ScanNamespaceForIssuesTool(BaseTool):
    name = "scan_namespace_for_issues"
    description = (
        "Scan all pods in a namespace (or all namespaces) for log issues. "
        "For each pod, fetches recent logs and detects errors, crashes, OOM kills, "
        "timeouts, connectivity failures, and other problems. "
        "Returns an aggregated report sorted by severity, highlighting the most critical pods first."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "description": "Namespace to scan, or 'all' for all namespaces"},
            "tail_lines": {"type": "integer", "description": "Log lines to check per pod (default 200)", "default": 200},
            "max_pods": {"type": "integer", "description": "Maximum pods to scan (default 20, max 50)", "default": 20},
        },
        "required": ["namespace"],
    }

    def execute(self, params: dict) -> ToolResult:
        namespace = params["namespace"]
        tail_lines = params.get("tail_lines", 200)
        max_pods = min(params.get("max_pods", 20), 50)

        try:
            pod_list = _list_pods_in_namespace(namespace)
        except Exception as e:
            return ToolResult(ok=False, error=str(e))

        pod_list = pod_list[:max_pods]
        pod_reports: list[dict] = []
        total_issues = 0
        skipped = 0

        for ns, pod_name in pod_list:
            try:
                raw_logs = _fetch_pod_logs(ns, pod_name, None, tail_lines, False)
                lines = raw_logs.splitlines()
                issues = _analyze_lines(lines)
                total_issues += len(issues)
                pod_reports.append({
                    "pod": pod_name,
                    "namespace": ns,
                    "issue_count": len(issues),
                    "top_severity": issues[0]["severity"] if issues else "none",
                    "issues": issues,
                })
            except Exception as e:
                skipped += 1
                log.debug("could not fetch logs for %s/%s: %s", ns, pod_name, e)

        # Sort pods: most critical first, then by issue count
        pod_reports.sort(key=lambda r: (
            _SEVERITY_ORDER.get(r["top_severity"], 99),
            -r["issue_count"],
        ))

        healthy = sum(1 for r in pod_reports if r["issue_count"] == 0)
        affected = len(pod_reports) - healthy

        return ToolResult(ok=True, data={
            "namespace": namespace,
            "pods_scanned": len(pod_reports),
            "pods_skipped": skipped,
            "pods_with_issues": affected,
            "pods_healthy": healthy,
            "total_issues": total_issues,
            "summary": (
                f"Scanned {len(pod_reports)} pods: {affected} have issues, {healthy} are clean."
            ),
            "pod_reports": pod_reports,
        })


def all_log_analysis_tools() -> list[BaseTool]:
    return [AnalyzePodLogsTool(), ScanNamespaceForIssuesTool()]
