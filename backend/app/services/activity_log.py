"""Persistence for `GET /api/activity` (plan §10/§11): a system-wide, not
persona-filtered, feed of recent autonomous pipeline runs -- "the best place
to demonstrate autonomy itself" per the plan's Trace Drawer section.

`record_pipeline_summary` is called from the two scheduler entry points
(app/schedulers/interval.py's `_tick`, app/schedulers/listener_bridge.py's
`run_listener_bridge`) with the exact list `app.graph.supervisor.run_pipeline`
already returns -- no new signal/decision shape invented here, just persisted.
Chat-triggered graph runs (`run_chat_turn`, POST /api/chat) are deliberately
NOT logged here: they are user-initiated, not autonomous, and the plan's
Agent Activity view is specifically about runs firing with nobody clicking
anything.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import text

from app.contracts import get_contract
from app.graph.act.db import get_engine

TriggeredBy = Literal["schedule", "event"]

_VALID_PERSONAS = ("transport_manager", "line_manager", "transport_head")


def _describe(entry: dict[str, Any]) -> str | None:
    """Builds a human-readable action sentence from one run_pipeline() summary
    entry (see supervisor.py's docstring for the exact shape). Returns None
    for entries with no persona to attribute the row to (logged_only
    data-quality entries) -- those aren't representable as an ActivityEntry.
    """
    persona = entry.get("persona")
    if persona not in _VALID_PERSONAS:
        return None

    if entry.get("action") == "error":
        return (
            f"Pipeline run failed while processing a {entry.get('signal_type', 'signal')} "
            f"signal (thread {entry.get('thread_id', 'unknown')})."
        )

    signal_type = entry.get("signal_type", "signal")
    summary = entry.get("decision_summary")
    base = summary or f"Processed a {signal_type} signal for {entry.get('scope', 'this scope')}."
    if entry.get("needs_human_signoff"):
        return f"{base} Drafted and held for sign-off."
    return base


def record_pipeline_summary(
    org_id: str,
    summary: list[dict[str, Any]],
    *,
    triggered_by: TriggeredBy,
) -> int:
    """Persists one pipeline_runs row per persona-attributable dispatch in
    `summary`. Returns the number of rows written."""
    rows = []
    for entry in summary:
        action = _describe(entry)
        if action is None:
            continue
        rows.append(
            {
                "org_id": org_id,
                "persona": entry["persona"],
                "action": action,
                "thread_id": entry.get("thread_id"),
                "triggered_by": triggered_by,
            }
        )
    if not rows:
        return 0

    contract = get_contract().entity("pipeline_run")
    table = contract.table
    c = contract.column
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {table} ({c('org_id')}, {c('persona')}, {c('action')}, "
                f"{c('thread_id')}, {c('triggered_by')}) "
                f"VALUES (:org_id, :persona, :action, :thread_id, :triggered_by)"
            ),
            rows,
        )
    return len(rows)


def record_report_run(
    org_id: str,
    persona: str,
    *,
    report_type: str,
    thread_id: str,
    item_count: int,
    triggered_by: TriggeredBy = "schedule",
) -> None:
    """One row per `app.graph.supervisor.run_report()` call -- the periodic
    TM4/TH3 digest path, not per-signal like record_pipeline_summary above,
    so it's a single insert rather than a batch."""
    action = f"Generated {report_type.replace('_', ' ')} covering {item_count} recent item(s)."
    contract = get_contract().entity("pipeline_run")
    table = contract.table
    c = contract.column
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {table} ({c('org_id')}, {c('persona')}, {c('action')}, "
                f"{c('thread_id')}, {c('triggered_by')}) "
                f"VALUES (:org_id, :persona, :action, :thread_id, :triggered_by)"
            ),
            {"org_id": org_id, "persona": persona, "action": action, "thread_id": thread_id, "triggered_by": triggered_by},
        )


def list_activity(org_id: str | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
    """Most-recent-first pipeline_runs rows, optionally scoped to one org_id.
    Not persona-filtered -- callers (the /activity route) filter client-side
    if they ever need to, matching the plan's "system-wide" framing."""
    contract = get_contract().entity("pipeline_run")
    table = contract.table
    c = contract.column
    engine = get_engine()
    where = f"WHERE {c('org_id')} = :org_id" if org_id else ""
    params: dict[str, Any] = {"limit": limit}
    if org_id:
        params["org_id"] = org_id
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"SELECT {c('id')} AS id, {c('persona')} AS persona, {c('action')} AS action, "
                f"{c('thread_id')} AS thread_id, {c('triggered_by')} AS triggered_by, "
                f"{c('created_at')} AS created_at "
                f"FROM {table} {where} ORDER BY {c('created_at')} DESC LIMIT :limit"
            ),
            params,
        ).mappings().all()
    return [dict(r) for r in rows]
