"""Builds GET /threads/{thread_id}/trace's TraceStep list (plan §10) straight
from the checkpointer's own state history -- "No new instrumentation -- just
formatting what's already there."

`top_graph.get_state_history(config)` returns one StateSnapshot per top-level
superstep of app.graph.graph's compiled reason->act graph (START -> reason ->
bridge_to_act -> act), oldest-to-newest once reversed. Because `reason` runs
the entire reason subgraph (route_to_specialist -> sql_agent/research_agent ->
impact_context_builder -> root_cause_synthesizer) as one plain Python call
inside a single node (see graph.py's module docstring for why -- that inner
subgraph is compiled without its own checkpointer), sql_result/impact_context/
decision all become visible together in the SAME snapshot, right after
`reason` completes -- so sql_generated/sql_executed/context_built/decision
share one real timestamp, and signal_detected gets its own, earlier one from
the initial snapshot (state as passed into .invoke(), before `reason` ran).
That is a faithful rendering of what actually happened, not an invented
per-node timeline.

Only top-level snapshots (checkpoint_ns == "") are read -- the embedded act
subgraph's own nested checkpoints exist in the same history but carry
act-stage-only fields (notification_id, dispatch_status, ...) that aren't
part of the TraceStepType union the frontend renders (plan's 5 fixed step
kinds), so they're not surfaced here.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.api.deps import get_top_graph
from app.contracts import get_contract
from app.graph.act.db import get_engine

# Matches the synthetic thread_id check_escalations() (supervisor.py) builds
# for an escalated notification: f"{next_persona}:{scope}:escalated-{id}".
_ESCALATED_THREAD_RE = re.compile(r"^[^:]+:.+:escalated-(?P<notification_id>\d+)$")


def _original_notification_row(notification_id: str) -> dict[str, Any] | None:
    contract = get_contract().entity("notification")
    table, c = contract.table, contract.column
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"SELECT {c('thread_id')} AS thread_id, {c('persona')} AS persona, "
                f"{c('created_at')} AS created_at FROM {table} WHERE {c('id')} = :id"
            ),
            {"id": notification_id},
        ).mappings().first()
    return dict(row) if row is not None else None


def _escalated_notification_row(thread_id: str) -> dict[str, Any] | None:
    """The promoted row itself, looked up by its synthetic thread_id.

    Used only as a last resort, when the ORIGINAL notification an escalation
    points at can no longer be resolved into a trace -- see build_trace.
    """
    contract = get_contract().entity("notification")
    table, c = contract.table, contract.column
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"SELECT {c('persona')} AS persona, {c('message')} AS message, "
                f"{c('created_at')} AS created_at FROM {table} "
                f"WHERE {c('thread_id')} = :thread_id "
                f"ORDER BY {c('id')} DESC LIMIT 1"
            ),
            {"thread_id": thread_id},
        ).mappings().first()
    return dict(row) if row is not None else None


def _ts(snapshot: Any) -> str:
    created = getattr(snapshot, "created_at", None)
    if isinstance(created, str) and created:
        return created
    if hasattr(created, "isoformat"):
        return created.isoformat()
    return datetime.now(timezone.utc).isoformat()


def _is_top_level(snapshot: Any) -> bool:
    configurable = (snapshot.config or {}).get("configurable", {}) if snapshot.config else {}
    return not configurable.get("checkpoint_ns")


def _signal_detail(signal: dict[str, Any] | None, question: str | None) -> str:
    if signal:
        parts = [signal.get("summary") or f"{signal.get('signal_type')} signal detected."]
        entity = signal.get("entity_type")
        entity_id = signal.get("entity_id")
        severity = signal.get("severity")
        if entity and entity_id is not None:
            parts.append(f"Entity: {entity} {entity_id}.")
        if severity:
            parts.append(f"Severity: {severity}.")
        source = signal.get("source")
        if source:
            parts.append(f"Detector: {source}.")
        return " ".join(parts)
    if question:
        return f'Chat question received: "{question}"'
    return "Signal detected."


def _sql_executed_detail(sql_result: dict[str, Any]) -> str:
    success = sql_result.get("success")
    rows = sql_result.get("rows_returned")
    retries = sql_result.get("sql_retry_count") or 0
    errors = sql_result.get("query_error_log") or []
    parts: list[str] = []
    if success:
        parts.append(f"Returned {rows} row(s)." if rows is not None else "Query succeeded.")
    else:
        parts.append("Query did not complete successfully within the retry cap.")
    if retries:
        parts.append(f"Self-corrected {retries} time(s) after a query error.")
    if errors:
        parts.append(f"Last error: {errors[-1]}")
    return " ".join(parts) or "SQL executed against the read-only replica."


def _context_detail(impact_context: dict[str, Any]) -> str:
    business_impact = impact_context.get("business_impact")
    if business_impact:
        return business_impact
    trend = impact_context.get("trend_direction", "unknown")
    band = impact_context.get("severity_band", "unknown")
    return f"Trend: {trend}, severity band: {band}."


def _decision_detail(decision: dict[str, Any]) -> str:
    # NOTE (plan SP-B §9a): `recommendation` used to only ever appear folded
    # into this joined string -- easy to miss inside a paragraph. It's kept
    # here too (so the collapsed detail text still reads completely on its
    # own), but build_trace's `decision` step ALSO carries it as its own
    # `recommendation` key now, so the frontend can render it as a visually
    # distinct "Recommended Action" block instead of re-parsing this string.
    parts: list[str] = []
    if decision.get("summary"):
        parts.append(decision["summary"])
    if decision.get("root_cause"):
        parts.append(f"Root cause: {decision['root_cause']}")
    if decision.get("recommendation"):
        parts.append(f"Recommendation: {decision['recommendation']}")
    if decision.get("needs_human_signoff"):
        parts.append("Routed through the interrupt gate -- held for human sign-off.")
    confidence = decision.get("confidence")
    if isinstance(confidence, (int, float)):
        parts.append(f"Confidence: {confidence:.0%}")
    return " ".join(parts) or "Decision recorded."


def _gate_decision_row(thread_id: str) -> dict[str, Any] | None:
    """Plan SP-B §8: gate_decisions rows are written directly by
    supervisor.run_pipeline BEFORE the graph even runs, so they live outside
    the LangGraph checkpoint history build_trace otherwise reads entirely
    from -- this is the one place trace_builder.py reaches past the
    checkpointer for real. Returns the single most recent decision for this
    thread_id (there is normally exactly one -- a thread_id is per-signal),
    or None if the gate was never consulted (e.g. a chat-question thread,
    which never goes through supervisor.run_pipeline's gate at all)."""
    contract = get_contract().entity("gate_decision")
    table, c = contract.table, contract.column
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {c('action')} AS action, {c('reason')} AS reason, "
                f"{c('matched_rule')} AS matched_rule, {c('confidence')} AS confidence, "
                f"{c('created_at')} AS created_at "
                f"FROM {table} WHERE {c('thread_id')} = :thread_id "
                f"ORDER BY {c('created_at')} DESC LIMIT 1"
            ),
            {"thread_id": thread_id},
        ).mappings().first()
    return dict(row) if row is not None else None


def build_trace(thread_id: str) -> list[dict[str, Any]]:
    top_graph = get_top_graph()
    config = {"configurable": {"thread_id": thread_id}}
    snapshots = list(top_graph.get_state_history(config))
    if not snapshots:
        # BUGFIX (found live: an escalated notification's "How was this
        # computed?" 404'd): check_escalations (supervisor.py) inserts its
        # promoted agent_notifications row with a synthetic thread_id
        # directly via SQL -- it never runs top_graph.invoke() under that
        # thread_id (escalation promotes visibility to a more senior
        # persona, it doesn't re-reason), so there is genuinely no
        # checkpoint history to read there. The honest, useful trace is the
        # ORIGINAL notification's own reasoning (recursed on its real
        # thread_id) with one extra step appended explaining the promotion,
        # not a 404 that makes an escalated item look untraceable.
        match = _ESCALATED_THREAD_RE.match(thread_id)
        if match:
            original = _original_notification_row(match.group("notification_id"))
            if original and original["thread_id"] and original["thread_id"] != thread_id:
                original_steps = build_trace(original["thread_id"])
                if original_steps:
                    created_at = original["created_at"]
                    original_steps.append(
                        {
                            "step": "escalation",
                            "label": "Escalated",
                            "detail": (
                                f"This item went unacknowledged by {original['persona']} past its "
                                "severity's configured timeout and was escalated here for visibility "
                                "-- the reasoning above is unchanged from the original."
                            ),
                            "timestamp": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                        }
                    )
                    return original_steps

            # The original is unresolvable -- deleted, or its own checkpoint
            # history has been trimmed. The promoted row is still sitting in
            # somebody's inbox, so 404 ("no trace found") is the one answer
            # that must never come back for it. Degrade to what the escalated
            # row itself can honestly account for: that it was promoted here,
            # by whom it went unacknowledged, and when.
            escalated = _escalated_notification_row(thread_id)
            if escalated:
                detail = (escalated["message"] or "").strip()
                if not detail:
                    detail = f"Escalated to {escalated['persona']} for visibility."
                created_at = escalated["created_at"]
                return [
                    {
                        "step": "escalation",
                        "label": "Escalated",
                        "detail": (
                            f"{detail} The original notification's own reasoning trace is no "
                            "longer available, so only the escalation itself is shown here."
                        ),
                        "timestamp": (
                            created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
                        ),
                    }
                ]
        return []

    snapshots.reverse()  # oldest -> newest
    top_snapshots = [s for s in snapshots if _is_top_level(s)]
    if not top_snapshots:
        top_snapshots = snapshots

    steps: list[dict[str, Any]] = []
    have_signal = have_gate = have_sql = have_context = have_decision = False
    gate_row = _gate_decision_row(thread_id)

    for snap in top_snapshots:
        values = snap.values or {}
        timestamp = _ts(snap)

        if not have_signal:
            signal = values.get("signal")
            question = values.get("question")
            if signal or question or values.get("thread_id"):
                detector_sql = (signal or {}).get("raw_metric", {}).get("detector_sql") if signal else None
                step: dict[str, Any] = {
                    "step": "signal_detected",
                    "label": "Signal Detected",
                    "detail": _signal_detail(signal, question),
                    "timestamp": timestamp,
                }
                if detector_sql:
                    # plan SP-B §8: the detector's OWN hardcoded SQL that
                    # actually found this signal -- distinct from (and, for a
                    # rule_only decision, the ONLY SQL in) the trace, since
                    # sql_generated/sql_executed below only ever show the SQL
                    # *agent's* dynamically-generated query, which never runs
                    # at all on the rule_only path (see route_to_specialist).
                    step["sql"] = detector_sql
                steps.append(step)
                have_signal = True

                # plan SP-B §8: inserted immediately after signal_detected,
                # sourced from gate_decisions (not the checkpoint state --
                # see _gate_decision_row's docstring). Absent entirely for a
                # chat-question thread, which never goes through the gate.
                if not have_gate and gate_row is not None:
                    action_label = gate_row["action"].replace("_", " ").title()
                    confidence = gate_row.get("confidence")
                    confidence_clause = f", confidence: {float(confidence):.0%}" if confidence is not None else ""
                    steps.append(
                        {
                            "step": "gate_decision",
                            "label": "Gate Evaluated",
                            "detail": (
                                f"{action_label} -- {gate_row['reason']} "
                                f"(rule: {gate_row['matched_rule']}{confidence_clause})"
                            ),
                            "timestamp": gate_row["created_at"].isoformat(),
                        }
                    )
                    have_gate = True

        sql_result = values.get("sql_result") or {}
        if not have_sql and sql_result:
            generated_sql = sql_result.get("generated_sql")
            retry_count = sql_result.get("sql_retry_count") or 0
            if generated_sql:
                steps.append(
                    {
                        "step": "sql_generated",
                        "label": "SQL Generated",
                        "detail": "Text-to-SQL agent grounded on live schema DDL + sample rows, chain-of-thought prompted.",
                        "sql": generated_sql,
                        "retry_count": retry_count,
                        "timestamp": timestamp,
                    }
                )
                steps.append(
                    {
                        "step": "sql_executed",
                        "label": "SQL Executed",
                        "detail": _sql_executed_detail(sql_result),
                        "retry_count": retry_count,
                        "timestamp": timestamp,
                    }
                )
            have_sql = True

        impact_context = values.get("impact_context") or {}
        if not have_context and impact_context:
            steps.append(
                {
                    "step": "context_built",
                    "label": "Impact Context Attached",
                    "detail": _context_detail(impact_context),
                    "timestamp": timestamp,
                }
            )
            have_context = True

        decision = values.get("decision") or {}
        if not have_decision and decision:
            decision_step: dict[str, Any] = {
                "step": "decision",
                "label": "Decision",
                "detail": _decision_detail(decision),
                "timestamp": timestamp,
            }
            # plan SP-B §9a: a distinct field, not just folded into `detail`,
            # so the frontend can render a visually separate "Recommended
            # Action" block instead of re-parsing the joined string.
            if decision.get("recommendation"):
                decision_step["recommendation"] = decision["recommendation"]
            steps.append(decision_step)
            have_decision = True

    return steps
