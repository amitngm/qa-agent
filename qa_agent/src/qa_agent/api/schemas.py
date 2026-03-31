"""HTTP request and response models for the trigger API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from qa_agent.core.run_metadata import RunMetadata
from qa_agent.core.status import StepFailureMode
from qa_agent.core.types import RunStatus, StepStatus


class AutoExploreRequest(BaseModel):
    """Payload for generic UI auto-exploration (password is never stored on run metadata)."""

    target_url: str = ""
    application: Optional[str] = None
    username: str = ""
    password: str = ""
    login_strategy: Literal["auto_detect", "manual_hints"] = "auto_detect"
    max_pages: int = Field(10, ge=1)
    safe_mode: bool = True
    headless: bool = True
    explore_mode: Literal["full", "selective"] = "full"
    selected_features: List[str] = Field(default_factory=list)
    username_selector: Optional[str] = None
    password_selector: Optional[str] = None
    login_button_selector: Optional[str] = None
    success_marker: Optional[str] = None

    @field_validator("target_url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        if not s.startswith(("http://", "https://")):
            raise ValueError("target_url must start with http:// or https://")
        return s

    @field_validator("selected_features", mode="before")
    @classmethod
    def _coerce_features(cls, v: Union[str, List[str], None]) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []


class RunRequest(BaseModel):
    """Optional overrides for a single triggered run."""

    run_mode: Literal["known_flow", "auto_explore"] = "known_flow"
    metadata: RunMetadata = Field(default_factory=RunMetadata)
    auto_explore: Optional[AutoExploreRequest] = None

    @model_validator(mode="after")
    def _auto_requires_payload(self) -> RunRequest:
        if self.run_mode == "auto_explore" and self.auto_explore is None:
            raise ValueError("auto_explore payload is required when run_mode is auto_explore")
        return self


class StepResultResponse(BaseModel):
    layer: str
    name: str
    status: StepStatus
    step_id: Optional[str] = None
    duration_ms: Optional[float] = None
    detail: Mapping[str, Any] = Field(default_factory=dict)
    errors: Sequence[str] = Field(default_factory=list)
    failure_mode: Optional[StepFailureMode] = None


class RunResponse(BaseModel):
    run_id: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    steps: list[StepResultResponse]
    summary: Mapping[str, Any] = Field(default_factory=dict)
