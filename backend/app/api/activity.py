"""GET /api/activity -- system-wide, not persona-filtered (plan §10/§11)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import default_org_id
from app.api.schemas import ActivityEntry, ActivityListResponse
from app.services.activity_log import count_activity, list_activity

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=ActivityListResponse)
def get_activity(
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ActivityListResponse:
    org_id = default_org_id()
    rows = list_activity(org_id, limit=limit, offset=offset)
    total = count_activity(org_id)
    items = [
        ActivityEntry(
            id=str(row["id"]),
            persona=row["persona"],
            action=row["action"],
            timestamp=row["created_at"].isoformat(),
            triggered_by=row["triggered_by"],
        )
        for row in rows
    ]
    return ActivityListResponse(items=items, total=total)
