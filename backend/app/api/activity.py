"""GET /api/activity -- system-wide, not persona-filtered (plan §10/§11)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import default_org_id
from app.api.schemas import ActivityEntry
from app.services.activity_log import list_activity

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=list[ActivityEntry])
def get_activity() -> list[ActivityEntry]:
    rows = list_activity(default_org_id(), limit=100)
    return [
        ActivityEntry(
            id=str(row["id"]),
            persona=row["persona"],
            action=row["action"],
            timestamp=row["created_at"].isoformat(),
            triggered_by=row["triggered_by"],
        )
        for row in rows
    ]
