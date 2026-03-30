"""
Stakeholder-facing failure groups for reporting and UI.

Maps from :class:`FailureTaxonomy` (technical) only — no product-specific rules.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Mapping, MutableMapping, Sequence

from qa_agent.core.failure_taxonomy import FailureTaxonomy


class StakeholderFailureCategory(str, Enum):
    """Generic groups suitable for dashboards and triage (not technical internals)."""

    PRODUCT_BUG = "product_bug"
    ENVIRONMENT_INFRA = "environment_infra"
    AUTOMATION_FRAMEWORK = "automation_framework"
    TEST_DATA = "test_data"
    PERMISSIONS_ACCESS = "permissions_access"
    DEPENDENCY_EXTERNAL = "dependency_external"
    FLAKY_INTERMITTENT = "flaky_intermittent"
    UNKNOWN_TRIAGE = "unknown_triage"


STAKEHOLDER_DISPLAY_NAME: Mapping[StakeholderFailureCategory, str] = {
    StakeholderFailureCategory.PRODUCT_BUG: "Product Bug",
    StakeholderFailureCategory.ENVIRONMENT_INFRA: "Environment / Infra",
    StakeholderFailureCategory.AUTOMATION_FRAMEWORK: "Automation Framework",
    StakeholderFailureCategory.TEST_DATA: "Test Data",
    StakeholderFailureCategory.PERMISSIONS_ACCESS: "Permissions / Access",
    StakeholderFailureCategory.DEPENDENCY_EXTERNAL: "Dependency / External Service",
    StakeholderFailureCategory.FLAKY_INTERMITTENT: "Flaky / Intermittent",
    StakeholderFailureCategory.UNKNOWN_TRIAGE: "Unknown / Needs Triage",
}


def map_technical_to_stakeholder(technical: FailureTaxonomy) -> StakeholderFailureCategory:
    """Deterministic technical → stakeholder mapping (generic QA semantics)."""
    return _TECHNICAL_TO_STAKEHOLDER[technical]


_TECHNICAL_TO_STAKEHOLDER: Mapping[FailureTaxonomy, StakeholderFailureCategory] = {
    FailureTaxonomy.ASSERTION: StakeholderFailureCategory.PRODUCT_BUG,
    FailureTaxonomy.CONTRACT: StakeholderFailureCategory.DEPENDENCY_EXTERNAL,
    FailureTaxonomy.DATA: StakeholderFailureCategory.TEST_DATA,
    FailureTaxonomy.INFRASTRUCTURE: StakeholderFailureCategory.ENVIRONMENT_INFRA,
    FailureTaxonomy.SECURITY: StakeholderFailureCategory.PERMISSIONS_ACCESS,
    FailureTaxonomy.TIMING: StakeholderFailureCategory.FLAKY_INTERMITTENT,
    FailureTaxonomy.UX: StakeholderFailureCategory.AUTOMATION_FRAMEWORK,
    FailureTaxonomy.UNKNOWN: StakeholderFailureCategory.UNKNOWN_TRIAGE,
}


def stakeholder_label(category: StakeholderFailureCategory) -> str:
    return STAKEHOLDER_DISPLAY_NAME[category]


def aggregate_stakeholder_summary(
    technical_buckets: Sequence[Mapping[str, Any]],
) -> list[MutableMapping[str, Any]]:
    """
    Roll up technical buckets into stakeholder groups (counts and contributing technical keys).

    Expects each bucket to include ``taxonomy`` (technical enum value) and ``count``.
    """
    groups: MutableMapping[str, MutableMapping[str, Any]] = {}
    for row in technical_buckets:
        tax = row.get("taxonomy")
        if not tax:
            continue
        try:
            ft = FailureTaxonomy(str(tax))
        except ValueError:
            continue
        sh = map_technical_to_stakeholder(ft)
        key = sh.value
        if key not in groups:
            groups[key] = {
                "stakeholder_category": key,
                "stakeholder_label": stakeholder_label(sh),
                "count": 0,
                "technical_taxonomies": [],
            }
        g = groups[key]
        g["count"] = int(g["count"]) + int(row.get("count", 0))
        if tax not in g["technical_taxonomies"]:
            g["technical_taxonomies"].append(str(tax))
    return sorted(groups.values(), key=lambda x: (-x["count"], x["stakeholder_label"]))
