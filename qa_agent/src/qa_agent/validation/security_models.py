"""Security / access-control HTTP checks — same transport as API validation, different semantics."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from qa_agent.validation.api_models import ApiCaseSpec, ApiValidationCaseResult


class SecurityCheckSpec(ApiCaseSpec):
    """
    HTTP check framed for permission / access outcomes.

    ``access_intent`` sets defaults when explicit expectations are omitted:

    * **denied** — expect HTTP **401** or **403** (typical unauthenticated / forbidden).
    * **allowed** — expect **2xx** (same default as :class:`ApiCaseSpec` with no status rules).

    Override with ``expect_status`` / ``expect_status_in`` when you need exact codes.
    """

    access_intent: Optional[Literal["denied", "allowed"]] = Field(
        default=None,
        description="Shorthand for common access expectations; optional.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Opaque label for dashboards; not interpreted.",
    )


def to_effective_api_spec(spec: SecurityCheckSpec) -> ApiCaseSpec:
    """Apply access_intent defaults, then validate as a plain :class:`ApiCaseSpec`."""
    data = spec.model_dump(exclude={"access_intent", "notes"})
    if spec.access_intent == "denied":
        if spec.expect_status is None and not spec.expect_status_in:
            data["expect_status_in"] = [401, 403]
    return ApiCaseSpec.model_validate(data)


class SecurityValidationCaseResult(ApiValidationCaseResult):
    """HTTP case outcome plus optional security metadata."""

    model_config = ConfigDict(extra="forbid")

    access_intent: Optional[str] = None
    notes: Optional[str] = None


class SecurityValidationSummary(BaseModel):
    """Aggregate for :class:`~qa_agent.core.run_metadata.ValidatorMetadata.security_validation`."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="completed | skipped | failed")
    checks_run: int = 0
    checks_passed: int = 0
    failed: Optional[bool] = None
    cases: List[SecurityValidationCaseResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


def wrap_security_case_result(
    api: ApiValidationCaseResult,
    spec: SecurityCheckSpec,
) -> SecurityValidationCaseResult:
    """Attach security metadata to a shared HTTP case result."""
    return SecurityValidationCaseResult(
        **api.model_dump(),
        access_intent=spec.access_intent,
        notes=spec.notes,
    )
