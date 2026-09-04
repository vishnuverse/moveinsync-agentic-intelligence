"""LangGraph node wrappers for the reason subgraph (plan §2 Reason paragraph,
§4 Reason subgraph, task items 2-4).

Each node is a pure `(ReasonState) -> partial state update` function per plan
§4's "nodes are pure functions" convention -- the actual reusable logic lives
in router.py / impact_context.py / root_cause.py / research_agent/ / sql_agent/
so it stays callable and unit-testable outside the graph too, the same split
sql_agent/nodes.py vs. sql_agent/security.py already establishes.
"""

from __future__ import annotations

from typing import Any

from app.contracts import get_contract
from app.graph.reason.impact_context import build_impact_context, infer_metric_from_signal
from app.graph.reason.research_agent import run_research_agent
from app.graph.reason.root_cause import synthesize_root_cause
from app.graph.reason.router import decide_route
from app.graph.reason.sql_agent import run_sql_agent
from app.graph.reason.state import ReasonState
from app.graph.sense.state import Signal

# Which raw_metric key on a Signal supplies the "observed_value" the research
# agent compares to its curated benchmark, per research topic. sla_timeliness
# has no signal_type routed to "research" today (see router.py) but is kept
# here for the chat-question path, where a question can request that topic
# directly even without an originating signal.
_OBSERVED_VALUE_KEY_BY_TOPIC = {
    "cost_efficiency": "observed_cost_per_km",
    "carbon_footprint": "avg_co2_per_passenger_km",
}


def route_to_specialist(state: ReasonState) -> dict[str, Any]:
    decision = decide_route(state.get("signal"), state.get("question"))
    return {
        "route": decision.route,
        "research_topic": decision.research_topic,
        "sql_question": decision.sql_question,
    }


def _resolve_org_id(state: ReasonState) -> str:
    org_id = state.get("org_id")
    if org_id:
        return org_id
    signal = state.get("signal")
    if signal is not None:
        return signal.org_id
    return get_contract().default_org_id


def call_sql_agent(state: ReasonState) -> dict[str, Any]:
    question = state.get("sql_question") or state.get("question")
    if not question:
        return {"sql_result": None}
    result = run_sql_agent(question, thread_context={"org_id": _resolve_org_id(state)})
    return {"sql_result": result}


def _observed_value_for_research(topic: str, signal: Signal | None) -> float | None:
    if signal is None or not signal.raw_metric:
        return None
    if topic == "sla_timeliness":
        breach_pct = signal.raw_metric.get("breach_pct")
        return (100.0 - float(breach_pct)) if breach_pct is not None else None
    key = _OBSERVED_VALUE_KEY_BY_TOPIC.get(topic)
    if key is None:
        return None
    value = signal.raw_metric.get(key)
    return float(value) if value is not None else None


def call_research_agent(state: ReasonState) -> dict[str, Any]:
    topic = state.get("research_topic")
    if not topic:
        return {"research_result": None}

    observed_value = _observed_value_for_research(topic, state.get("signal"))
    if observed_value is None:
        # Chat-question path with no signal to derive an observed value from
        # yet -- nothing to compare, so skip rather than fabricate a number.
        return {"research_result": None}

    result = run_research_agent(topic, observed_value, thread_context={"org_id": _resolve_org_id(state)})
    return {"research_result": result}


def impact_context_builder(state: ReasonState) -> dict[str, Any]:
    signal = state.get("signal")
    research_result = state.get("research_result")

    if signal is not None:
        metric = infer_metric_from_signal(signal)
    elif research_result is not None:
        metric = {
            "label": research_result["metric_name"],
            "value": research_result["observed_value"],
            "unit": research_result["unit"],
            "prior_value": None,
        }
    else:
        metric = {"label": state.get("question") or "metric", "value": None, "unit": "", "prior_value": None}

    impact_context = build_impact_context(metric, signal=signal, benchmark=research_result)
    return {"impact_context": impact_context}


def root_cause_synthesizer(state: ReasonState) -> dict[str, Any]:
    impact_context = state["impact_context"]
    decision = synthesize_root_cause(
        state.get("signal"),
        impact_context,
        sql_result=state.get("sql_result"),
        benchmark=state.get("research_result"),
    )
    return {"decision": decision}
