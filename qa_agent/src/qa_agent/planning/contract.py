"""
Planner boundary (offline).

Producers in this package may only use configuration, static assets, and the
Run Store for **non-live** artifacts. They must not invoke discoverers,
executors, platform drivers, or network calls to the application under test.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping, Sequence

from pydantic import BaseModel, Field


class OfflinePlanArtifact(BaseModel):
    """
    Canonical shape for planner output persisted before any live discovery.

    The discoverer may read ``plan_id`` / hashes from the store but must not
    require the planner to perform live probes.
    """

    plan_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    environment: str = "local"
    steps_outline: Sequence[Mapping[str, Any]] = Field(
        default_factory=list,
        description="Opaque step graph hints; no live resolution.",
    )
    constraints: Mapping[str, Any] = Field(default_factory=dict)
    detail: MutableMapping[str, Any] = Field(default_factory=dict)
