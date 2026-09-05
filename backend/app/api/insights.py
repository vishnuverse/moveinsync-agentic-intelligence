"""GET /api/insights/{persona}, GET /api/insights/cost-optimization (plan
SP-B §9b) -- per-persona moving-average KPI rollups, and the
cost-optimization-for-a-window capability. Pair with GET /api/data-coverage
(app/api/meta.py) for the min/max date bounds a date-range picker needs --
the real dataset is a fixed historical window, not a wall-clock-relative one,
so a picker should be bounded by the data's own actual range.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import default_org_id
from app.api.schemas import AggregatedInsightsResponse, CostOptimizationResponse, PersonaId
from app.graph.act.db import get_engine
from app.services import aggregated_insights as ai

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/{persona}", response_model=AggregatedInsightsResponse)
def get_persona_insights(persona: PersonaId) -> AggregatedInsightsResponse:
    org_id = default_org_id()
    engine = get_engine()

    if persona == "line_manager":
        # Org-wide for now -- a real per-team scope needs a team_id the
        # frontend doesn't send yet (no team-selection UI exists); see
        # aggregated_insights.no_shows_today_and_week's own `team_id` param
        # for the per-team path once that UI exists.
        no_shows = ai.no_shows_today_and_week(engine, org_id)
        return AggregatedInsightsResponse(
            no_shows_today=no_shows["today"],
            no_shows_this_week=no_shows["this_week"],
            no_shows_trend_pct=no_shows["trend_pct"],
            no_shows_trend_direction=no_shows["trend_direction"],
        )

    if persona == "transport_manager":
        no_shows = ai.no_shows_today_and_week(engine, org_id)
        flagged = ai.flagged_drivers(engine, org_id)
        return AggregatedInsightsResponse(
            no_shows_today=no_shows["today"],
            no_shows_this_week=no_shows["this_week"],
            no_shows_trend_pct=no_shows["trend_pct"],
            no_shows_trend_direction=no_shows["trend_direction"],
            flagged_driver_count=flagged["flagged_driver_count"],
            total_drivers_evaluated=flagged["total_drivers_evaluated"],
        )

    # transport_head: org-wide no-show trend (route-optimization opportunities
    # and cost live under /insights/cost-optimization, since those need an
    # explicit date-window control the frontend renders separately).
    no_shows = ai.org_wide_no_show_trend(engine, org_id)
    return AggregatedInsightsResponse(
        no_shows_today=no_shows["today"],
        no_shows_this_week=no_shows["this_week"],
        no_shows_trend_pct=no_shows["trend_pct"],
        no_shows_trend_direction=no_shows["trend_direction"],
    )


@router.get("/cost-optimization/window", response_model=CostOptimizationResponse)
def get_cost_optimization(
    since: date | None = Query(default=None, description="Window start (defaults to the data's most recent date)"),
    until: date | None = Query(default=None, description="Window end (defaults to the data's most recent date)"),
) -> CostOptimizationResponse:
    org_id = default_org_id()
    engine = get_engine()
    result = ai.cost_optimization_outlook(engine, org_id, since=since, until=until)
    return CostOptimizationResponse(**result)
