"""Shared HTTP check helpers for api_validation and security_validation (httpx)."""

from __future__ import annotations

import json
import time
from typing import Any, List, Mapping, Tuple
from urllib.parse import urljoin

import httpx

from qa_agent.validation.api_models import ApiCaseSpec, ApiValidationCaseResult

_RESPONSE_PREVIEW_MAX = 2048


def resolve_url(base_url: str, case_url: str) -> str:
    u = (case_url or "").strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if not base_url.strip():
        raise ValueError(f"relative url {case_url!r} requires plugins base_url")
    base = base_url.rstrip("/") + "/"
    return urljoin(base, u.lstrip("/"))


def validate_http_response(
    spec: ApiCaseSpec,
    status_code: int,
    body_text: str,
    content_type: str,
) -> Tuple[bool, List[str], Mapping[str, Any]]:
    """Apply spec rules; return (ok, validation_errors, detail)."""
    errs: List[str] = []
    detail: dict[str, Any] = {"content_type": content_type}

    if spec.expect_status is not None:
        if status_code != spec.expect_status:
            errs.append(f"expected status {spec.expect_status}, got {status_code}")
    elif spec.expect_status_in:
        if status_code not in spec.expect_status_in:
            errs.append(f"expected status one of {spec.expect_status_in}, got {status_code}")
    else:
        if not (200 <= status_code < 300):
            errs.append(f"expected 2xx status, got {status_code}")

    if spec.expect_body_contains is not None and spec.expect_body_contains not in body_text:
        errs.append("response body does not contain expected substring")

    if spec.expect_json_keys:
        parsed: Any = None
        try:
            parsed = json.loads(body_text)
        except json.JSONDecodeError:
            errs.append("response is not valid JSON but expect_json_keys was set")
        else:
            if not isinstance(parsed, dict):
                errs.append("expected JSON object at root for expect_json_keys")
            else:
                for k in spec.expect_json_keys:
                    if k not in parsed:
                        errs.append(f"missing top-level JSON key {k!r}")

    preview = body_text if len(body_text) <= _RESPONSE_PREVIEW_MAX else body_text[:_RESPONSE_PREVIEW_MAX] + "…"
    detail["response_preview"] = preview

    return (len(errs) == 0, errs, detail)


def execute_http_case(
    client: httpx.Client,
    *,
    base_url: str,
    default_timeout: float,
    spec: ApiCaseSpec,
) -> ApiValidationCaseResult:
    """Perform one HTTP request and validate using :func:`validate_http_response`."""
    t0 = time.perf_counter()
    try:
        resolved = resolve_url(base_url, spec.url)
    except ValueError as exc:
        return ApiValidationCaseResult(
            case_id=spec.id,
            ok=False,
            method=spec.method,
            url=spec.url,
            error=str(exc),
        )

    timeout = spec.timeout_seconds if spec.timeout_seconds is not None else default_timeout
    try:
        resp = client.request(
            spec.method,
            resolved,
            headers=dict(spec.headers),
            content=spec.body.encode("utf-8") if spec.body is not None else None,
            timeout=timeout,
            follow_redirects=spec.follow_redirects,
        )
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        return ApiValidationCaseResult(
            case_id=spec.id,
            ok=False,
            method=spec.method,
            url=resolved,
            latency_ms=latency_ms,
            error=str(exc),
            detail={"error_type": type(exc).__name__},
        )

    latency_ms = (time.perf_counter() - t0) * 1000
    body_text = resp.text
    ctype = resp.headers.get("content-type", "")
    ok, val_errs, detail = validate_http_response(spec, resp.status_code, body_text, ctype)
    return ApiValidationCaseResult(
        case_id=spec.id,
        ok=ok,
        method=spec.method,
        url=resolved,
        status_code=resp.status_code,
        latency_ms=latency_ms,
        validation_errors=val_errs,
        detail=detail,
    )
