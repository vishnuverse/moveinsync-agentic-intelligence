"""generate_html_report() -- the public, framework-independent entry point
(plan §2 html_report_generator) -- plus the `html_report_generator` LangGraph
node wrapper that persists the result to `agent_reports` (plan §11).

Two-mode design per the build brief: static mode renders correctly with zero
LLM calls (`use_llm_narrative=False`); LLM-narrative mode layers richer prose
into the *same* template afterwards. The HTML skeleton, brand styling, KPI
cards, and findings table never depend on the LLM succeeding -- see
narrative.py's fallback-to-static behavior on any LLM failure.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

# See ../nodes.py's module-level NOTE: `Optional[RunnableConfig]`, not
# `RunnableConfig | None`, is required for LangGraph to auto-inject the live
# config (and therefore configurable.thread_id) into this node.

from ..db import get_engine, upsert_report
from ..state import ActState
from .narrative import generate_llm_narrative
from .static_narrative import generate_static_narrative
from .template import compute_kpis, render_report_html

# backend/app/graph/act/html_report_agent/generator.py -> backend/data/reports
DEFAULT_REPORTS_DIR = Path(__file__).resolve().parents[4] / "data" / "reports"

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-") or "report"


def generate_html_report(
    items: list[dict[str, Any]],
    persona: str,
    period_label: str,
    use_llm_narrative: bool = True,
    *,
    title: str | None = None,
) -> str:
    """Renders one standalone HTML report string from structured decision
    items. No DB/filesystem I/O -- callers that need the report persisted
    use the `html_report_generator` node below, or write the returned string
    themselves."""
    metrics = compute_kpis(items)

    narrative = None
    if use_llm_narrative:
        narrative = generate_llm_narrative(items, metrics, persona, period_label)
    if narrative is None:
        narrative = generate_static_narrative(items, metrics, persona, period_label)

    return render_report_html(
        items=items,
        metrics=metrics,
        narrative=narrative,
        persona=persona,
        period_label=period_label,
        generated_at=datetime.now(timezone.utc),
        title=title,
    )


def _resolve_thread_id(state: ActState, config: Optional[RunnableConfig]) -> str | None:
    explicit = state.get("thread_id")
    if explicit:
        return explicit
    if config:
        return (config.get("configurable") or {}).get("thread_id")
    return None


def html_report_generator(state: ActState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    """LangGraph node: generate_html_report() + persist to `agent_reports`
    (contract-resolved, plan §3) + write the HTML to
    backend/data/reports/<report_type>_<persona>_<thread_id>.html.

    Idempotent by construction for graph replay (plan §4's "safe to re-run"
    requirement): the output filename and the `agent_reports` row are both
    keyed off thread_id, so re-executing this node (e.g. on an
    interrupt-resume replay) overwrites the same file/row rather than
    creating a new one.
    """
    decision = state.get("decision", {})
    items = state.get("report_items") or ([decision] if decision else [])
    persona = state.get("persona") or decision.get("target_persona") or "transport_head"
    period_label = state.get("period_label") or "Ad-hoc"
    use_llm = state.get("use_llm_narrative", True)
    report_type = state.get("report_type") or "ad_hoc"
    org_id = state.get("org_id", "moveinsync-demo")
    thread_id = _resolve_thread_id(state, config)
    title = state.get("title") or f"{report_type.replace('_', ' ').title()} Report"

    html_str = generate_html_report(items, persona, period_label, use_llm_narrative=use_llm, title=title)

    reports_dir = DEFAULT_REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    file_key = thread_id or uuid.uuid4().hex
    filename = f"{_slugify(report_type)}_{_slugify(persona)}_{_slugify(file_key)}.html"
    path = reports_dir / filename
    path.write_text(html_str, encoding="utf-8")

    engine = get_engine()
    result = upsert_report(
        engine,
        org_id=org_id,
        report_type=report_type,
        persona=persona,
        title=title,
        period_start=state.get("period_start"),
        period_end=state.get("period_end"),
        storage_ref=str(path),
        format="html",
        thread_id=thread_id,
    )

    return {
        "report_id": result["id"],
        "report_storage_ref": str(path),
        "report_html": html_str,
    }
