"""Top-level LangGraph wiring (plan §4, §11, §12 step 9): the single
compiled graph that runs a signal or a chat question through reason, then
routes the resulting `ReasonDecision` into the act subgraph -- embedded here
as a genuine LangGraph subgraph node, not a black-box function call, so
`interrupt()`/`Command(resume=...)` persist through this graph's own
checkpointer the same way any other LangGraph subgraph does (LangGraph
propagates a parent's checkpointer down into a compiled subgraph added as a
node when that subgraph itself was compiled with `checkpointer=None` --
verified live, see build report).

    START -> reason -> bridge_to_act -> act (embedded act subgraph) -> END

`reason` wraps `app.graph.reason.run_reason`; `bridge_to_act` is the "light
supervisor node" the plan describes at the graph level -- it turns a
`ReasonDecision` plus the originating signal into the act subgraph's routing
inputs (`action_type`, `scope`, `title`, `severity`, ...). Persona routing
itself (which persona(s) a signal is *for*) happens one level up, in
`app.graph.supervisor.run_pipeline`'s fan-out -- by the time a signal reaches
this graph, `persona` is already fixed for this specific invocation.

This graph is invoked once per (signal-or-question, persona) pair, each with
its own `thread_id` (`app.graph.supervisor.build_thread_id`) -- that thread_id
is what a future `GET /threads/{thread_id}/trace` endpoint (plan §10) reads
back out of this graph's own checkpointer.
"""

from __future__ import annotations

import functools
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.act import build_act_subgraph
from app.graph.act.state import ActionType
from app.graph.reason import run_reason

from .state import TopState

# TM5-style signals get drafted as an outbound driver/vendor/leadership
# communication (act's communication_drafter) rather than a plain inbox
# notification -- everything else is a proactive alert (plan §1 TM1/LM3/TH1).
# Kept here (not in supervisor.py) to avoid a supervisor<->graph import cycle:
# supervisor.py imports build_top_graph from this module, so this module
# cannot import back from supervisor.py.
_COMMUNICATION_SIGNAL_TYPES = {"incident"}
_SEVERITY_MAP = {"critical": "critical", "high": "warning", "medium": "warning", "low": "info"}


def _infer_action_type(signal: dict[str, Any] | None, decision: dict[str, Any]) -> ActionType:
    if signal and signal.get("signal_type") in _COMMUNICATION_SIGNAL_TYPES:
        return "communication"
    return "notification"


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bridge_to_act(state: TopState) -> dict[str, Any]:
    """The graph-level "light supervisor node" (plan §4) run between reason
    and act: turns a ReasonDecision + the originating signal into act's
    routing inputs (action_type, entity refs, severity, audience). Persona
    routing itself (which persona(s) get a signal at all) already happened
    one level up in app.graph.supervisor.run_pipeline's fan-out, before this
    graph was even invoked."""
    decision = state.get("decision", {}) or {}
    signal = state.get("signal")
    action_type = _infer_action_type(signal, decision)

    updates: dict[str, Any] = {"action_type": action_type, "source_signal": signal}
    if signal:
        updates["related_entity_type"] = signal.get("entity_type")
        updates["related_entity_id"] = _safe_int(signal.get("entity_id"))
        mapped_severity = _SEVERITY_MAP.get(signal.get("severity"))
        if mapped_severity:
            updates["severity"] = mapped_severity
    if action_type == "communication":
        updates["audience"] = "vendor" if signal and signal.get("entity_type") in ("incident", "route") else "leadership"

    return updates


def reason_node(state: TopState) -> dict:
    result = run_reason(
        signal=state.get("signal"),
        question=state.get("question"),
        org_id=state.get("org_id"),
        persona=state.get("persona"),
    )
    return {
        "org_id": result.get("org_id", state.get("org_id")),
        "sql_question": result.get("sql_question", ""),
        "sql_result": result.get("sql_result", {}),
        "research_context": result.get("research_context", {}),
        "impact_context": result.get("impact_context", {}),
        "decision": result.get("decision", {}),
    }


@functools.lru_cache(maxsize=4)
def _cached_top_graph(checkpointer: BaseCheckpointSaver):
    graph = StateGraph(TopState)
    graph.add_node("reason", reason_node)
    graph.add_node("bridge_to_act", bridge_to_act)
    # Compiled WITHOUT its own checkpointer -- it inherits the parent's
    # (below) when embedded as a node. Giving it one of its own here would
    # make it an independently-checkpointed subgraph instead of sharing this
    # graph's thread_id/history, which breaks the single-thread trace story
    # (plan §10).
    act_subgraph = build_act_subgraph(checkpointer=None)
    graph.add_node("act", act_subgraph)

    graph.add_edge(START, "reason")
    graph.add_edge("reason", "bridge_to_act")
    graph.add_edge("bridge_to_act", "act")
    graph.add_edge("act", END)

    return graph.compile(checkpointer=checkpointer)


def build_top_graph(checkpointer: BaseCheckpointSaver):
    """Returns the compiled reason->act graph, cached per checkpointer
    instance (there is normally exactly one shared checkpointer per process,
    see app.checkpointer.get_checkpointer)."""
    return _cached_top_graph(checkpointer)
