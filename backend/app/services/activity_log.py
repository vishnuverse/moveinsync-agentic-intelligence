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


_SIGNAL_TYPE_LABELS: dict[str, str] = {
    "delay_breach": "a route delay",
    "incident": "a safety incident",
    "cost_divergence": "a cost divergence",
    "emissions_over_target": "an emissions-over-target reading",
    "attendance_correlated_with_transport": "a transport-linked attendance pattern",
    "attendance_unrelated_late": "an attendance pattern unrelated to transport",
    "escort_compliance_violation": "an escort-compliance issue",
    "billing_discrepancy": "a billing discrepancy",
}


def _describe(entry: dict[str, Any]) -> str | None:
    """Builds a human-readable action sentence from one run_pipeline() summary
    entry (see supervisor.py's docstring for the exact shape). Returns None
    for entries not worth surfacing in the feed: logged_only data-quality
    entries (no persona to attribute the row to), and -- BUGFIX (found live:
    the dedup fix in supervisor.run_pipeline emits an "skipped_already_
    processed" entry per already-seen signal every tick, e.g. dozens of them
    once real historical data is loaded; these aren't autonomous ACTIONS,
    they're explicitly the absence of one, and would drown out genuine
    activity in the feed) -- also filtered here rather than displayed.
    """
    persona = entry.get("persona")
    if persona not in _VALID_PERSONAS:
        return None
    if entry.get("action") == "skipped_already_processed":
        return None

    signal_label = _SIGNAL_TYPE_LABELS.get(entry.get("signal_type", ""), "a signal")

    if entry.get("action") == "error":
        # BUGFIX (found live): this used to surface the raw thread_id string
        # ("thread line_manager:team:502:attendance_unrelated_late-1900") --
        # meaningless to a viewer of the feed, and it read as a permanent
        # failure even though a transient one (e.g. a since-fixed config
        # issue) resolves on the next tick without anyone doing anything.
        return f"Couldn't finish reasoning about {signal_label} this cycle -- will retry automatically next cycle."

    summary = entry.get("decision_summary")
    base = summary or f"Processed {signal_label} for {entry.get('scope', 'this scope')}."
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
    report_label = {"daily_digest": "the daily digest", "monthly_leadership": "the monthly leadership report"}.get(
        report_type, report_type.replace("_", " ")
    )
    # BUGFIX (found live): "Generated daily digest covering 0 recent item(s)"
    # read as if something meaningful happened when nothing did -- phrased
    # separately for the empty case rather than a generic template that
    # doesn't distinguish "reported real activity" from "nothing to report".
    if item_count:
        action = f"Generated {report_label}, covering {item_count} recent item{'s' if item_count != 1 else ''}."
    else:
        action = f"Checked in for {report_label} -- nothing new to report since the last cycle."
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
