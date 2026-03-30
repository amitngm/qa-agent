"""Analysis layer — aggregates results, failure correlation, taxonomy, and diagnostics."""

from __future__ import annotations

import time
from typing import Any, List, Mapping, MutableMapping

from qa_agent.config.settings import AgentConfig
from qa_agent.core.failure_taxonomy import classify_failure_taxonomy
from qa_agent.core.run_metadata import flow_engine_results_list, validator_block
from qa_agent.core.stakeholder_taxonomy import aggregate_stakeholder_summary
from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.layers.base import AnalysisLayer
from qa_agent.store.protocol import RunStore


def _failure_correlation_pass(metadata: Mapping[str, Any]) -> List[MutableMapping[str, Any]]:
    """Group failures by shared detail keys (generic heuristic, no product logic)."""
    failures: list[dict[str, Any]] = []
    flow_results = flow_engine_results_list(metadata)
    if isinstance(flow_results, list):
        for fr in flow_results:
            if isinstance(fr, dict) and not fr.get("ok", True):
                failures.append(
                    {
                        "source": "flow_engine",
                        "flow_key": fr.get("flow_key"),
                        "flow_version": fr.get("flow_version"),
                        "aborted_after": fr.get("aborted_after"),
                    }
                )
    for key in ("step_assertions", "flow_assertions", "api_validation", "data_validation", "security_validation"):
        block = validator_block(metadata, key)
        if isinstance(block, dict) and block.get("failed"):
            failures.append({"source": "metadata", "block": key})
    ex = metadata.get("executor")
    if isinstance(ex, dict):
        ui = ex.get("ui_automation")
        if isinstance(ui, dict) and ui.get("failed"):
            failures.append({"source": "metadata", "block": "ui_automation"})
    groups: MutableMapping[str, list] = {}
    for f in failures:
        gk = str(f.get("flow_key") or f.get("block") or "unknown")
        groups.setdefault(gk, []).append(f)
    return [{"correlation_key": k, "items": v} for k, v in groups.items()]


def _steps_from_store(store: Any) -> list[StepResult]:
    out: list[StepResult] = []
    if not isinstance(store, RunStore):
        return out
    for rec in store.list_step_records():
        if not isinstance(rec, dict):
            continue
        payload = {k: v for k, v in rec.items() if k != "sequence_index"}
        try:
            out.append(StepResult.model_validate(payload))
        except Exception:
            continue
    return out


class DefaultAnalysis(AnalysisLayer):
    def run(self, context: RunContext, config: AgentConfig) -> StepResult:
        start = time.perf_counter()
        md = context.metadata_as_dict()
        correlation = _failure_correlation_pass(md)
        prior_steps = _steps_from_store(context.run_store)
        taxonomy = classify_failure_taxonomy(prior_steps, md)
        stakeholder_summary = aggregate_stakeholder_summary(taxonomy)
        context.merge_metadata(
            {
                "analyzer": {
                    "insights": [],
                    "failure_correlation": correlation,
                    "failure_taxonomy": taxonomy,
                    "stakeholder_failure_summary": stakeholder_summary,
                }
            }
        )
        duration_ms = (time.perf_counter() - start) * 1000
        return StepResult(
            layer="analysis",
            name=self.name,
            status=StepStatus.SUCCEEDED,
            duration_ms=duration_ms,
            detail={
                "failure_correlation_groups": len(correlation),
                "failure_taxonomy_buckets": len(taxonomy),
            },
        )
