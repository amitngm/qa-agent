"""Report dispatcher — delivers generated artifacts to sinks (no generation logic)."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Sequence, Union

from qa_agent.config.settings import SeverityRoutingConfig
from qa_agent.reporting.json_format import report_to_json
from qa_agent.reporting.schema import QaReport

SinkFn = Callable[[str, str, Mapping[str, Any]], None]


def _sink_ids_for_severity(severity: str, routing: SeverityRoutingConfig) -> list[str]:
    """
    Resolve sink ids for a severity key.

    Empty lists in ``route_by_severity`` (or an empty ``unmatched_severity_sink_ids``) are treated
    as misconfiguration: we fall through so dispatch never silently delivers to zero sinks when
    sinks are registered (see :meth:`ReportDispatcher.dispatch_json`).
    """
    ids = routing.route_by_severity.get(severity)
    if ids is not None and len(ids) > 0:
        return list(ids)
    unmatched = list(routing.unmatched_severity_sink_ids)
    if len(unmatched) > 0:
        return unmatched
    return ["default"]


class ReportDispatcher:
    """
    Routes formatted payloads to registered sinks.

    ``sink`` callable receives (kind, payload, headers) where kind is e.g.
    ``application/json`` or ``text/html``.

    Sinks are registered with a stable ``sink_id`` (default ``"default"``).
    When ``dispatch_*`` is called with ``routing``, only sinks whose id appears
    in the resolved list for ``report.severity`` are invoked.
    """

    def __init__(
        self,
        sinks: Optional[Union[Sequence[tuple[str, SinkFn]], Sequence[SinkFn]]] = None,
    ) -> None:
        self._sinks: list[tuple[str, SinkFn]] = []
        if sinks:
            for item in sinks:
                if isinstance(item, tuple) and len(item) == 2:
                    sid, fn = item
                    self._sinks.append((str(sid), fn))
                else:
                    self._sinks.append(("default", item))  # type: ignore[arg-type]

    def add_sink(self, sink: SinkFn, *, sink_id: str = "default") -> None:
        self._sinks.append((sink_id, sink))

    def dispatch_json(
        self,
        report: QaReport,
        *,
        indent: Optional[int] = 2,
        routing: Optional[SeverityRoutingConfig] = None,
    ) -> str:
        body = report_to_json(report, indent=indent)
        meta: Mapping[str, Any] = {"format": "qa_report_v1"}
        if routing is None:
            for _, sink in self._sinks:
                sink("application/json", body, meta)
            return body

        want = _sink_ids_for_severity(report.severity, routing)
        target = set(want)
        dispatched = 0
        for sid, sink in self._sinks:
            if sid in target:
                sink("application/json", body, meta)
                dispatched += 1
        if dispatched == 0 and self._sinks:
            meta_fb: dict[str, Any] = {
                "format": "qa_report_v1",
                "routing_delivery_warning": (
                    "resolved_severity_route_matched_no_registered_sinks; "
                    "delivering_to_all_registered_sinks_as_fallback"
                ),
            }
            for _, sink in self._sinks:
                sink("application/json", body, meta_fb)
        return body
