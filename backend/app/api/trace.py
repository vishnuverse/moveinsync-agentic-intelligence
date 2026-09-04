"""GET /api/threads/{thread_id}/trace (plan §10 Trace Drawer)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.schemas import TraceStep
from app.services.trace_builder import build_trace

router = APIRouter(tags=["trace"])


@router.get("/threads/{thread_id}/trace", response_model=list[TraceStep])
def get_trace(thread_id: str) -> list[TraceStep]:
    steps = build_trace(thread_id)
    if not steps:
        raise HTTPException(status_code=404, detail=f"no trace found for thread '{thread_id}'")
    return [TraceStep(**s) for s in steps]
