"""Generic API contract / HTTP check models — config specs and structured run results."""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiCaseSpec(BaseModel):
    """One HTTP check (YAML / JSON config)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable identifier for reports and correlation.")
    method: str = "GET"
    url: str = Field(..., description="Absolute URL, or path relative to plugin base_url.")
    headers: Mapping[str, str] = Field(default_factory=dict)
    body: Optional[str] = Field(default=None, description="Raw request body (e.g. JSON string).")
    timeout_seconds: Optional[float] = Field(
        default=None,
        description="Override plugin default_timeout_seconds for this case.",
    )
    follow_redirects: bool = True
    expect_status: Optional[int] = Field(
        default=None,
        description="If set, response status code must equal this value.",
    )
    expect_status_in: Optional[List[int]] = Field(
        default=None,
        description="If set (and expect_status is not), status must be one of these.",
    )
    expect_body_contains: Optional[str] = Field(
        default=None,
        description="If set, response body must contain this substring.",
    )
    expect_json_keys: Optional[List[str]] = Field(
        default=None,
        description="If set, parsed JSON object must contain these top-level keys.",
    )

    @field_validator("method")
    @classmethod
    def _upper_method(cls, v: str) -> str:
        return (v or "GET").upper()


class ApiValidationCaseResult(BaseModel):
    """Structured outcome for a single :class:`ApiCaseSpec`."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    ok: bool
    method: str = "GET"
    url: str = ""
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    validation_errors: List[str] = Field(default_factory=list)
    detail: Mapping[str, Any] = Field(
        default_factory=dict,
        description="Opaque extras e.g. response_preview, content_type.",
    )


class ApiValidationSummary(BaseModel):
    """Aggregate result for :class:`~qa_agent.core.run_metadata.ValidatorMetadata.api_validation`."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        ...,
        description="completed | skipped | failed (failed = plugin error before cases).",
    )
    checks_run: int = 0
    checks_passed: int = 0
    failed: Optional[bool] = Field(
        default=None,
        description="True if any case failed (for correlation / analysis).",
    )
    cases: List[ApiValidationCaseResult] = Field(default_factory=list)
    errors: List[str] = Field(
        default_factory=list,
        description="Plugin-level errors (e.g. config invalid).",
    )
