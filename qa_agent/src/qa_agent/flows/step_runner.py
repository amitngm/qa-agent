"""
Platform-owned interpretation of :class:`FlowStepSpec` / :class:`FlowStepKind`.

The phase :class:`~qa_agent.flows.engine.FlowEngine` calls ``flow.execute_steps`` only;
this module is the **single** place that dispatches on step *kind* for declarative flows.
Flows implement :class:`~qa_agent.flows.step_hooks.FlowStepHooks` and pass it here from
``execute_steps`` — they do not reimplement BRANCH / CONDITIONAL / POLL semantics ad hoc.
"""

from __future__ import annotations

import time
from typing import List, MutableMapping, Optional, Sequence

from qa_agent.config.settings import AgentConfig
from qa_agent.flows.step_hooks import FlowStepHooks, UnsupportedFlowStepKind
from qa_agent.flows.step_spec import FlowStepKind, FlowStepSpec
from qa_agent.flows.types import FlowContext, FlowPhase, FlowPhaseResult, FlowStepOutcome, PhaseOutcome


def _advance_in_list_order(
    specs: Sequence[FlowStepSpec],
    spec: FlowStepSpec,
) -> Optional[str]:
    """Next step_key in declaration order, if any."""
    for i, s in enumerate(specs):
        if s.step_key == spec.step_key and i + 1 < len(specs):
            return specs[i + 1].step_key
    return None


def _index_steps(specs: Sequence[FlowStepSpec]) -> dict[str, FlowStepSpec]:
    by_key: dict[str, FlowStepSpec] = {}
    for s in specs:
        if s.step_key in by_key:
            raise ValueError(f"duplicate step_key in FlowStepSpec list: {s.step_key!r}")
        by_key[s.step_key] = s
    return by_key


def standard_run_execute_phase(
    specs: Sequence[FlowStepSpec],
    ctx: FlowContext,
    config: AgentConfig,
    hooks: FlowStepHooks,
) -> FlowPhaseResult:
    """
    Run the execute phase for a declarative step graph.

    Entry is ``specs[0].step_key`` when ``specs`` is non-empty. Step kinds are interpreted
    here; :paramref:`hooks` provides only domain-specific actions.
    """
    if not specs:
        return FlowPhaseResult.ok(FlowPhase.EXECUTE, step_outcomes=[])

    by_key = _index_steps(specs)
    outcomes: List[FlowStepOutcome] = []
    current_key: Optional[str] = specs[0].step_key
    visited: MutableMapping[str, int] = {}

    max_steps = max(len(specs) * 20, 20)

    for _ in range(max_steps):
        if current_key is None:
            return FlowPhaseResult.ok(FlowPhase.EXECUTE, step_outcomes=outcomes)

        spec = by_key.get(current_key)
        if spec is None:
            return FlowPhaseResult.fail(
                FlowPhase.EXECUTE,
                f"unknown step_key {current_key!r}",
                detail={"known_keys": list(by_key.keys())},
            )

        visited[current_key] = visited.get(current_key, 0) + 1
        if visited[current_key] > len(specs):
            return FlowPhaseResult.fail(
                FlowPhase.EXECUTE,
                "flow step graph cycle detected",
                detail={"step_key": current_key},
            )

        kind = spec.kind
        if kind == FlowStepKind.SEQUENTIAL:
            out = hooks.execute_step(ctx, config, spec)
            outcomes.append(out)
            if out.outcome == PhaseOutcome.FAILED:
                current_key = spec.next_on_failure
                if current_key is None:
                    return FlowPhaseResult(
                        phase=FlowPhase.EXECUTE,
                        outcome=PhaseOutcome.FAILED,
                        detail={"failed_at": spec.step_key},
                        errors=list(out.errors) or ["execute_step_failed"],
                        step_outcomes=outcomes,
                    )
            else:
                current_key = spec.next_on_success
                if current_key is None:
                    current_key = _advance_in_list_order(specs, spec)
                if current_key is None:
                    return FlowPhaseResult.ok(FlowPhase.EXECUTE, step_outcomes=outcomes)
            continue

        if kind == FlowStepKind.BRANCH:
            try:
                disc = hooks.branch_value(ctx, config, spec)
            except UnsupportedFlowStepKind as exc:
                return FlowPhaseResult.fail(
                    FlowPhase.EXECUTE,
                    str(exc),
                    detail={"step_key": spec.step_key, "kind": kind.value},
                )
            next_key = spec.branch_targets.get(disc)
            if next_key is None:
                return FlowPhaseResult.fail(
                    FlowPhase.EXECUTE,
                    f"branch_targets has no path for discriminator {disc!r}",
                    detail={"step_key": spec.step_key, "branch_targets": dict(spec.branch_targets)},
                )
            outcomes.append(
                FlowStepOutcome(
                    step_key=spec.step_key,
                    outcome=PhaseOutcome.SUCCEEDED,
                    step_kind=kind.value,
                    branch_taken=disc,
                    detail={"next": next_key},
                )
            )
            current_key = next_key
            continue

        if kind == FlowStepKind.CONDITIONAL:
            try:
                ok = hooks.evaluate_condition(ctx, config, spec)
            except UnsupportedFlowStepKind as exc:
                return FlowPhaseResult.fail(
                    FlowPhase.EXECUTE,
                    str(exc),
                    detail={"step_key": spec.step_key, "kind": kind.value},
                )
            current_key = spec.next_on_success if ok else spec.next_on_failure
            outcomes.append(
                FlowStepOutcome(
                    step_key=spec.step_key,
                    outcome=PhaseOutcome.SUCCEEDED,
                    step_kind=kind.value,
                    detail={"condition_result": ok},
                )
            )
            continue

        if kind == FlowStepKind.POLL:
            poll = dict(spec.poll)
            timeout_ms = float(poll.get("timeout_ms", 30_000))
            interval_ms = float(poll.get("interval_ms", 100))
            max_iterations = int(poll.get("max_iterations", 500))
            deadline = time.monotonic() + timeout_ms / 1000.0
            iterations = 0
            satisfied = False
            try:
                while time.monotonic() < deadline and iterations < max_iterations:
                    iterations += 1
                    if hooks.poll_tick(ctx, config, spec):
                        satisfied = True
                        break
                    time.sleep(interval_ms / 1000.0)
            except UnsupportedFlowStepKind as exc:
                return FlowPhaseResult.fail(
                    FlowPhase.EXECUTE,
                    str(exc),
                    detail={"step_key": spec.step_key, "kind": kind.value},
                )

            out = PhaseOutcome.SUCCEEDED if satisfied else PhaseOutcome.FAILED
            outcomes.append(
                FlowStepOutcome(
                    step_key=spec.step_key,
                    outcome=out,
                    step_kind=kind.value,
                    poll_iterations=iterations,
                    detail={"satisfied": satisfied},
                )
            )
            if satisfied:
                current_key = spec.next_on_success
            else:
                current_key = spec.next_on_failure
                if current_key is None:
                    return FlowPhaseResult(
                        phase=FlowPhase.EXECUTE,
                        outcome=PhaseOutcome.FAILED,
                        errors=["poll_not_satisfied"],
                        detail={"step_key": spec.step_key},
                        step_outcomes=outcomes,
                    )
            continue

        return FlowPhaseResult.fail(
            FlowPhase.EXECUTE,
            f"unsupported FlowStepKind: {kind!r}",
            detail={"step_key": spec.step_key},
        )

    return FlowPhaseResult.fail(
        FlowPhase.EXECUTE,
        "flow step runner exceeded max iterations",
        detail={"last_key": current_key},
    )
