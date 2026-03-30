"""Shared types for platform drivers."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field


class DriverResult(BaseModel):
    """Outcome of a single driver operation — no UI-specific fields."""

    ok: bool
    detail: Mapping[str, Any] = Field(default_factory=dict)
    errors: Sequence[str] = Field(default_factory=list)
