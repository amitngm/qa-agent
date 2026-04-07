"""Typed run metadata — one nested model per pipeline concern plus a controlled ``extensions`` bucket."""

from __future__ import annotations

from typing import Any, List, Mapping, MutableMapping, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from qa_agent.discovery.contract import DiscoveryReport
from qa_agent.validation.api_models import ApiValidationSummary
from qa_agent.platform.auto_explore_models import AutoExploreSummary
from qa_agent.platform.ui_models import UiAutomationSummary
from qa_agent.validation.data_models import DataValidationSummary
from qa_agent.validation.page_models import PageValidationSummary
from qa_agent.validation.security_models import SecurityValidationSummary


# --- Planner (offline planning) ---


class PlannerPlanSummary(BaseModel):
    """Minimal plan reference surfaced on the run."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    offline_only: bool = True


class PlannerMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offline_plan_id: str
    plan: PlannerPlanSummary
    prior_run_digests: Optional[List[Mapping[str, Any]]] = Field(
        default=None,
        description="Optional digests from RunHistoryReader when planner integration is enabled.",
    )


# --- Discovery (discoverer) ---

# Discovery output uses ``DiscoveryReport`` directly on :class:`RunMetadata` field ``discovery``.

# --- Executor (actions, flows, optional UI automation bridge) ---


class ExecutionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions_completed: int = 0


class ExecutorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution: Optional[ExecutionSummary] = None
    flow_engine_results: List[Any] = Field(default_factory=list)
    flow_keys: Optional[List[str]] = None
    ui_automation: Optional[UiAutomationSummary] = None
    auto_explore_ui: Optional[AutoExploreSummary] = None


# --- Validator (assertions + validation plugins) ---


class AssertionsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories_addressed: List[str] = Field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0
    failed: Optional[bool] = None
    assertions: List[Any] = Field(default_factory=list)


class ValidatorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_assertions: Optional[AssertionsSummary] = None
    flow_assertions: Optional[AssertionsSummary] = None
    page_validation: Optional[PageValidationSummary] = None
    api_validation: Optional[ApiValidationSummary] = None
    data_validation: Optional[DataValidationSummary] = None
    security_validation: Optional[SecurityValidationSummary] = None


# --- Analyzer (aggregation / taxonomy / diagnostics) ---


class AnalyzerMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insights: List[Any] = Field(default_factory=list)
    failure_correlation: List[Mapping[str, Any]] = Field(default_factory=list)
    failure_taxonomy: List[Mapping[str, Any]] = Field(default_factory=list)
    stakeholder_failure_summary: List[Mapping[str, Any]] = Field(default_factory=list)


# --- Reporter (artifact generation + external sinks) ---


class ReportingLayerMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sink: Mapping[str, Any] = Field(default_factory=dict)
    generator: str = ""
    dispatcher: str = ""


class ReportSinkMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "placeholder"
    format: Optional[str] = None


class ReporterMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reporting: Optional[ReportingLayerMetadata] = None
    report_sink: Optional[ReportSinkMetadata] = None


def _deep_merge_dict(base: Optional[Mapping[str, Any]], update: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not base and not update:
        return {}
    out: dict[str, Any] = {**(dict(base) if base else {})}
    if not update:
        return out
    for k, v in dict(update).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _lift_legacy_top_level(data: MutableMapping[str, Any]) -> None:
    """Normalize legacy flat keys into nested layer buckets (in place)."""
    if "planner" not in data and ("plan" in data or "offline_plan_id" in data):
        plan_raw = data.get("plan")
        oid = data.get("offline_plan_id")
        if isinstance(plan_raw, dict):
            pid = str(plan_raw.get("plan_id") or oid or "")
            offline = bool(plan_raw.get("offline_only", True))
        else:
            pid = str(oid or "")
            offline = True
        if pid or oid:
            data["planner"] = {
                "offline_plan_id": str(oid or pid),
                "plan": {"plan_id": pid or str(oid), "offline_only": offline},
            }
            data.pop("plan", None)
            data.pop("offline_plan_id", None)
        else:
            ext = dict(data.get("extensions") or {})
            warn_list = ext.get("legacy_normalization_warnings")
            if not isinstance(warn_list, list):
                warn_list = []
            warn_list.append(
                "Legacy planner fields (plan/offline_plan_id) could not be migrated to PlannerMetadata "
                "(missing resolvable plan_id/offline_plan_id). Raw values preserved under extensions.legacy_planner_unresolved."
            )
            ext["legacy_normalization_warnings"] = warn_list
            ext["legacy_planner_unresolved"] = {"plan": plan_raw, "offline_plan_id": oid}
            data["extensions"] = ext
            data.pop("plan", None)
            data.pop("offline_plan_id", None)

    if "discovery" not in data and "discovery_report" in data:
        data["discovery"] = data.pop("discovery_report")

    if "executor" not in data:
        ex: dict[str, Any] = {}
        for k in ("execution", "flow_engine_results", "flow_keys", "ui_automation", "auto_explore_ui"):
            if k in data:
                ex[k] = data.pop(k)
        if ex:
            data["executor"] = ex

    if "validator" not in data:
        vm: dict[str, Any] = {}
        for k in ("step_assertions", "flow_assertions", "page_validation", "api_validation", "data_validation", "security_validation"):
            if k in data:
                vm[k] = data.pop(k)
        if vm:
            data["validator"] = vm

    if "analyzer" not in data and "analysis" in data:
        data["analyzer"] = data.pop("analysis")

    if "reporter" not in data:
        rep: dict[str, Any] = {}
        for k in ("reporting", "report_sink"):
            if k in data:
                rep[k] = data.pop(k)
        if rep:
            data["reporter"] = rep

    # Orphan legacy keys (e.g. partial merges) after nested executor exists
    if "executor" in data and isinstance(data["executor"], dict):
        ex = data["executor"]
        for k in ("execution", "flow_engine_results", "flow_keys", "ui_automation", "auto_explore_ui"):
            if k in data:
                ex[k] = data.pop(k)

    # Strip duplicate legacy keys once nested buckets exist (avoid routing to extensions)
    if "planner" in data:
        data.pop("plan", None)
        data.pop("offline_plan_id", None)
    if "discovery" in data:
        data.pop("discovery_report", None)
    if "executor" in data:
        for k in ("execution", "flow_engine_results", "flow_keys", "ui_automation", "auto_explore_ui"):
            data.pop(k, None)
    if "validator" in data:
        for k in ("step_assertions", "flow_assertions", "page_validation", "api_validation", "data_validation", "security_validation"):
            data.pop(k, None)
    if "analyzer" in data:
        data.pop("analysis", None)
    if "reporter" in data:
        data.pop("reporting", None)
        data.pop("report_sink", None)


_ALLOWED_ROOT = frozenset(
    {
        "planner",
        "discovery",
        "executor",
        "validator",
        "analyzer",
        "reporter",
        "extensions",
        "environment",
    }
)


def _route_unknown_to_extensions(data: MutableMapping[str, Any]) -> None:
    """Move keys outside the typed surface into ``extensions``."""
    ext = dict(data.get("extensions") or {})
    for key in list(data.keys()):
        if key not in _ALLOWED_ROOT:
            ext[key] = data.pop(key)
    if ext:
        data["extensions"] = ext


class RunMetadata(BaseModel):
    """
    Pipeline metadata grouped by role: planner, discovery, executor, validator, analyzer, reporter.

    Arbitrary keys are **not** accepted at the root; use :attr:`extensions` for forward-compatible
    or plugin-specific data. Legacy flat layouts (e.g. top-level ``analysis``) are coerced on load.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    environment: Optional[str] = Field(default=None, description="Optional run environment override.")

    planner: Optional[PlannerMetadata] = None
    discovery: Optional[DiscoveryReport] = Field(
        default=None,
        validation_alias=AliasChoices("discovery", "discovery_report"),
        serialization_alias="discovery_report",
    )
    executor: Optional[ExecutorMetadata] = None
    validator: Optional[ValidatorMetadata] = None
    analyzer: Optional[AnalyzerMetadata] = Field(
        default=None,
        validation_alias=AliasChoices("analyzer", "analysis"),
        serialization_alias="analysis",
    )
    reporter: Optional[ReporterMetadata] = None
    extensions: Mapping[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_and_extensions(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d: MutableMapping[str, Any] = dict(data)
        _lift_legacy_top_level(d)
        _route_unknown_to_extensions(d)
        return d

    def merged(self, updates: Mapping[str, Any]) -> RunMetadata:
        """Deep-merge layer dicts; validates to :class:`RunMetadata`."""
        base = self.model_dump(mode="python")
        patch = dict(updates)
        _lift_legacy_top_level(patch)
        combined = _deep_merge_dict(base, patch)
        _route_unknown_to_extensions(combined)
        return RunMetadata.model_validate(combined)


# --- Read helpers for engine code (nested + legacy-shaped dicts) ---


def flow_engine_results_list(meta: Mapping[str, Any]) -> List[Any]:
    """Flow engine JSON rows for taxonomy / correlation."""
    ex = meta.get("executor")
    if isinstance(ex, dict):
        fr = ex.get("flow_engine_results")
        if isinstance(fr, list):
            return list(fr)
    raw = meta.get("flow_engine_results")
    if isinstance(raw, list):
        return list(raw)
    return []


def validator_block(meta: Mapping[str, Any], name: str) -> Optional[Mapping[str, Any]]:
    """Assertion / validation plugin block by logical name."""
    v = meta.get("validator")
    if isinstance(v, dict):
        block = v.get(name)
        if isinstance(block, dict):
            return block
    block = meta.get(name)
    return block if isinstance(block, dict) else None


def analysis_block(meta: Mapping[str, Any]) -> Mapping[str, Any]:
    """Analyzer output (wire key ``analysis``, or legacy / in-memory ``analyzer``)."""
    a = meta.get("analysis") or meta.get("analyzer")
    return a if isinstance(a, dict) else {}
