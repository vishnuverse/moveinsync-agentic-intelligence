"""Shared state/types for the reason subgraph (plan §2 Reason paragraph, §4
Reason subgraph).

`ReasonState` is the input/output contract for the compiled subgraph: it can
be entered with either a sense-stage `signal` (a scheduler/event-triggered
run) or a raw NL `question` (the future chat endpoint) -- `route_to_specialist`
normalizes whichever arrived into a concrete plan (`route`/`research_topic`/
`sql_question`) that the rest of the graph acts on.

`ImpactContext` and `ReasonDecision` are the two structured outputs every
reason-stage run produces, per plan §2's "every metric carries context" rule
and the act-subgraph's need for an explicit sign-off flag.
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.graph.reason.research_agent import ResearchComparison
from app.graph.reason.sql_agent import SQLAgentResult
from app.graph.sense.state import Signal


class ImpactContext(TypedDict):
    """Output of impact_context_builder -- attached to every metric before it
    reaches the act stage (task item 2)."""

    trend_direction: str  # "up" | "down" | "flat" | "unknown"
    severity_band: str  # "low" | "medium" | "high" | "critical"
    business_impact: str  # one human sentence: what this means
    comparison_baseline: dict[str, Any]  # what it was compared against, and how


class ReasonDecision(TypedDict):
    """Output of root_cause_synthesizer -- the final decision object the act
    subgraph consumes (task item 3)."""

    summary: str
    root_cause: str
    recommendation: str
    confidence: float  # 0.0-1.0
    needs_human_signoff: bool
    target_persona: str  # "transport_manager" | "line_manager" | "transport_head"
    supporting_evidence: list[str]


class ReasonState(TypedDict, total=False):
    """Input/output state for the reason subgraph (task item 4).

    Entry: either `signal` (sense-stage origin) or `question` (chat-endpoint
    origin) should be set by the caller; `route_to_specialist` reads whichever
    is present. `org_id`/`persona` are optional scoping hints -- when absent,
    downstream nodes fall back to the signal's own org_id or the data
    contract's default_org_id.
    """

    org_id: str
    persona: str | None

    signal: Signal | None
    question: str | None

    route: str  # "sql" | "research" | "both" | "context_only" -- set by route_to_specialist
    research_topic: str | None
    sql_question: str | None

    sql_result: SQLAgentResult | None
    research_result: ResearchComparison | None
    impact_context: ImpactContext | None
    decision: ReasonDecision | None
