"""
Declarative execute-phase steps — **kind semantics are implemented in** :mod:`qa_agent.flows.step_runner`.

:class:`FlowStepKind` and :class:`FlowStepSpec` define a **platform-owned** vocabulary for
structured steps (sequential, branch, conditional, poll). The phase
:class:`~qa_agent.flows.engine.FlowEngine` does **not** read these types; it only invokes
``flow.execute_steps``. Flows that use specs call
:func:`~qa_agent.flows.step_runner.standard_run_execute_phase` from ``execute_steps`` and
implement :class:`~qa_agent.flows.step_hooks.FlowStepHooks` for domain actions.

Custom execute-phase behavior without specs remains valid: override ``execute_steps`` and
do not use the runner (escape hatch for non-declarative flows).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field


class FlowStepKind(str, Enum):
    """Built-in step kinds interpreted only by :func:`~qa_agent.flows.step_runner.standard_run_execute_phase`."""

    SEQUENTIAL = "sequential"
    BRANCH = "branch"
    CONDITIONAL = "conditional"
    POLL = "poll"


class FlowStepSpec(BaseModel):
    """
    One row in a declarative execute-phase graph.

    Fields like ``condition``, ``branch_targets``, and ``poll`` are opaque to the runner
    except where :func:`~qa_agent.flows.step_runner.standard_run_execute_phase` explicitly
    reads them (e.g. ``branch_targets``, ``poll`` timeouts). Hooks interpret domain meaning.
    """

    step_key: str
    kind: FlowStepKind = FlowStepKind.SEQUENTIAL
    next_on_success: Optional[str] = Field(
        default=None,
        description="Optional next step_key when this step succeeds.",
    )
    next_on_failure: Optional[str] = Field(
        default=None,
        description="Optional next step_key when this step fails.",
    )
    condition: Mapping[str, Any] = Field(
        default_factory=dict,
        description="Opaque predicate spec; hooks may read for CONDITIONAL steps.",
    )
    branch_targets: Mapping[str, str] = Field(
        default_factory=dict,
        description="For BRANCH: discriminator value -> next step_key.",
    )
    poll: Mapping[str, Any] = Field(
        default_factory=dict,
        description="For POLL: e.g. interval_ms, timeout_ms, max_iterations (runner reads these).",
    )
    detail: Mapping[str, Any] = Field(default_factory=dict)
