"""Minimal HTML renderer for QaReport — no external template engine."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Optional

from qa_agent.reporting.schema import (
    Conclusion,
    EvidenceItem,
    FailureCategoryItem,
    FlowRunReport,
    QaReport,
    RunSummary,
    StepReport,
)


def _esc(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return html.escape(value.isoformat())
    if isinstance(value, (dict, list)):
        import json

        return html.escape(json.dumps(value, default=str, indent=2)[:8000])
    return html.escape(str(value))


def render_html(report: QaReport, *, title: Optional[str] = None) -> str:
    """Produce a self-contained HTML document."""
    t = title or "QA run report"
    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        f"<meta charset=\"utf-8\"><title>{_esc(t)}</title>",
        "<style>",
        "body{font-family:system-ui,Segoe UI,sans-serif;margin:1.5rem;line-height:1.45;color:#1a1a1a;}",
        "h1,h2{font-weight:600;}",
        "table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:0.9rem;}",
        "th,td{border:1px solid #ccc;padding:0.4rem 0.6rem;text-align:left;vertical-align:top;}",
        "th{background:#f0f0f0;}",
        ".pass{color:#0d5f2b;}.fail{color:#a40000;}.partial{color:#8a5b00;}.skipped{color:#555;}",
        ".meta{color:#555;font-size:0.85rem;}",
        "pre{background:#f6f8fa;padding:0.75rem;overflow:auto;max-height:24rem;}",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>{_esc(t)}</h1>",
        _section_run(report.run),
        _section_conclusion(report.conclusion),
        _section_failure_taxonomy(report),
        _section_steps(report.steps),
        _section_failures(report.failure_categories),
        _section_evidence(report.evidence),
        _section_flows(report.flows),
        f"<p class=\"meta\">Schema {_esc(report.schema_version)} · Generated {_esc(report.generated_at)}</p>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts)


def _section_run(run: RunSummary) -> str:
    lines = [
        "<h2>Run summary</h2>",
        "<table>",
        "<tr><th>Run ID</th><td>" + _esc(run.run_id) + "</td></tr>",
        "<tr><th>Status</th><td>" + _esc(run.status) + "</td></tr>",
        "<tr><th>Started</th><td>" + _esc(run.started_at) + "</td></tr>",
        "<tr><th>Finished</th><td>" + _esc(run.finished_at) + "</td></tr>",
    ]
    if run.environment:
        lines.append("<tr><th>Environment</th><td>" + _esc(run.environment) + "</td></tr>")
    lines.append("</table>")
    if run.orchestrator_summary:
        lines.append("<h3>Orchestrator summary</h3><pre>" + _esc(run.orchestrator_summary) + "</pre>")
    return "\n".join(lines)


def _verdict_class(verdict: str) -> str:
    v = verdict.lower()
    if v == "pass":
        return "pass"
    if v == "fail":
        return "fail"
    if v == "partial":
        return "partial"
    return "skipped"


def _section_conclusion(c: Conclusion) -> str:
    cls = _verdict_class(c.verdict.value)
    return "\n".join(
        [
            "<h2>Conclusion</h2>",
            f"<p><strong class=\"{cls}\">Verdict: {_esc(c.verdict.value)}</strong> — {_esc(c.message)}</p>",
            "<ul class=\"meta\">",
            f"<li>Total steps: {_esc(c.total_step_count)}</li>",
            f"<li>Failed: {_esc(c.failed_step_count)}</li>",
            f"<li>Skipped: {_esc(c.skipped_step_count)}</li>",
            "</ul>",
        ]
    )


def _section_failure_taxonomy(report: QaReport) -> str:
    """Stakeholder-facing roll-up plus technical buckets for internal traceability."""
    blocks: list[str] = ["<h2>Failure taxonomy</h2>"]
    sh = report.stakeholder_failure_summary
    tech = report.technical_failure_taxonomy
    if not sh and not tech:
        blocks.append("<p class=\"meta\">No failure taxonomy data for this run.</p>")
        return "\n".join(blocks)

    blocks.append("<h3>Stakeholder view</h3>")
    if not sh:
        blocks.append("<p class=\"meta\">No stakeholder roll-up (no classified failures).</p>")
    else:
        rows = ["<tr><th>Group</th><th>Count</th><th>Technical taxonomies</th></tr>"]
        for g in sh:
            tt = ", ".join(g.technical_taxonomies) if g.technical_taxonomies else "—"
            rows.append(
                "<tr>"
                f"<td>{_esc(g.stakeholder_label)}</td>"
                f"<td>{_esc(g.count)}</td>"
                f"<td class=\"meta\">{_esc(tt)}</td>"
                "</tr>"
            )
        blocks.append("<table>\n" + "\n".join(rows) + "\n</table>")

    blocks.append("<h3>Technical buckets (engine)</h3>")
    if not tech:
        blocks.append("<p class=\"meta\">None.</p>")
    else:
        trows = [
            "<tr><th>Technical taxonomy</th><th>Count</th>"
            "<th>Stakeholder label</th><th>Items (summary)</th></tr>"
        ]
        for row in tech:
            if not isinstance(row, dict):
                continue
            tax = row.get("taxonomy", "")
            cnt = row.get("count", 0)
            slabel = row.get("stakeholder_label", "—")
            items = row.get("items") or []
            brief = f"{len(items)} item(s)" if items else "—"
            trows.append(
                "<tr>"
                f"<td><code>{_esc(tax)}</code></td>"
                f"<td>{_esc(cnt)}</td>"
                f"<td>{_esc(slabel)}</td>"
                f"<td class=\"meta\">{_esc(brief)}</td>"
                "</tr>"
            )
        blocks.append("<table>\n" + "\n".join(trows) + "\n</table>")

    return "\n".join(blocks)


def _section_steps(steps: list[StepReport]) -> str:
    if not steps:
        return "<h2>Step results</h2><p class=\"meta\">No steps recorded.</p>"
    rows = [
        "<tr>",
        "<th>#</th><th>Step ID</th><th>Layer</th><th>Name</th><th>Status</th><th>Pass/Fail</th>",
        "<th>Failure mode</th><th>Duration (ms)</th><th>Errors</th><th>Failure category</th>",
        "</tr>",
    ]
    for s in steps:
        pf = s.pass_fail.value
        pcls = _verdict_class(pf if pf != "skipped" else "skipped")
        rows.append(
            "<tr>"
            f"<td>{_esc(s.index)}</td>"
            f"<td>{_esc(s.step_id or '—')}</td>"
            f"<td>{_esc(s.layer)}</td>"
            f"<td>{_esc(s.name)}</td>"
            f"<td>{_esc(s.status)}</td>"
            f"<td class=\"{pcls}\">{_esc(pf)}</td>"
            f"<td>{_esc(s.failure_mode or '—')}</td>"
            f"<td>{_esc(s.duration_ms)}</td>"
            f"<td>{_esc('; '.join(s.errors) if s.errors else '—')}</td>"
            f"<td>{_esc(s.failure_category or '—')}</td>"
            "</tr>"
        )
    return "<h2>Step results</h2>\n<table>\n" + "\n".join(rows) + "\n</table>"


def _section_failures(items: list[FailureCategoryItem]) -> str:
    if not items:
        return "<h2>Failure categories</h2><p class=\"meta\">None.</p>"
    rows = ["<tr><th>Category</th><th>Source</th><th>Flow</th><th>Phase</th><th>Detail</th></tr>"]
    for f in items:
        rows.append(
            "<tr>"
            f"<td>{_esc(f.category)}</td>"
            f"<td>{_esc(f.source)}</td>"
            f"<td>{_esc(f.flow_key)}</td>"
            f"<td>{_esc(f.phase)}</td>"
            f"<td><pre>{_esc(f.detail)}</pre></td>"
            "</tr>"
        )
    return "<h2>Failure categories</h2>\n<table>\n" + "\n".join(rows) + "\n</table>"


def _section_evidence(items: list[EvidenceItem]) -> str:
    if not items:
        return "<h2>Evidence</h2><p class=\"meta\">No evidence references.</p>"
    rows = ["<tr><th>Kind</th><th>Reference</th><th>Source</th><th>Flow</th><th>Detail</th></tr>"]
    for e in items:
        rows.append(
            "<tr>"
            f"<td>{_esc(e.kind)}</td>"
            f"<td>{_esc(e.ref)}</td>"
            f"<td>{_esc(e.source)}</td>"
            f"<td>{_esc(e.flow_key)}</td>"
            f"<td><pre>{_esc(e.detail)}</pre></td>"
            "</tr>"
        )
    return "<h2>Evidence references</h2>\n<table>\n" + "\n".join(rows) + "\n</table>"


def _section_flows(flows: list[FlowRunReport]) -> str:
    if not flows:
        return "<h2>Flows</h2><p class=\"meta\">No flow engine results.</p>"
    blocks: list[str] = ["<h2>Flows</h2>"]
    for f in flows:
        ok = "pass" if f.ok else "fail"
        fv = f.flow_version or "—"
        blocks.append(
            f"<h3>Flow <code>{_esc(f.flow_key)}</code> "
            f"<span class=\"meta\">v{_esc(fv)}</span> "
            f"<span class=\"{ok}\">({_esc(f.ok)})</span></h3>"
        )
        blocks.append("<p class=\"meta\">Instance " + _esc(f.flow_instance_id) + "</p>")
        if f.aborted_after:
            blocks.append("<p class=\"meta\">Aborted after: " + _esc(f.aborted_after) + "</p>")
        if f.phases:
            pr = ["<tr><th>Phase</th><th>Outcome</th><th>ms</th><th>Errors</th></tr>"]
            for p in f.phases:
                pr.append(
                    "<tr>"
                    f"<td>{_esc(p.phase)}</td>"
                    f"<td>{_esc(p.outcome)}</td>"
                    f"<td>{_esc(p.duration_ms)}</td>"
                    f"<td>{_esc('; '.join(p.errors))}</td>"
                    "</tr>"
                )
            blocks.append("<table>\n" + "\n".join(pr) + "\n</table>")
        if f.parse_notes:
            blocks.append(
                '<p class="meta warn">Parse notes: '
                + _esc("; ".join(f.parse_notes))
                + "</p>"
            )
        if f.summary:
            blocks.append("<pre>" + _esc(f.summary) + "</pre>")
    return "\n".join(blocks)
