"""HTTP tools — test microservices, REST APIs, and health endpoints."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from qa_agent.buddy.tool import BaseTool, RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.tools.http")

_DEFAULT_TIMEOUT = 15.0


# ─────────────────────────────────────────────
# READ tools
# ─────────────────────────────────────────────

class HttpGetTool(BaseTool):
    name = "http_get"
    description = (
        "Make an HTTP GET request to any URL and return status code, headers, "
        "and response body. Use for checking service APIs and health endpoints."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "headers": {"type": "object", "description": "Optional request headers"},
            "timeout": {"type": "number", "description": "Timeout in seconds (default 15)"},
            "follow_redirects": {"type": "boolean", "default": True},
        },
        "required": ["url"],
    }

    def execute(self, params: dict) -> ToolResult:
        return _do_request("GET", params)


class HttpPostTool(BaseTool):
    name = "http_post"
    description = (
        "Make an HTTP POST request with a JSON body. "
        "Use to test API endpoints, trigger service operations, or call microservice APIs."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "body": {"type": "object", "description": "JSON request body"},
            "headers": {"type": "object", "description": "Optional request headers"},
            "timeout": {"type": "number", "default": 15},
        },
        "required": ["url"],
    }

    def execute(self, params: dict) -> ToolResult:
        return _do_request("POST", params)


class HealthCheckTool(BaseTool):
    name = "http_health_check"
    description = (
        "Check the health of a service by hitting its health endpoint. "
        "Checks /health, /healthz, /ready, /ping in order until one returns 2xx. "
        "Returns latency and status for each probe."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Base URL of the service (e.g. http://myservice:8080)"},
            "timeout": {"type": "number", "default": 10},
        },
        "required": ["base_url"],
    }

    def execute(self, params: dict) -> ToolResult:
        base = params["base_url"].rstrip("/")
        timeout = params.get("timeout", 10)
        probes = ["/health", "/healthz", "/ready", "/ping", "/livez"]
        results = []
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=False) as client:
            for path in probes:
                url = f"{base}{path}"
                t0 = time.perf_counter()
                try:
                    resp = client.get(url)
                    ms = round((time.perf_counter() - t0) * 1000)
                    results.append({"path": path, "status": resp.status_code,
                                    "ok": resp.is_success, "latency_ms": ms})
                    if resp.is_success:
                        break
                except httpx.RequestError as e:
                    results.append({"path": path, "error": str(e)})
        overall_ok = any(r.get("ok") for r in results)
        return ToolResult(ok=overall_ok, data={"base_url": base, "probes": results})


class TestEndpointTool(BaseTool):
    name = "http_test_endpoint"
    description = (
        "Test an HTTP endpoint with assertions: expected status code, "
        "response body contains string, JSON keys present, max latency. "
        "Returns pass/fail per assertion."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "default": "GET"},
            "body": {"type": "object"},
            "headers": {"type": "object"},
            "expect_status": {"type": "integer", "description": "Expected HTTP status code"},
            "expect_body_contains": {"type": "string", "description": "Response body must contain this string"},
            "expect_json_keys": {"type": "array", "items": {"type": "string"}},
            "max_latency_ms": {"type": "integer", "description": "Max acceptable response time in ms"},
            "timeout": {"type": "number", "default": 15},
        },
        "required": ["url"],
    }

    def execute(self, params: dict) -> ToolResult:
        method = params.get("method", "GET").upper()
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=params.get("timeout", 15), follow_redirects=True, verify=False) as client:
                kwargs: dict = {
                    "headers": params.get("headers") or {},
                }
                if params.get("body") and method in ("POST", "PUT", "PATCH"):
                    kwargs["json"] = params["body"]
                resp = client.request(method, params["url"], **kwargs)
        except httpx.RequestError as e:
            return ToolResult(ok=False, error=str(e))

        ms = round((time.perf_counter() - t0) * 1000)
        body_text = resp.text[:4000]

        assertions = []
        overall = True

        if params.get("expect_status") is not None:
            ok = resp.status_code == params["expect_status"]
            overall = overall and ok
            assertions.append({"check": "status_code", "expected": params["expect_status"],
                                "actual": resp.status_code, "pass": ok})

        if params.get("expect_body_contains"):
            needle = params["expect_body_contains"]
            ok = needle in body_text
            overall = overall and ok
            assertions.append({"check": "body_contains", "expected": needle, "pass": ok})

        if params.get("expect_json_keys"):
            try:
                j = resp.json()
                for key in params["expect_json_keys"]:
                    ok = key in j
                    overall = overall and ok
                    assertions.append({"check": f"json_key:{key}", "pass": ok})
            except Exception:
                overall = False
                assertions.append({"check": "json_parse", "pass": False, "error": "Response is not valid JSON"})

        if params.get("max_latency_ms") is not None:
            ok = ms <= params["max_latency_ms"]
            overall = overall and ok
            assertions.append({"check": "latency", "max_ms": params["max_latency_ms"],
                                "actual_ms": ms, "pass": ok})

        return ToolResult(ok=overall, data={
            "url": params["url"],
            "method": method,
            "status": resp.status_code,
            "latency_ms": ms,
            "assertions": assertions,
            "body_preview": body_text[:500],
        })


class ServiceDiscoveryTool(BaseTool):
    name = "http_discover_services"
    description = (
        "Given a list of service base URLs, check each one for liveness and return "
        "a health summary. Useful for quickly auditing all microservices."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "services": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["name", "url"],
                },
            },
            "timeout": {"type": "number", "default": 5},
        },
        "required": ["services"],
    }

    def execute(self, params: dict) -> ToolResult:
        timeout = params.get("timeout", 5)
        results = []
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=False) as client:
            for svc in params.get("services", []):
                t0 = time.perf_counter()
                try:
                    resp = client.get(svc["url"].rstrip("/") + "/health")
                    ms = round((time.perf_counter() - t0) * 1000)
                    results.append({
                        "name": svc["name"], "url": svc["url"],
                        "status": resp.status_code, "ok": resp.is_success, "latency_ms": ms,
                    })
                except httpx.RequestError as e:
                    results.append({"name": svc["name"], "url": svc["url"], "ok": False, "error": str(e)})
        healthy = sum(1 for r in results if r.get("ok"))
        return ToolResult(ok=healthy == len(results), data={
            "total": len(results), "healthy": healthy, "unhealthy": len(results) - healthy,
            "services": results,
        })


# ─────────────────────────────────────────────
# WRITE tool
# ─────────────────────────────────────────────

class HttpWriteTool(BaseTool):
    name = "http_write"
    description = (
        "Make an HTTP PUT, PATCH, or DELETE request to a service API. "
        "Use for mutating operations — requires user approval."
    )
    risk_level = RiskLevel.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["PUT", "PATCH", "DELETE"]},
            "body": {"type": "object"},
            "headers": {"type": "object"},
            "timeout": {"type": "number", "default": 15},
        },
        "required": ["url", "method"],
    }

    def execute(self, params: dict) -> ToolResult:
        return _do_request(params["method"], params)


# ─────────────────────────────────────────────
# Shared helper
# ─────────────────────────────────────────────

def _do_request(method: str, params: dict) -> ToolResult:
    t0 = time.perf_counter()
    try:
        with httpx.Client(
            timeout=params.get("timeout", _DEFAULT_TIMEOUT),
            follow_redirects=params.get("follow_redirects", True),
            verify=False,
        ) as client:
            kwargs: dict = {"headers": params.get("headers") or {}}
            if params.get("body") and method in ("POST", "PUT", "PATCH"):
                kwargs["json"] = params["body"]
            resp = client.request(method, params["url"], **kwargs)
    except httpx.RequestError as e:
        return ToolResult(ok=False, error=str(e))

    ms = round((time.perf_counter() - t0) * 1000)
    content_type = resp.headers.get("content-type", "")
    try:
        body: Any = resp.json() if "json" in content_type else resp.text[:4000]
    except Exception:
        body = resp.text[:4000]

    return ToolResult(ok=resp.is_success, data={
        "status": resp.status_code,
        "latency_ms": ms,
        "content_type": content_type,
        "body": body,
    })


def all_http_tools() -> list[BaseTool]:
    return [
        HttpGetTool(),
        HttpPostTool(),
        HealthCheckTool(),
        TestEndpointTool(),
        ServiceDiscoveryTool(),
        HttpWriteTool(),
    ]
