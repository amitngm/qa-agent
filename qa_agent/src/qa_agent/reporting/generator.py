"""Report generator — builds artifacts from run outcomes (no I/O)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from qa_agent.config.settings import AgentConfig
from qa_agent.core.types import RunContext, RunResult
from qa_agent.reporting.builder import build_report
from qa_agent.reporting.schema import QaReport


class ReportGenerator:
    """Produces a :class:`QaReport` document."""

    def generate(
        self,
        run: RunResult,
        *,
        context: Optional[RunContext] = None,
        agent_config: Optional[AgentConfig] = None,
        extensions: Optional[Mapping[str, Any]] = None,
    ) -> QaReport:
        return build_report(
            run,
            context=context,
            agent_config=agent_config,
            extensions=extensions,
        )
