"""State + shared types for the act subgraph (plan §2 Act paragraph, §4 Act
subgraph, §11 backend/app/graph/act/).

`ReasonDecision` is the seam between the reason subgraph (built by a sibling
agent, backend/app/graph/reason/) and this act subgraph: whatever produces
one, act only ever consumes this shape, so the two subgraphs stay decoupled
from each other's internals regardless of build order.

`ActState` wraps a `ReasonDecision` with everything act needs to route, write
to `agent_notifications`/`agent_reports`, and report a final dispatch status.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

ActionType = Literal["notification", "report", "communication"]
Persona = Literal["transport_manager", "line_manager", "transport_head"]
Severity = Literal["info", "warning", "critical"]


class ReasonDecision(TypedDict, total=False):
    """Structured output the reason subgraph hands to act (plan §2).

    `total=False`: every field is optional at the type level on purpose. This
    subgraph was built in parallel with reason/ against the plan's documented
    shape, not against reason/'s actual code -- nodes here use `.get(...)`
    with sane fallbacks throughout so a decision object missing a field
    degrades gracefully instead of raising.
    """

    summary: str
    root_cause: str
    recommendation: str
    confidence: float
    needs_human_signoff: bool
    target_persona: Persona | str
    supporting_evidence: list[dict[str, Any]] | dict[str, Any]


class ActState(TypedDict, total=False):
    # ---- input (supplied by the caller / parent graph) ----
    decision: ReasonDecision
    action_type: ActionType
    org_id: str
    persona: str
    scope: str
    severity: Severity
    title: str | None
    related_entity_type: str | None
    related_entity_id: int | None
    source_signal: dict[str, Any] | None
    # LangGraph's own thread_id (config.configurable.thread_id) is preferred
    # for idempotency keys; this field is an explicit override/fallback for
    # callers that invoke nodes directly without a RunnableConfig (tests).
    thread_id: str | None

    # html_report_generator input
    report_items: list[ReasonDecision]
    period_label: str
    report_type: str
    period_start: Any
    period_end: Any
    use_llm_narrative: bool

    # communication_drafter input
    audience: Literal["driver", "vendor", "leadership"] | str
    audience_language_pref: str | None

    # ---- outputs ----
    notification_id: int | None
    notification_status: str | None
    report_id: int | None
    report_storage_ref: str | None
    report_html: str | None
    communication_draft: str | None
    communication_channel_note: str | None
    approval: dict[str, Any] | None
    dispatch_status: str | None
