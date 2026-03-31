"""Page-level validation models — output of the page_validator pipeline stage."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class PageCheckResult(BaseModel):
    """Result of one validation rule applied to a single page."""

    model_config = ConfigDict(extra="forbid")

    rule: str
    passed: bool
    severity: Literal["fail", "warn", "info"] = "fail"
    detail: str = ""


class PageValidationResult(BaseModel):
    """Aggregated validation outcome for one visited page."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    feature: str = ""
    all_features: List[str] = Field(default_factory=list)
    passed: bool = True
    has_warnings: bool = False
    checks: List[PageCheckResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class FeatureValidationGroup(BaseModel):
    """Roll-up of page validation results for one feature."""

    model_config = ConfigDict(extra="forbid")

    feature: str
    pages_total: int = 0
    pages_passed: int = 0
    pages_failed: int = 0
    pages_warned: int = 0
    pages: List[PageValidationResult] = Field(default_factory=list)


class PageValidationSummary(BaseModel):
    """Top-level output stored under ``validator.page_validation`` on RunMetadata."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="completed | skipped | failed")
    pages_total: int = 0
    pages_passed: int = 0
    pages_failed: int = 0
    pages_warned: int = 0
    checks_run: int = 0
    checks_passed: int = 0
    rules_applied: List[str] = Field(default_factory=list)
    features: List[FeatureValidationGroup] = Field(default_factory=list)
    untagged_pages: List[PageValidationResult] = Field(default_factory=list)
    failed: bool = False
    skip_reason: Optional[str] = None
