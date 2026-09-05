"""Reason subgraph assembly (plan §4 Reason subgraph, task item 4).

route_to_specialist -> [sql_agent cluster | research_agent] (parallel when
both apply) -> impact_context_builder -> root_cause_synthesizer, composable
by the top-level supervisor alongside the sense and act subgraphs (built by
other agents) -- `build_reason_subgraph().compile()` returns a Runnable like
any other subgraph node in the top-level graph, the same convention
sense/subgraph.py's build_sense_subgraph() already establishes.

Embedding contract for the top-level supervisor/act-subgraph builder:
    from app.graph.reason.subgraph import run_reason
    result = run_reason(signal=some_signal)              # sense-triggered
    result = run_reason(question="...", org_id="...")     # chat-triggered
    result["decision"]  # -> ReasonDecision (state.py) for the act subgraph
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.reason import nodes
from app.graph.reason.state import ReasonState
from app.graph.sense.state import Signal

_SPECIALIST_DESTINATIONS = ["call_sql_agent", "call_research_agent", "impact_context_builder", "smalltalk_reply"]


def _route_after_specialist(state: ReasonState) -> list[str] | str:
    route = state.get("route", "context_only")
    if route == "both":
        return ["call_sql_agent", "call_research_agent"]
    if route == "sql":
        return "call_sql_agent"
    if route == "research":
        return "call_research_agent"
    if route == "smalltalk":
        # Plan: chat intent node -- "hi"/"thanks"/"what can you do" never
        # reaches the SQL agent, the research agent, or an LLM call at all;
        # smalltalk_reply is a standalone terminal node (straight to END,
        # not through impact_context_builder/root_cause_synthesizer, which
        # have nothing to build context around for a greeting).
        return "smalltalk_reply"
    return "impact_context_builder"


def _route_after_impact_context(state: ReasonState) -> str:
    """SP-B gate (plan §1e): `gate_mode == "rule_only"` (set by
    app.graph.reason.gate.evaluate_gate, via supervisor.run_pipeline ->
    graph.py's reason_node) skips root_cause_synthesizer -- the ONLY LLM call
    in this subgraph -- entirely, routing to the templated
    rule_based_decision node instead. Any other value (including the
    default, unset case) takes today's existing LLM path, unchanged."""
    return "rule_based_decision" if state.get("gate_mode") == "rule_only" else "root_cause_synthesizer"


def build_reason_subgraph() -> StateGraph:
    """Returns an uncompiled StateGraph -- call `.compile()` (with or without
    a checkpointer/store) before invoking, same convention as
    sense/subgraph.py's build_sense_subgraph()."""

    graph = StateGraph(ReasonState)

    graph.add_node("route_to_specialist", nodes.route_to_specialist)
    graph.add_node("call_sql_agent", nodes.call_sql_agent)
    graph.add_node("call_research_agent", nodes.call_research_agent)
    graph.add_node("impact_context_builder", nodes.impact_context_builder)
    graph.add_node("rule_based_decision", nodes.rule_based_decision)
    graph.add_node("root_cause_synthesizer", nodes.root_cause_synthesizer)
    graph.add_node("smalltalk_reply", nodes.smalltalk_reply)

    graph.add_edge(START, "route_to_specialist")
    graph.add_conditional_edges("route_to_specialist", _route_after_specialist, _SPECIALIST_DESTINATIONS)
    graph.add_edge("call_sql_agent", "impact_context_builder")
    graph.add_edge("call_research_agent", "impact_context_builder")
    graph.add_conditional_edges(
        "impact_context_builder", _route_after_impact_context, ["root_cause_synthesizer", "rule_based_decision"]
    )
    graph.add_edge("root_cause_synthesizer", END)
    graph.add_edge("rule_based_decision", END)
    graph.add_edge("smalltalk_reply", END)

    return graph


def run_reason(
    *,
    signal: Signal | None = None,
    question: str | None = None,
    org_id: str | None = None,
    persona: str | None = None,
    gate_mode: str | None = None,
    gate_reason: str | None = None,
    gate_confidence: float | None = None,
) -> ReasonState:
    """Convenience entry point mirroring sense/subgraph.py's run_sense() --
    builds+compiles+invokes without the caller holding a compiled graph
    handle itself. Exactly one of `signal`/`question` is expected to carry
    the actual trigger; passing neither degrades gracefully to
    `route="context_only"` rather than erroring.

    `gate_mode`/`gate_reason`/`gate_confidence` (plan SP-B §1e) are optional
    -- when None (the default, and always the case for the chat/question
    entry path), the subgraph takes its original, unmodified LLM path."""

    compiled = build_reason_subgraph().compile()
    initial_state: ReasonState = {"signal": signal, "question": question}
    if org_id is not None:
        initial_state["org_id"] = org_id
    if persona is not None:
        initial_state["persona"] = persona
    if gate_mode is not None:
        initial_state["gate_mode"] = gate_mode
    if gate_reason is not None:
        initial_state["gate_reason"] = gate_reason
    if gate_confidence is not None:
        initial_state["gate_confidence"] = gate_confidence
    return compiled.invoke(initial_state)
