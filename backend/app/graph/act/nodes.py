"""Core act-subgraph nodes: notification_dispatch, interrupt_gate,
send_dispatch, and the two routing functions (plan §2 Act paragraph, §4 Act
subgraph).

Node/edge shape:

    route_by_action_type (conditional entry)
        -> notification_dispatch | html_report_generator | communication_drafter
        -> route_after_action (conditional)
              needs_human_signoff=True  -> interrupt_gate -> send_dispatch
              needs_human_signoff=False -> send_dispatch directly

`interrupt_gate` is the single place -- regardless of action_type -- that
creates/updates the `agent_notifications` row with status='needs_intervention'
and calls `interrupt()`: this is what the plan means by "the interrupted
state IS the pending-approval inbox entry" (§4). `notification_dispatch`
handles the *other* case: a plain proactive alert/notification
(action_type="notification") that does not need sign-off, written with
status='open' directly.

Idempotency (plan §4's "Resume responsiveness" note: nodes must be safe to
re-run if LangGraph replays them on resume): every DB write here goes through
`db.upsert_notification`, keyed on `thread_id`, so re-executing a node updates
the same row instead of inserting a duplicate. `interrupt_gate` additionally
only publishes to Redis on first creation (`result["created"]`) so a
resume-replay of the node (which re-runs from the top up to the `interrupt()`
call) does not re-publish a duplicate "needs_intervention" event.
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.contracts import get_contract

from .db import get_engine, mark_notification_status, upsert_notification
from .redis_publish import notification_channel, publish_event
from .state import ActState

_VALID_ACTION_TYPES = ("notification", "report", "communication")

# NOTE on `config: Optional[RunnableConfig] = None` below (not `RunnableConfig
# | None`): with `from __future__ import annotations`, annotations are
# strings at runtime, and LangGraph's node-signature introspection
# (langgraph._internal._runnable, KWARGS_CONFIG_KEYS) only recognizes the
# exact strings "RunnableConfig" / "Optional[RunnableConfig]" when deciding
# whether to auto-inject the live RunnableConfig (which carries
# configurable.thread_id) into a node call. "RunnableConfig | None" is NOT
# recognized -- LangGraph silently skips injection and the node's `config`
# stays at its default (None), which silently broke thread_id-based
# idempotency here (confirmed live during build verification: every write
# landed with thread_id=NULL until this was fixed). Keep this exact spelling.


def _resolve_thread_id(state: ActState, config: Optional[RunnableConfig]) -> str | None:
    explicit = state.get("thread_id")
    if explicit:
        return explicit
    if config:
        return (config.get("configurable") or {}).get("thread_id")
    return None


def _default_title(decision: dict[str, Any]) -> str:
    summary = decision.get("summary") or "Automated notification"
    return summary if len(summary) <= 120 else summary[:117] + "..."


def _infer_severity(state: ActState, decision: dict[str, Any]) -> str:
    explicit = state.get("severity")
    if explicit:
        return explicit
    confidence = decision.get("confidence")
    if decision.get("needs_human_signoff"):
        return "critical"
    if isinstance(confidence, (int, float)) and confidence < 0.6:
        return "warning"
    return "info"


def route_by_action_type(state: ActState) -> str:
    """Conditional entry point: picks which action node handles this decision."""
    action_type = state.get("action_type", "notification")
    if action_type not in _VALID_ACTION_TYPES:
        raise ValueError(
            f"unknown act action_type: {action_type!r} -- expected one of {_VALID_ACTION_TYPES}"
        )
    return action_type


def route_after_action(state: ActState) -> str:
    """Conditional edge run after whichever action node executed: gates on
    ReasonDecision.needs_human_signoff, per plan §4's HITL requirement that
    NO send/escalate action fires before interrupt_gate when sign-off is
    needed."""
    decision = state.get("decision", {})
    return "interrupt_gate" if decision.get("needs_human_signoff") else "send_dispatch"


def notification_dispatch(state: ActState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    """Writes/updates the `agent_notifications` row for a plain proactive
    alert (action_type="notification") and publishes it to
    `notifications:{persona}` for a future WebSocket layer to push live.

    If this particular decision also needs sign-off, the row is written with
    status='needs_intervention' here (so it reads correctly even before
    interrupt_gate runs) -- interrupt_gate is still what performs the actual
    graph pause and re-confirms/updates the same row on resume.

    `scheduled_for` (plan SP-B §3): computed from `notification_cadence`
    (set upstream by supervisor.run_pipeline from the signal's configured
    alert_rules row). The row is always written immediately -- reasoning,
    the gate_decisions audit trail, and Trace Drawer history are never
    delayed -- only its *visibility* is: app.services.notifications_query's
    read-side filter hides it until `scheduled_for` passes, and the live
    WebSocket/SSE push below is skipped entirely for a deferred item (the
    whole point of a lower cadence tier is to not interrupt in real time;
    it simply appears once the inbox is next read after its boundary passes).
    """
    from datetime import datetime, timezone

    from app.rules import compute_scheduled_for

    decision = state.get("decision", {})
    org_id = state.get("org_id") or get_contract().default_org_id
    persona = state.get("persona") or decision.get("target_persona") or "transport_manager"
    scope = state.get("scope", "global")
    thread_id = _resolve_thread_id(state, config)
    needs_signoff = bool(decision.get("needs_human_signoff"))
    status = "needs_intervention" if needs_signoff else "open"
    severity = _infer_severity(state, decision)
    title = state.get("title") or _default_title(decision)
    message = decision.get("summary") or decision.get("recommendation") or "Automated notification"
    scheduled_for = compute_scheduled_for(state.get("notification_cadence"), datetime.now(timezone.utc))

    engine = get_engine()
    result = upsert_notification(
        engine,
        org_id=org_id,
        persona=persona,
        scope=scope,
        severity=severity,
        title=title,
        message=message,
        status=status,
        thread_id=thread_id,
        related_entity_type=state.get("related_entity_type"),
        related_entity_id=state.get("related_entity_id"),
        scheduled_for=scheduled_for,
    )

    if result["created"] and scheduled_for is None:
        publish_event(
            notification_channel(persona),
            {
                "kind": "notification",
                "notification_id": result["id"],
                "status": status,
                "severity": severity,
                "title": title,
                "persona": persona,
                "thread_id": thread_id,
            },
        )

    return {"notification_id": result["id"], "notification_status": status}


def interrupt_gate(state: ActState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    """The HITL gate. Only reached (per route_after_action) when
    decision.needs_human_signoff is True. Upserts the `agent_notifications`
    row to status='needs_intervention' -- idempotently, so replaying this
    node on resume updates rather than duplicates it -- then calls
    `interrupt()`, pausing the graph until a future FastAPI endpoint issues
    `Command(resume=...)` on the same thread_id.

    Resume payload shape expected: a dict `{"approved": bool, "approver":
    str, "comment": str | None}` (or a bare bool/truthy value, handled for
    convenience). See subgraph.py's module docstring for the exact call shape.
    """
    decision = state.get("decision", {})
    action_type = state.get("action_type", "notification")
    org_id = state.get("org_id") or get_contract().default_org_id
    persona = state.get("persona") or decision.get("target_persona") or "transport_manager"
    scope = state.get("scope", "global")
    thread_id = _resolve_thread_id(state, config)
    title = state.get("title") or _default_title(decision)
    message = decision.get("summary") or decision.get("recommendation") or "Action requires approval."

    engine = get_engine()
    result = upsert_notification(
        engine,
        org_id=org_id,
        persona=persona,
        scope=scope,
        severity="critical",
        title=title,
        message=message,
        status="needs_intervention",
        thread_id=thread_id,
        related_entity_type=state.get("related_entity_type"),
        related_entity_id=state.get("related_entity_id"),
    )

    if result["created"]:
        publish_event(
            notification_channel(persona),
            {
                "kind": "needs_intervention",
                "notification_id": result["id"],
                "action_type": action_type,
                "persona": persona,
                "thread_id": thread_id,
                "summary": decision.get("summary"),
                "recommendation": decision.get("recommendation"),
            },
        )

    approval = interrupt(
        {
            "type": "approval_request",
            "notification_id": result["id"],
            "action_type": action_type,
            "persona": persona,
            "decision": decision,
            "prepared": {
                "report_storage_ref": state.get("report_storage_ref"),
                "communication_draft": state.get("communication_draft"),
            },
        }
    )

    approval_dict = approval if isinstance(approval, dict) else {"approved": bool(approval)}
    return {"notification_id": result["id"], "approval": approval_dict}


def send_dispatch(state: ActState, config: Optional[RunnableConfig] = None) -> dict[str, Any]:
    """Executes the approved action after resume (or immediately, for
    decisions that never needed sign-off). Strictly downstream of
    interrupt_gate in the graph -- never invoked before an approval decision
    exists when one was required.

    No real external send integration exists yet (no email/SMS/Slack
    provider wired into this build) -- "send" here means: finalize the
    notification's status and publish a terminal event on
    `notifications:{persona}` for the WebSocket layer, which is what plan §4's
    "Resume responsiveness" note requires (push the result the moment resume
    happens, not on the scheduler's next poll). Wiring an actual outbound
    channel (email/SMS/Sarvam TTS call) is the natural next step and is noted
    in `dispatch_status` rather than silently assumed.
    """
    decision = state.get("decision", {})
    action_type = state.get("action_type", "notification")
    persona = state.get("persona") or decision.get("target_persona") or "transport_manager"
    notification_id = state.get("notification_id")
    approval = state.get("approval")

    if decision.get("needs_human_signoff"):
        approved = bool(approval.get("approved")) if isinstance(approval, dict) else bool(approval)
        if not approved:
            if notification_id is not None:
                mark_notification_status(get_engine(), notification_id=notification_id, status="resolved")
            publish_event(
                notification_channel(persona),
                {"kind": "rejected", "notification_id": notification_id, "action_type": action_type},
            )
            return {"dispatch_status": "rejected"}

        if notification_id is not None:
            mark_notification_status(get_engine(), notification_id=notification_id, status="acked")
        publish_event(
            notification_channel(persona),
            {"kind": "dispatched", "notification_id": notification_id, "action_type": action_type},
        )
        return {"dispatch_status": f"sent:{action_type}"}

    publish_event(
        notification_channel(persona),
        {"kind": "dispatched", "notification_id": notification_id, "action_type": action_type},
    )
    return {"dispatch_status": f"sent:{action_type}"}
