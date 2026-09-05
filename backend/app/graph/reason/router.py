"""route_to_specialist decision logic (plan §4 Reason subgraph, task item 4).

Pure decision function, kept separate from the LangGraph node wrapper in
nodes.py so it's unit-testable without a graph runtime -- the same separation
sql_agent/security.py uses for its own pure logic vs. sql_agent/nodes.py's
graph-facing wrappers.

Decides whether a reason-stage run needs the SQL agent (an internal-data
question), the research agent (external benchmark context), both, or neither
(the sense stage already has everything -- straight to context-building).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.graph.sense.state import Signal

# Signals whose raw_metric already carries every number needed, but whose
# "why" benefits from trip-level supporting detail only the SQL agent can
# pull (specific drivers/times/patterns behind an aggregate).
_SQL_ROUTE_SIGNAL_TYPES = {
    "delay_breach",
    "incident",
    "attendance_correlated_with_transport",
    "attendance_unrelated_late",
}

# Signals that are only meaningfully "good or bad" once compared to an
# external benchmark (plan §2: "so a number can be judged good/bad, not just
# relative to itself") -- routed to the research agent instead of the SQL
# agent, which only ever answers from internal data.
_RESEARCH_ROUTE_SIGNAL_TYPES = {
    "cost_divergence",
    "emissions_over_target",
}
_RESEARCH_TOPIC_BY_SIGNAL_TYPE = {
    "cost_divergence": "cost_efficiency",
    "emissions_over_target": "carbon_footprint",
}

_RESEARCH_KEYWORDS = ("carbon", "emission", "co2", "sustainab", "benchmark", "industry", "esg", "footprint")

# Chat-only intent classification -- a fast, zero-LLM keyword check, not
# another model call: matching this app's own "reduce unnecessary LLM
# calls" discipline (the SP-B gate), there is no reason to spend a real LLM
# call classifying "hi" vs. "what was our OTA last week" when a short,
# well-scoped pattern list does it for free and instantly. Only the WHOLE
# (trimmed, lowercased) message matching one of these short lists counts --
# "hi, what was our OTA yesterday" still reaches the SQL agent; a bare "hi"
# does not. A message that doesn't match any of these falls through to the
# existing SQL/research routing unchanged.
_GREETING_PATTERNS = (
    "hi", "hii", "hiya", "hello", "hey", "hey there", "yo", "sup", "howdy",
    "good morning", "good afternoon", "good evening", "good night",
)
_PLEASANTRY_PATTERNS = (
    "thanks", "thank you", "thx", "ty", "cool", "nice", "great", "awesome",
    "ok", "okay", "cheers", "appreciate it", "got it", "sounds good",
)
_FAREWELL_PATTERNS = ("bye", "goodbye", "see you", "see ya", "later", "take care")
_CAPABILITY_PHRASES = (
    "who are you", "what are you", "what can you do", "what do you do",
    "how do you work", "what can i ask", "what can i ask you", "what do you know",
)


def _is_smalltalk(question: str) -> bool:
    normalized = question.strip().lower().rstrip("!.?, ")
    if not normalized:
        return True
    if normalized in _GREETING_PATTERNS or normalized in _PLEASANTRY_PATTERNS or normalized in _FAREWELL_PATTERNS:
        return True
    # Capability questions ("what can you do") are short and phrase-shaped
    # rather than exact-match-shaped ("help me out here" should still count),
    # so this checks containment within a short message rather than an exact
    # match -- long enough to allow natural phrasing, short enough that a
    # real data question containing an incidental "help" word (rare, but
    # possible: "can you help me understand the delay on route 12") doesn't
    # get misrouted; the word-count cap is what keeps that safe.
    if len(normalized.split()) <= 7 and any(phrase in normalized for phrase in _CAPABILITY_PHRASES):
        return True
    return False


@dataclass(frozen=True)
class RouteDecision:
    route: str  # "sql" | "research" | "both" | "context_only" | "smalltalk"
    research_topic: str | None
    sql_question: str | None


def decide_route(signal: Signal | None, question: str | None) -> RouteDecision:
    """`question` (chat path) takes priority over `signal` when both are
    present -- a direct NL question always needs the SQL agent at minimum,
    since that's the only node that can ground an answer in live data."""

    if question is not None:
        if _is_smalltalk(question):
            return RouteDecision(route="smalltalk", research_topic=None, sql_question=None)
        return _decide_for_question(question)

    if signal is None:
        return RouteDecision(route="context_only", research_topic=None, sql_question=None)

    return _decide_for_signal(signal)


def _decide_for_question(question: str) -> RouteDecision:
    needs_research = any(kw in question.lower() for kw in _RESEARCH_KEYWORDS)
    research_topic = _guess_topic_from_question(question) if needs_research else None
    route = "both" if needs_research else "sql"
    return RouteDecision(route=route, research_topic=research_topic, sql_question=question)


def _decide_for_signal(signal: Signal) -> RouteDecision:
    if signal.signal_type in _RESEARCH_ROUTE_SIGNAL_TYPES:
        return RouteDecision(
            route="research",
            research_topic=_RESEARCH_TOPIC_BY_SIGNAL_TYPE[signal.signal_type],
            sql_question=None,
        )

    if signal.signal_type in _SQL_ROUTE_SIGNAL_TYPES:
        return RouteDecision(
            route="sql",
            research_topic=None,
            sql_question=_synthesize_question_for_signal(signal),
        )

    # data_quality_issue and any future/unknown signal_type: the sense
    # detector already computed everything needed (plan §4's "neither" case).
    return RouteDecision(route="context_only", research_topic=None, sql_question=None)


def _guess_topic_from_question(question: str) -> str:
    lowered = question.lower()
    if any(kw in lowered for kw in ("carbon", "emission", "co2", "footprint")):
        return "carbon_footprint"
    if any(kw in lowered for kw in ("sla", "on-time", "on time", "timeliness", "punctual")):
        return "sla_timeliness"
    return "cost_efficiency"


def _synthesize_question_for_signal(signal: Signal) -> str:
    return (
        f"For {signal.entity_type} {signal.entity_id}: {signal.summary} "
        "What does the recent trip-level data show that could explain this, and is there a "
        "specific pattern (a particular driver, time of day, or day of week) worth calling out?"
    )
