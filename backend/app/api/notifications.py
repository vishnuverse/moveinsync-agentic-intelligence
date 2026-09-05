"""GET /api/notifications?persona=<id> and POST /api/notifications/{id}/resume
(plan §11, act-subgraph HITL flow).

resume: look up the notification's thread_id (stored on the row per
app/graph/act/db.upsert_notification), then resume the SAME top-level graph
(app.api.deps.get_top_graph -- see its docstring for why not a standalone
act subgraph) with Command(resume={"approved": bool, "approver": str,
"comment": str}), mapping the frontend's decision:"approve"|"reject" to
approved:True/False per the act-subgraph agent's documented resume shape
(app/graph/act/subgraph.py's module docstring).

Idempotency: if the notification is no longer in needs_intervention (already
resumed once, by this endpoint or a prior process), this returns the
already-settled status WITHOUT calling Command(resume=...) again -- resuming
a thread that isn't currently paused has no well-defined LangGraph behavior,
and the plan's own verification step explicitly requires "no duplicate side
effects" on a second resume call.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from langgraph.types import Command

from app.api.deps import default_org_id, get_top_graph
from app.api.schemas import (
    MarkFalsePositiveRequest,
    NotificationItem,
    NotificationListResponse,
    PersonaId,
    ResumeDecisionRequest,
    ResumeDecisionResponse,
)
from app.graph.act.db import get_engine, get_notification, mark_false_positive
from app.services.notifications_query import count_notifications, list_notifications, to_frontend_status

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationListResponse)
def get_notifications(
    persona: PersonaId,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> NotificationListResponse:
    org_id = default_org_id()
    rows = list_notifications(org_id, persona, limit=limit, offset=offset)
    total = count_notifications(org_id, persona)
    items = [
        NotificationItem(
            id=str(row["id"]),
            severity=row["severity"],
            message=row["message"],
            status=to_frontend_status(row["status"]),
            thread_id=row["thread_id"] or "",
            created_at=row["created_at"].isoformat(),
            is_false_positive=bool(row.get("is_false_positive")),
        )
        for row in rows
    ]
    return NotificationListResponse(items=items, total=total)


@router.post("/notifications/{notification_id}/resume", response_model=ResumeDecisionResponse)
def resume_notification(notification_id: str, body: ResumeDecisionRequest) -> ResumeDecisionResponse:
    try:
        nid = int(notification_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"notification '{notification_id}' not found")

    engine = get_engine()
    row = get_notification(engine, nid)
    if row is None:
        raise HTTPException(status_code=404, detail=f"notification '{notification_id}' not found")

    thread_id = row.get("thread_id")
    current_status = row.get("status")

    if current_status != "needs_intervention" or not thread_id:
        # Already settled (or never had a resumable thread) -- return the
        # current state idempotently instead of re-invoking Command(resume=...).
        return ResumeDecisionResponse(
            id=notification_id,
            status=to_frontend_status(current_status or "open"),
            resolved_at=(row.get("updated_at") or datetime.now(timezone.utc)).isoformat(),
        )

    approved = body.decision == "approve"
    top_graph = get_top_graph()
    config = {"configurable": {"thread_id": thread_id}}
    resume_payload = {
        "approved": approved,
        "approver": f"{row.get('persona', 'dashboard')}-ui",
        "comment": body.edited_text or "",
    }
    try:
        result = top_graph.invoke(Command(resume=resume_payload), config=config)
    except Exception as exc:  # noqa: BLE001 - surface as a clean 502, don't leak a stack trace to the UI
        raise HTTPException(status_code=502, detail=f"failed to resume thread '{thread_id}': {exc}") from exc

    resolved_at = datetime.now(timezone.utc)
    final_row = get_notification(engine, nid) or row
    return ResumeDecisionResponse(
        id=notification_id,
        status=to_frontend_status(final_row.get("status") or ("acked" if approved else "resolved")),
        resolved_at=(final_row.get("updated_at") or resolved_at).isoformat(),
    )


@router.post("/notifications/{notification_id}/false-positive", response_model=ResumeDecisionResponse)
def mark_notification_false_positive(
    notification_id: str, body: MarkFalsePositiveRequest
) -> ResumeDecisionResponse:
    """Plan SP-B §7: a human marking a dispatched alert as wrong. Deliberately
    small -- a feedback *signal* for a human tuning thresholds in Settings
    (surfaced via GET /api/settings/usage's false_positive_rate_by_signal_type),
    not an automatic training loop."""
    try:
        nid = int(notification_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"notification '{notification_id}' not found")

    engine = get_engine()
    row = get_notification(engine, nid)
    if row is None:
        raise HTTPException(status_code=404, detail=f"notification '{notification_id}' not found")

    mark_false_positive(engine, notification_id=nid, note=body.note)

    final_row = get_notification(engine, nid) or row
    return ResumeDecisionResponse(
        id=notification_id,
        status=to_frontend_status(final_row.get("status") or "resolved"),
        resolved_at=(final_row.get("updated_at") or datetime.now(timezone.utc)).isoformat(),
    )
