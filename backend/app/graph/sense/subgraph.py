"""Sense subgraph assembly (plan §4 Sense subgraph).

`poll_or_event_entry` normalizes whichever entry mode the caller used (a
scheduler's `since` delta window, or a `event` dict from the LISTEN/NOTIFY
listener in listener.py) into a concrete `since`, then all six detectors run
as parallel branches of one LangGraph `StateGraph`, and their `signals` lists
merge via the additive reducer declared on `SenseState.signals`.

This is a real LangGraph subgraph (not just a plain function) so the
top-level supervisor/reason/act graphs built by other agents can compose it
uniformly -- `build_sense_subgraph().compile()` returns a `Runnable` like any
other node/subgraph in the top-level graph.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langgraph.graph import END, START, StateGraph
from sqlalchemy import Engine

from app.graph.sense import nodes as detectors
from app.graph.sense.db import get_engine
from app.graph.sense.state import SenseState

DEFAULT_ENTRY_LOOKBACK = timedelta(hours=24)

_DETECTOR_NAMES = (
    "detect_delay_signal",
    "detect_incident_signal",
    "detect_cost_anomaly",
    "detect_emissions_signal",
    "detect_attendance_correlation",
    "detect_escort_compliance_signal",
    "detect_billing_discrepancy_signal",
    "flag_data_quality",
)


def poll_or_event_entry(state: SenseState) -> dict:
    """Fills in `since` when the caller didn't pass one (scheduler ticks
    normally do; an event-driven invocation from the listener may only pass
    `event` and rely on this default). Does not otherwise branch on `event`
    today -- every detector re-scans its own window regardless of which
    table's row triggered the run, since one changed row can be relevant to
    more than one detector (e.g. a new trip affects delay AND attendance
    correlation AND cost, once its cost/emissions rows land)."""

    org_id = state.get("org_id")
    if not org_id:
        from app.contracts import get_contract

        org_id = get_contract().default_org_id

    since = state.get("since")
    if since is None:
        since = datetime.now(timezone.utc) - DEFAULT_ENTRY_LOOKBACK

    return {"org_id": org_id, "since": since}


def _make_detector_node(detector_name: str, engine: Engine):
    detector_fn = getattr(detectors, detector_name)

    def node(state: SenseState) -> dict:
        org_id = state["org_id"]
        since = state.get("since")
        with engine.connect() as conn:
            signals = detector_fn(conn, org_id, since)
        return {"signals": signals}

    node.__name__ = detector_name
    return node


def build_sense_subgraph(engine: Engine | None = None) -> StateGraph:
    """Returns an uncompiled `StateGraph` -- call `.compile()` on it (with or
    without a checkpointer) before invoking. Kept uncompiled here so a caller
    composing this into a larger graph can add its own checkpointer/config at
    the outer `.compile()` call instead of being forced into this module's."""

    engine = engine or get_engine()
    graph = StateGraph(SenseState)

    graph.add_node("poll_or_event_entry", poll_or_event_entry)
    for name in _DETECTOR_NAMES:
        graph.add_node(name, _make_detector_node(name, engine))

    graph.add_edge(START, "poll_or_event_entry")
    for name in _DETECTOR_NAMES:
        graph.add_edge("poll_or_event_entry", name)
        graph.add_edge(name, END)

    return graph


def run_sense(
    org_id: str | None = None,
    since: datetime | None = None,
    event: dict | None = None,
    engine: Engine | None = None,
) -> SenseState:
    """Convenience entry point for a scheduler that just wants signals back
    without holding a compiled graph handle itself. Equivalent to
    `build_sense_subgraph(engine).compile().invoke({...})`."""

    compiled = build_sense_subgraph(engine).compile()
    initial_state: SenseState = {"signals": []}
    if org_id is not None:
        initial_state["org_id"] = org_id
    if since is not None:
        initial_state["since"] = since
    if event is not None:
        initial_state["event"] = event
    return compiled.invoke(initial_state)
