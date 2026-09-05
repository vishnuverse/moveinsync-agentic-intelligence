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
    # SP-B gate (plan §1e): `rule_only` means "skip the LLM entirely" -- not
    # just root_cause_synthesizer's final call, but ALSO call_sql_agent's/
    # call_research_agent's own LLM calls, both of which run BEFORE
    # impact_context_builder and would otherwise still fire even when the
    # final synthesis step is skipped. Forcing "context_only" here is what
    # actually makes rule_only a zero-LLM-call path end to end.
    if state.get("gate_mode") == "rule_only":
        return {"route": "context_only", "research_topic": None, "sql_question": None}

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


# Per-signal_type templated recommendation, used only by rule_based_decision
# below (plan SP-B §1e) -- deliberately short and generic (this is the
# no-LLM path: a plain, always-correct-enough next step, not a tailored
# root-cause narrative). A signal_type with no entry here still gets a safe
# generic fallback rather than a KeyError.
_RULE_ONLY_RECOMMENDATION_TEMPLATES: dict[str, str] = {
    "delay_breach": "Review route scheduling/dispatch with the vendor; this breach cleared the configured threshold by a wide margin.",
    "cost_divergence": "Flag this vendor's invoice for a rate-card review before next settlement.",
    "emissions_over_target": "Prioritize this route for EV/hybrid fleet rotation.",
    "attendance_unrelated_late": "Line manager should follow up directly with the employee; transport isn't a contributing factor.",
    "attendance_correlated_with_transport": "Do not count these lates against the employee; the shuttle is the root cause.",
    "billing_discrepancy": "Open a vendor chargeback dispute for the flagged trips.",
    "performance_variability": "Investigate this route/vendor's consistency, not just its average -- erratic performance often precedes a magnitude breach.",
}


# Mirrors frontend/src/components/ChatPanel.tsx's EXAMPLE_PROMPTS (kept in
# sync by convention, not code-shared, since one's a UI fixture and the
# other's a chat-turn reply) -- a "hi"/"what can you do" reply should point
# at the same kinds of questions the UI's own example chips already suggest.
_SMALLTALK_EXAMPLES_BY_PERSONA: dict[str, list[str]] = {
    "transport_manager": [
        "Which route had the worst on-time performance this week?",
        "Are there any safety incidents I should know about?",
        "Which vendor is falling short of their SLA?",
    ],
    "line_manager": [
        "What's my team's no-show rate this month?",
        "Are shuttle delays affecting my team's attendance?",
    ],
    "transport_head": [
        "Which vendor has the highest billing discrepancy?",
        "How do our emissions compare to the industry baseline?",
    ],
}
_PERSONA_LABELS = {
    "transport_manager": "Transport Manager",
    "line_manager": "Line Manager",
    "transport_head": "Transport & Facilities Head",
}


def smalltalk_reply(state: ReasonState) -> dict[str, Any]:
    """Plan: "handle general chats like hi/hello with an intent node" --
    reached only when app.graph.reason.router.decide_route classified the
    raw message as greeting/pleasantry/farewell/capability-question, via a
    fast keyword check with zero LLM cost (see router.py's own docstring for
    why this is deterministic, not a model call). Produces a real
    ReasonDecision (same shape every other terminal reason node returns) so
    the chat endpoint's `decision.summary` fallback picks it up exactly like
    any other answer -- no special-casing needed downstream.

    Never calls call_sql_agent/call_research_agent/impact_context_builder/
    root_cause_synthesizer -- there is nothing to look up or reason about
    for "hi," so this is a true zero-LLM, zero-DB-query path, same spirit as
    rule_based_decision's no-LLM path for an unambiguous signal."""
    question = (state.get("question") or "").strip().lower().rstrip("!.?, ")
    persona = state.get("persona") or "transport_manager"
    persona_label = _PERSONA_LABELS.get(persona, "transport")
    examples = _SMALLTALK_EXAMPLES_BY_PERSONA.get(persona, _SMALLTALK_EXAMPLES_BY_PERSONA["transport_manager"])

    if question in ("bye", "goodbye", "see you", "see ya", "later", "take care"):
        summary = "Goodbye! I'll be here if you need anything on trips, delays, cost, or safety."
    elif question in ("thanks", "thank you", "thx", "ty", "cheers", "appreciate it"):
        summary = "You're welcome! Let me know if there's anything else you'd like to look into."
    elif question in ("ok", "okay", "got it", "sounds good", "cool", "nice", "great", "awesome"):
        summary = "Got it. I'm here whenever you want to dig into your data."
    else:
        example_lines = "\n".join(f"- {ex}" for ex in examples)
        summary = (
            f"Hi! I'm the {persona_label} assistant. I can answer questions grounded in your live "
            f"transport data -- trips, delays, cost, safety, and attendance. Try asking:\n{example_lines}"
        )

    decision: dict[str, Any] = {
        "summary": summary,
        "root_cause": "",
        "recommendation": "",
        "confidence": 1.0,
        "needs_human_signoff": False,
        "target_persona": persona,
        "supporting_evidence": [],
    }
    return {"decision": decision}


def rule_based_decision(state: ReasonState) -> dict[str, Any]:
    """The no-LLM path (plan SP-B §1e): reached only when
    app.graph.reason.gate.evaluate_gate decided `rule_only` for this signal
    -- an unambiguous, high-margin breach with a proven low false-positive
    rate, not worth spending an LLM call on. Still produces a real
    ReasonDecision (same shape synthesize_root_cause returns) so bridge_to_act
    /act need zero changes: `impact_context_builder` already ran (it's pure,
    no LLM), so this reuses its `business_impact` sentence rather than
    emitting a bare template with no real numbers in it.

    `needs_human_signoff` is always False here -- gate.py's safety floor
    already routed anything requiring signoff (incident/escort_compliance_
    violation/critical severity) to `escalate` before this node could ever
    run, so nothing reaching this node is safety-critical."""
    signal = state.get("signal")
    impact_context = state["impact_context"]
    confidence = state.get("gate_confidence") or 0.75
    gate_reason = state.get("gate_reason") or "high-margin, unambiguous breach"
    signal_type = signal.signal_type if signal else ""
    recommendation = _RULE_ONLY_RECOMMENDATION_TEMPLATES.get(
        signal_type, "Review the flagged item; the rule engine found no ambiguity requiring deeper analysis."
    )
    decision: dict[str, Any] = {
        "summary": signal.summary if signal else impact_context.get("business_impact", "Rule-based determination."),
        "root_cause": f"Rule-based determination ({gate_reason}) -- {impact_context.get('business_impact', '')}",
        "recommendation": recommendation,
        "confidence": confidence,
        "needs_human_signoff": False,
        "target_persona": state.get("persona") or "transport_manager",
        "supporting_evidence": [f"signal[{signal.source}]: {signal.summary}"] if signal else [],
    }
    return {"decision": decision}
