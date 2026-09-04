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

from datetime import datetime, timezone
from typing import Any

from app.api.deps import get_top_graph


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


def build_trace(thread_id: str) -> list[dict[str, Any]]:
    top_graph = get_top_graph()
    config = {"configurable": {"thread_id": thread_id}}
    snapshots = list(top_graph.get_state_history(config))
    if not snapshots:
        return []

    snapshots.reverse()  # oldest -> newest
    top_snapshots = [s for s in snapshots if _is_top_level(s)]
    if not top_snapshots:
        top_snapshots = snapshots

    steps: list[dict[str, Any]] = []
    have_signal = have_sql = have_context = have_decision = False

    for snap in top_snapshots:
        values = snap.values or {}
        timestamp = _ts(snap)

        if not have_signal:
            signal = values.get("signal")
            question = values.get("question")
            if signal or question or values.get("thread_id"):
                steps.append(
                    {
                        "step": "signal_detected",
                        "label": "Signal Detected",
                        "detail": _signal_detail(signal, question),
                        "timestamp": timestamp,
                    }
                )
                have_signal = True

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
            steps.append(
                {
                    "step": "decision",
                    "label": "Decision",
                    "detail": _decision_detail(decision),
                    "timestamp": timestamp,
                }
            )
            have_decision = True

    return steps
