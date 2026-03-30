"""Suite — groups multiple flows above a single flow definition."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, Field


class SuiteDefinition(BaseModel):
    """
    Logical test suite above flow level.

    Hosts map ``flow_keys`` to registered flows; metadata is opaque.
    """

    suite_id: str = "default"
    suite_version: str = "1.0.0"
    flow_keys: list[str] = Field(
        default_factory=lambda: ["generic_crud_lifecycle"],
        description="Registered flow keys to run when executor metadata does not override.",
    )
    metadata: Mapping[str, Any] = Field(default_factory=dict)
