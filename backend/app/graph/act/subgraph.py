"""Act subgraph assembly (plan §4 Act subgraph, §11
backend/app/graph/act/subgraph.py).

    route_by_action_type (conditional entry)
        -> notification_dispatch | html_report_generator | communication_drafter
        -> route_after_action (conditional)
              needs_human_signoff -> interrupt_gate -> send_dispatch
              otherwise           -> send_dispatch

--------------------------------------------------------------------------
Embedding contract for the supervisor-assembly agent
--------------------------------------------------------------------------

    from app.graph.act import build_act_subgraph, ActState

    # A checkpointer is REQUIRED for interrupt()/Command(resume=...) to work
    # at all -- LangGraph needs it to persist the paused state. The plan
    # specifies PostgresSaver for the real system (wired by the
    # supervisor-assembly agent); MemorySaver is fine for standalone/test use.
    act_graph = build_act_subgraph(checkpointer=my_postgres_saver)

    initial_state: ActState = {
        "decision": reason_decision,          # ReasonDecision-shaped dict from reason/
        "action_type": "notification",        # "notification" | "report" | "communication"
        "org_id": "moveinsync-demo",
        "persona": "transport_manager",
        "scope": "region_west",
        # ... plus action-type-specific fields, see state.py
    }
    config = {"configurable": {"thread_id": "transport_manager:region_west:tm5-incident-492"}}

    result = act_graph.invoke(initial_state, config=config)
    # If needs_human_signoff was True, result contains
    # `__interrupt__` (LangGraph's own key) with the interrupt payload, and
    # execution is PAUSED -- nothing has been sent. Resume it later with:

    from langgraph.types import Command
    resumed = act_graph.invoke(
        Command(resume={"approved": True, "approver": "line.manager@moveinsync.demo", "comment": "ok, proceed"}),
        config=config,   # SAME thread_id -- this is how LangGraph finds the paused run
    )
    # `resumed["dispatch_status"]` is now "sent:<action_type>" (or "rejected"
    # if approved=False was passed instead).

thread_id convention: use `{persona}:{scope}:{unique-decision-ref}` so a
resumed thread is unambiguous and the same thread_id also becomes the
idempotency key for the `agent_notifications`/`agent_reports` rows this
subgraph writes (see db.py).
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from .communication_drafter import communication_drafter
from .html_report_agent import html_report_generator
from .nodes import interrupt_gate, notification_dispatch, route_after_action, route_by_action_type, send_dispatch
from .state import ActState

_ACTION_NODES = ("notification_dispatch", "html_report_generator", "communication_drafter")


def build_act_subgraph(checkpointer: BaseCheckpointSaver | None = None):
    graph = StateGraph(ActState)

    graph.add_node("notification_dispatch", notification_dispatch)
    graph.add_node("html_report_generator", html_report_generator)
    graph.add_node("communication_drafter", communication_drafter)
    graph.add_node("interrupt_gate", interrupt_gate)
    graph.add_node("send_dispatch", send_dispatch)

    graph.set_conditional_entry_point(
        route_by_action_type,
        {
            "notification": "notification_dispatch",
            "report": "html_report_generator",
            "communication": "communication_drafter",
        },
    )

    for action_node in _ACTION_NODES:
        graph.add_conditional_edges(
            action_node,
            route_after_action,
            {"interrupt_gate": "interrupt_gate", "send_dispatch": "send_dispatch"},
        )

    graph.add_edge("interrupt_gate", "send_dispatch")
    graph.add_edge("send_dispatch", END)

    return graph.compile(checkpointer=checkpointer)
