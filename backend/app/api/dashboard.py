"""GET /api/dashboard?persona=<id> (plan §11)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import default_org_id
from app.api.schemas import MetricCardData, PersonaId
from app.services.dashboard_cards import build_dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=list[MetricCardData])
def get_dashboard(persona: PersonaId) -> list[MetricCardData]:
    cards = build_dashboard(default_org_id(), persona)
    return [MetricCardData(**c) for c in cards]
