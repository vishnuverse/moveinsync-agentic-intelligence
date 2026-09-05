"""Top-level state for graph.py's unified reason->act graph (plan §4:
"one compiled graph, a light supervisor node, three subgraphs -- sense ->
reason -> act").

`TopState` is deliberately the union of `ReasonState`'s and `ActState`'s keys
(both already `total=False`) rather than a new independent shape: this graph
embeds the compiled act subgraph directly as a node (`graph.add_node("act",
act_subgraph)`), and LangGraph passes the parent's state dict straight into a
subgraph-node as-is -- the subgraph reads/writes only the keys it knows about
(`ActState`'s), so the parent schema only needs to be a superset, not an
identical TypedDict class.

sense is NOT a node in this graph -- it is an independent fan-out producer
(one `run_sense()` call yields N signals) called once per pipeline tick by
`app.graph.supervisor.run_pipeline`, which then invokes this graph once per
(signal, persona) pair with its own `thread_id`. That fan-out shape doesn't
map onto a single linear StateGraph, so it stays in supervisor.py rather than
being forced in here -- see supervisor.py's module docstring for the full
rationale.
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.graph.act.state import ActionType, Severity


class TopState(TypedDict, total=False):
    # ---- reason input ----
    org_id: str
    persona: str | None
    signal: dict[str, Any] | None
    question: str | None
    # When True, the top graph stops after `reason` and never enters
    # `bridge_to_act`/`act` -- used by supervisor.run_chat_turn to make a chat
    # Q&A turn strictly read-only (no agent_notifications row written, no
    # interrupt_gate/HITL pause possible) while still running reason under this
    # thread_id so the trace history (app/services/trace_builder.py reading
    # get_state_history) is unchanged. Signal-driven run_pipeline and run_report
    # leave this unset, so they still run act exactly as before.
    skip_act: bool

    # ---- reason intermediate (trace visibility) ----
    sql_question: str
    sql_result: dict[str, Any]
    research_context: dict[str, Any]
    impact_context: dict[str, Any]

    # ---- reason output / act input ----
    decision: dict[str, Any]
    action_type: ActionType
    scope: str
    severity: Severity
    title: str | None
    related_entity_type: str | None
    related_entity_id: int | None
    source_signal: dict[str, Any] | None
    thread_id: str | None
    audience: str
    audience_language_pref: str | None
    report_items: list[dict[str, Any]]
    period_label: str
    report_type: str
    period_start: Any
    period_end: Any
    use_llm_narrative: bool

    # ---- act output ----
    notification_id: int | None
    notification_status: str | None
    report_id: int | None
    report_storage_ref: str | None
    report_html: str | None
    communication_draft: str | None
    communication_channel_note: str | None
    approval: dict[str, Any] | None
    dispatch_status: str | None
