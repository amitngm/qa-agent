"""Structured UI automation run results — driver-agnostic shapes for metadata and reports."""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field


class UiStepResult(BaseModel):
    """One configured step outcome (maps to :class:`~qa_agent.platform.types.DriverResult`)."""

    model_config = ConfigDict(extra="forbid")

    step_index: int
    op: str
    ok: bool
    detail: Mapping[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class UiAutomationSummary(BaseModel):
    """Aggregate for ``executor.ui_automation`` in run metadata."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="completed | skipped | failed")
    driver: str = Field(default="playwright", description="Driver implementation id.")
    browser: str = Field(default="chromium", description="Browser channel used when applicable.")
    steps_run: int = 0
    steps_passed: int = 0
    failed: Optional[bool] = None
    steps: List[UiStepResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
