"""Eight-bucket failure taxonomy for generic classification (no product semantics)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, MutableMapping, Sequence

from qa_agent.core.run_metadata import flow_engine_results_list
from qa_agent.core.types import StepExecutionStatus, StepResult


class FailureTaxonomy(str, Enum):
    """Orthogonal failure dimensions for aggregation and reporting."""

    ASSERTION = "assertion"
    CONTRACT = "contract"
    DATA = "data"
    INFRASTRUCTURE = "infrastructure"
    SECURITY = "security"
    TIMING = "timing"
    UX = "ux"
    UNKNOWN = "unknown"


def _bucket_for_layer(layer: str, step_name: str = "") -> FailureTaxonomy:
    l = (layer or "").lower()
    n = (step_name or "").lower()
    if l == "report_sink" or l == "auto_explore_ui" or (l == "plugins" and n == "report_sink"):
        return FailureTaxonomy.INFRASTRUCTURE
    if "security" in l:
        return FailureTaxonomy.SECURITY
    if "step_assertion" in l or "flow_assertion" in l:
        return FailureTaxonomy.ASSERTION
    if "api" in l:
        return FailureTaxonomy.CONTRACT
    if "data" in l:
        return FailureTaxonomy.DATA
    if l in ("planner", "discovery", "execution", "analysis", "reporting"):
        return FailureTaxonomy.INFRASTRUCTURE
    if l == "plugins":
        return FailureTaxonomy.UX
    if "ui" in l or "automation" in l:
        return FailureTaxonomy.UX
    return FailureTaxonomy.UNKNOWN


def _detail_timing_hint(detail: Mapping[str, Any]) -> bool:
    d = str(detail).lower()
    return "timeout" in d or "timing" in d or ("duration" in d and "exceed" in d)


def classify_failure_taxonomy(
    steps: Sequence[StepResult],
    metadata: Mapping[str, Any],
) -> list[MutableMapping[str, Any]]:
    """
    Assign failed steps and failed flow-engine rows to taxonomy buckets.

    Flow-level failures without step context default to ``UNKNOWN``.
    """
    buckets: MutableMapping[str, list[MutableMapping[str, Any]]] = {e.value: [] for e in FailureTaxonomy}

    for s in steps:
        if s.status != StepExecutionStatus.FAILED:
            continue
        bucket = _bucket_for_layer(s.layer, s.name)
        detail = dict(s.detail)
        if _detail_timing_hint(detail):
            bucket = FailureTaxonomy.TIMING
        buckets[bucket.value].append(
            {"layer": s.layer, "name": s.name, "errors": list(s.errors), "detail_keys": list(detail.keys())}
        )

    flows = flow_engine_results_list(metadata)
    if isinstance(flows, list):
        for fr in flows:
            if isinstance(fr, dict) and not fr.get("ok", True):
                buckets[FailureTaxonomy.UNKNOWN.value].append(
                    {
                        "source": "flow_engine",
                        "flow_key": fr.get("flow_key"),
                        "flow_version": fr.get("flow_version"),
                    }
                )

    out: list[MutableMapping[str, Any]] = []
    for key in FailureTaxonomy:
        items = buckets[key.value]
        if items:
            row: MutableMapping[str, Any] = {
                "taxonomy": key.value,
                "count": len(items),
                "items": items,
            }
            # Stakeholder-facing fields (reporting/UI); technical `taxonomy` unchanged for engine use.
            from qa_agent.core.stakeholder_taxonomy import map_technical_to_stakeholder, stakeholder_label

            sh = map_technical_to_stakeholder(key)
            row["stakeholder_category"] = sh.value
            row["stakeholder_label"] = stakeholder_label(sh)
            out.append(row)
    return out
