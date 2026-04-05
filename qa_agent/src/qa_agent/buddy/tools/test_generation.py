"""Test generation tools — generate test cases from log patterns, API specs, and error history."""

from __future__ import annotations

import json
import re
import logging
from collections import Counter
from typing import Any

from qa_agent.buddy.tool import BaseTool, RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.tools.test_generation")


# ── Pattern → test case templates ────────────────────────────────────────────

_ERROR_TO_TEST_TEMPLATES: dict[str, list[dict]] = {
    "oom": [
        {
            "name": "Memory limit respected under sustained load",
            "type": "performance",
            "priority": "P0",
            "given": "Pod has memory limit set in deployment spec",
            "when": "Service receives sustained high traffic for 5 minutes",
            "then": "Pod does not get OOMKilled; memory usage stays below 85% of limit",
            "gap": "Catches memory leaks or missing limits that cause OOMKill in prod",
        },
        {
            "name": "Graceful degradation when memory pressure is high",
            "type": "negative",
            "priority": "P1",
            "given": "Pod is running at 90% memory utilization",
            "when": "A new large request arrives",
            "then": "Service returns 503 or sheds load instead of crashing",
            "gap": "Missing circuit breaker or backpressure handling",
        },
    ],
    "crash_loop": [
        {
            "name": "Service starts successfully when all dependencies are available",
            "type": "integration",
            "priority": "P0",
            "given": "Database, config service, and dependent microservices are running",
            "when": "Pod starts",
            "then": "Container reaches Running state within 60 seconds; no restarts",
            "gap": "Catches startup race conditions and missing readiness gates",
        },
        {
            "name": "Startup fails fast and clearly when a required dependency is missing",
            "type": "negative",
            "priority": "P1",
            "given": "Required DB_HOST environment variable is not set",
            "when": "Pod starts",
            "then": "Container exits with non-zero code and logs a clear error message naming the missing config",
            "gap": "Missing startup config validation — pod silently crash-loops instead of failing clearly",
        },
    ],
    "connectivity": [
        {
            "name": "Service retries transient connection failures with backoff",
            "type": "integration",
            "priority": "P1",
            "given": "Downstream service is temporarily unavailable for 5 seconds",
            "when": "A request is made that requires the downstream service",
            "then": "Client retries with exponential backoff and eventually succeeds; no data loss",
            "gap": "No retry logic — any transient blip causes user-visible errors",
        },
        {
            "name": "Service returns 503 when downstream is fully down",
            "type": "negative",
            "priority": "P0",
            "given": "Downstream service is completely unavailable",
            "when": "A request requiring downstream is made",
            "then": "Response is 503 with descriptive error; upstream service does not crash",
            "gap": "No fallback — downstream failure propagates to full service crash",
        },
        {
            "name": "NetworkPolicy allows required service-to-service traffic",
            "type": "integration",
            "priority": "P1",
            "given": "NetworkPolicy is applied to the namespace",
            "when": "Service A calls Service B on the expected port",
            "then": "Call succeeds within timeout; no connection refused",
            "gap": "NetworkPolicy too restrictive — blocks legitimate internal traffic",
        },
    ],
    "timeout": [
        {
            "name": "Requests complete within SLA under normal load",
            "type": "performance",
            "priority": "P0",
            "given": "System is under normal load (< 80% capacity)",
            "when": "A standard API request is made",
            "then": "Response received within the configured timeout (e.g. < 5s)",
            "gap": "No latency SLA validation — slow responses only discovered by users",
        },
        {
            "name": "Client-side timeout is shorter than server-side timeout",
            "type": "unit",
            "priority": "P1",
            "given": "Client has a 10s timeout; server processes up to 30s",
            "when": "A slow request is made",
            "then": "Client times out at 10s and returns error, not waiting 30s",
            "gap": "Timeout misconfiguration — requests hang for server's full timeout window",
        },
    ],
    "auth": [
        {
            "name": "Expired token returns 401 with clear message",
            "type": "negative",
            "priority": "P0",
            "given": "User has an expired JWT token",
            "when": "Request is made with the expired token",
            "then": "API returns 401 with JSON body indicating token expiry",
            "gap": "Missing token expiry handling — service may crash or return 500",
        },
        {
            "name": "Access denied returns 403 (not 404) for unauthorized resources",
            "type": "security",
            "priority": "P0",
            "given": "User has a valid token but lacks permission for a resource",
            "when": "User requests that resource",
            "then": "API returns 403 Forbidden (not 404, which leaks resource existence)",
            "gap": "Information disclosure via 404 on forbidden resources",
        },
        {
            "name": "Service account has only required RBAC permissions",
            "type": "security",
            "priority": "P1",
            "given": "Service account is deployed with RBAC rules",
            "when": "Service account token is used to call K8s API",
            "then": "Only permitted operations succeed; over-privileged operations fail",
            "gap": "Overly broad RBAC grants — compromised pod has excessive cluster access",
        },
    ],
    "not_found": [
        {
            "name": "Missing ConfigMap key fails at startup with clear error",
            "type": "negative",
            "priority": "P0",
            "given": "A required ConfigMap key is absent",
            "when": "Application starts and tries to read the config value",
            "then": "Startup fails immediately with log message naming the missing key",
            "gap": "No config validation at startup — silent null/default value causes subtle runtime bugs",
        },
        {
            "name": "404 from API is handled gracefully",
            "type": "negative",
            "priority": "P1",
            "given": "Resource does not exist",
            "when": "API is called for that resource",
            "then": "Returns 404 with structured error body; does not propagate as 500",
            "gap": "Missing 404 handling — upstream gets unhandled exception",
        },
    ],
    "storage": [
        {
            "name": "Service handles disk-full condition gracefully",
            "type": "negative",
            "priority": "P1",
            "given": "PVC is at 95% capacity",
            "when": "A write operation is attempted",
            "then": "Write returns clear error; service continues accepting reads; alert is triggered",
            "gap": "No disk space guard — silent write failure or service crash",
        },
    ],
    "exception": [
        {
            "name": "Unhandled exceptions return 500 with request ID (not stack trace)",
            "type": "negative",
            "priority": "P0",
            "given": "Service encounters an unexpected code path",
            "when": "An unhandled exception occurs",
            "then": "API returns 500 with request_id in body; full stack trace in logs only (not response)",
            "gap": "Stack traces in API responses leak internals; missing correlation ID makes debugging hard",
        },
        {
            "name": "Input validation rejects malformed requests with 400",
            "type": "negative",
            "priority": "P1",
            "given": "API endpoint requires specific input format",
            "when": "Malformed or missing required fields are submitted",
            "then": "Returns 400 with field-level validation errors; no exception thrown",
            "gap": "Missing input validation — malformed data reaches business logic and causes exceptions",
        },
    ],
}


def _generate_from_issues(issues: list[dict]) -> list[dict]:
    """Given a list of analyzed issues, return deduped test cases sorted by priority."""
    seen_names: set[str] = set()
    test_cases = []
    tc_num = 1

    for issue in issues:
        category = issue.get("category", "")
        templates = _ERROR_TO_TEST_TEMPLATES.get(category, [])
        for tmpl in templates:
            if tmpl["name"] in seen_names:
                continue
            seen_names.add(tmpl["name"])
            test_cases.append({
                "id": f"TC-{tc_num:03d}",
                "name": tmpl["name"],
                "type": tmpl["type"],
                "priority": tmpl["priority"],
                "triggered_by": f"{issue['severity'].upper()} {category} issue in logs",
                "given": tmpl["given"],
                "when": tmpl["when"],
                "then": tmpl["then"],
                "gap_covered": tmpl["gap"],
            })
            tc_num += 1

    # Sort by priority P0 → P1 → P2
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    test_cases.sort(key=lambda x: priority_order.get(x["priority"], 9))
    return test_cases


def _generate_api_test_cases(
    endpoint: str, method: str, description: str,
    status_codes: list[int], auth_required: bool,
) -> list[dict]:
    """Generate standard API test matrix for a given endpoint."""
    tests = []
    tc_num = 1

    def add(name, typ, priority, given, when, then_, gap):
        nonlocal tc_num
        tests.append({
            "id": f"TC-{tc_num:03d}", "name": name, "type": typ, "priority": priority,
            "given": given, "when": f"{method} {endpoint} — {when}", "then": then_, "gap_covered": gap,
        })
        tc_num += 1

    add(
        f"{method} {endpoint} — happy path",
        "integration", "P0",
        "Service is running and all dependencies are available",
        "valid request is sent with correct payload and auth",
        f"Returns {status_codes[0] if status_codes else 200} with expected response schema",
        "Baseline: endpoint is reachable and returns expected format",
    )
    add(
        f"{method} {endpoint} — missing required fields",
        "negative", "P1",
        "Endpoint expects specific required fields",
        "request is sent with missing required fields",
        "Returns 400 with field-level validation error; not 500",
        "Missing input validation causes server exception",
    )
    add(
        f"{method} {endpoint} — response time under SLA",
        "performance", "P1",
        "System is under normal load",
        "valid request is sent",
        "Response received in < 2000ms",
        "No latency SLA — slow responses go undetected",
    )
    if auth_required:
        add(
            f"{method} {endpoint} — no auth token returns 401",
            "security", "P0",
            "Endpoint requires authentication",
            "request is sent with no Authorization header",
            "Returns 401 Unauthorized; not 200 or 500",
            "Missing auth check — endpoint accessible without credentials",
        )
        add(
            f"{method} {endpoint} — invalid token returns 401",
            "security", "P0",
            "Endpoint requires authentication",
            "request is sent with tampered or expired token",
            "Returns 401 Unauthorized with clear error message",
            "Expired/invalid tokens accepted — authentication bypass",
        )
    add(
        f"{method} {endpoint} — returns correct Content-Type header",
        "integration", "P2",
        "Endpoint returns JSON",
        "valid request is sent",
        "Response has Content-Type: application/json header",
        "Missing Content-Type causes client parsing failures",
    )
    if method in ("POST", "PUT", "PATCH"):
        add(
            f"{method} {endpoint} — idempotency / duplicate request handling",
            "integration", "P1",
            "Endpoint performs a state mutation",
            "same request is sent twice with the same payload",
            "Second request does not create duplicate data; returns expected status",
            "No idempotency — duplicate submissions create duplicate records",
        )

    return tests


# ── Tools ─────────────────────────────────────────────────────────────────────

class GenerateTestsFromLogsTool(BaseTool):
    name = "generate_tests_from_logs"
    description = (
        "Given the output from analyze_pod_logs or scan_namespace_for_issues, "
        "generate a prioritized test case suite targeting the EXACT failure patterns found. "
        "Each test case includes type, priority, Given/When/Then, and what gap it covers. "
        "Pass the full issues list from the log analysis result."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "description": "List of issue dicts from analyze_pod_logs or scan_namespace_for_issues",
                "items": {"type": "object"},
            },
            "service_name": {
                "type": "string",
                "description": "Name of the service being tested (for labeling)",
            },
        },
        "required": ["issues"],
    }

    def execute(self, params: dict) -> ToolResult:
        issues = params.get("issues") or []
        if not issues:
            return ToolResult(ok=True, data={
                "test_cases": [],
                "summary": "No issues provided — no targeted test cases generated.",
                "note": "Run analyze_pod_logs or scan_namespace_for_issues first, then pass the issues list here.",
            })

        test_cases = _generate_from_issues(issues)
        categories = Counter(i.get("category") for i in issues)
        severities = Counter(i.get("severity") for i in issues)

        return ToolResult(ok=True, data={
            "service": params.get("service_name", "unknown"),
            "issues_analyzed": len(issues),
            "issue_breakdown": dict(categories),
            "severity_breakdown": dict(severities),
            "test_cases_generated": len(test_cases),
            "test_cases": test_cases,
            "summary": (
                f"Generated {len(test_cases)} test cases from {len(issues)} log issues "
                f"across {len(categories)} error categories."
            ),
        })


class GenerateApiTestsTool(BaseTool):
    name = "generate_api_tests"
    description = (
        "Generate a standard API test matrix for a given endpoint: "
        "happy path, negative cases, auth checks, performance, and idempotency. "
        "Produces Given/When/Then test cases with priority and gap analysis."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "endpoint": {"type": "string", "description": "API path, e.g. /api/v1/users"},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "description": "HTTP method",
            },
            "description": {
                "type": "string",
                "description": "Short description of what the endpoint does",
                "default": "",
            },
            "expected_status_codes": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Expected success status codes (e.g. [200, 201])",
                "default": [200],
            },
            "auth_required": {
                "type": "boolean",
                "description": "Whether the endpoint requires authentication",
                "default": True,
            },
        },
        "required": ["endpoint", "method"],
    }

    def execute(self, params: dict) -> ToolResult:
        endpoint = params["endpoint"]
        method = params["method"].upper()
        description = params.get("description", "")
        status_codes = params.get("expected_status_codes", [200])
        auth_required = params.get("auth_required", True)

        test_cases = _generate_api_test_cases(endpoint, method, description, status_codes, auth_required)

        return ToolResult(ok=True, data={
            "endpoint": endpoint,
            "method": method,
            "test_cases_generated": len(test_cases),
            "test_cases": test_cases,
            "summary": f"Generated {len(test_cases)} test cases for {method} {endpoint}",
        })


class AnalyzeErrorFrequencyTool(BaseTool):
    name = "analyze_error_frequency"
    description = (
        "Given raw log text, count the frequency of each unique error/exception type "
        "and identify which errors are most common, recurring, or novel. "
        "Use this to prioritize which issues to fix first and which tests to write. "
        "Returns a ranked breakdown of error patterns with occurrence counts."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "log_text": {
                "type": "string",
                "description": "Raw log content to analyze",
            },
            "top_n": {
                "type": "integer",
                "description": "Return top N most frequent errors (default 20)",
                "default": 20,
            },
        },
        "required": ["log_text"],
    }

    def execute(self, params: dict) -> ToolResult:
        log_text = params.get("log_text", "")
        top_n = params.get("top_n", 20)

        lines = log_text.splitlines()

        # Extract exception/error identifiers
        exception_pattern = re.compile(
            r'([A-Z][a-zA-Z]*(?:Exception|Error|Failure|Fault|Panic|Fatal)[A-Za-z]*)', re.MULTILINE
        )
        error_line_pattern = re.compile(r'\b(ERROR|FATAL|CRITICAL|SEVERE)\b.*', re.IGNORECASE)
        stack_trace_pattern = re.compile(r'Traceback \(most recent call last\)', re.IGNORECASE)

        exception_counter: Counter = Counter()
        error_message_counter: Counter = Counter()
        stack_trace_count = 0

        for line in lines:
            for exc in exception_pattern.findall(line):
                exception_counter[exc] += 1
            if error_line_pattern.search(line):
                # Take first 80 chars as the key (strips noise)
                key = line.strip()[:80]
                error_message_counter[key] += 1
            if stack_trace_pattern.search(line):
                stack_trace_count += 1

        top_exceptions = [
            {"exception": exc, "count": cnt, "likely_root_cause": _classify_exception(exc)}
            for exc, cnt in exception_counter.most_common(top_n)
        ]
        top_errors = [
            {"message": msg, "count": cnt}
            for msg, cnt in error_message_counter.most_common(top_n)
        ]

        return ToolResult(ok=True, data={
            "total_lines": len(lines),
            "stack_traces_found": stack_trace_count,
            "unique_exception_types": len(exception_counter),
            "unique_error_messages": len(error_message_counter),
            "top_exceptions": top_exceptions,
            "top_error_messages": top_errors,
            "summary": (
                f"Found {len(exception_counter)} unique exception types and "
                f"{len(error_message_counter)} unique error messages across {len(lines)} log lines. "
                f"{stack_trace_count} stack traces detected."
            ),
        })


def _classify_exception(exc_name: str) -> str:
    name_lower = exc_name.lower()
    if any(x in name_lower for x in ("nullpointer", "nullreference", "attributeerror", "typeerror")):
        return "CODE — null/type handling"
    if any(x in name_lower for x in ("connection", "socket", "network", "timeout", "eof")):
        return "NETWORK — connectivity or timeout"
    if any(x in name_lower for x in ("auth", "permission", "unauthorized", "forbidden", "access")):
        return "AUTH — permission or credential issue"
    if any(x in name_lower for x in ("config", "property", "setting", "env", "notfound", "missing")):
        return "CONFIG — missing or wrong configuration"
    if any(x in name_lower for x in ("database", "sql", "jdbc", "integrity", "constraint")):
        return "DATA — database or data integrity issue"
    if any(x in name_lower for x in ("memory", "heap", "oom", "outofmemory")):
        return "INFRA — memory pressure or OOM"
    if any(x in name_lower for x in ("io", "file", "disk", "storage")):
        return "INFRA — I/O or storage issue"
    return "UNKNOWN — review stack trace"


def all_test_generation_tools() -> list[BaseTool]:
    return [
        GenerateTestsFromLogsTool(),
        GenerateApiTestsTool(),
        AnalyzeErrorFrequencyTool(),
    ]
