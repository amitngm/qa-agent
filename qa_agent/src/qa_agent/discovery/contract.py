"""Discoverer output contract — generic targets with opaque metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping, Optional, Sequence

from pydantic import BaseModel, Field


class DiscoveryTarget(BaseModel):
    """One discoverable item (endpoint, selector, dataset handle, etc.)."""

    target_id: str
    kind: str
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    labels: Mapping[str, str] = Field(default_factory=dict)


class DiscoveryReport(BaseModel):
    """Structured output all discovery implementations should populate."""

    discoverer_id: str
    plan_reference_id: Optional[str] = Field(
        default=None,
        description="Optional id of an OfflinePlanArtifact from the Run Store (offline boundary).",
    )
    produced_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    targets: list[DiscoveryTarget] = Field(default_factory=list)
    sources: Sequence[str] = Field(
        default_factory=list,
        description="Opaque source identifiers (paths, URLs, plugin ids).",
    )
    detail: MutableMapping[str, Any] = Field(default_factory=dict)
