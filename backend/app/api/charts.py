"""GET /api/charts/* -- lightweight, LLM-free aggregation endpoints backing
the Highcharts panels on each persona dashboard (frontend/src/charts/).

Same cost discipline as app/api/dashboard.py: every route below is one (or
two, for the vendor scorecard) grouped SQL query via app/services/chart_data.py,
no LLM call on the request path -- this data loads on every dashboard view.

`since`/`until` (ISO date strings, both optional) let the frontend's sliding
date-range picker override each chart's default "last N days back from the
data's own most recent activity" window with an exact, user-picked range --
see chart_data.py's per-function docstrings for why the default alone isn't
enough (a live-replay data tail can make "last N days" land in an empty gap).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import default_org_id
from app.api.schemas import ChartSeriesData, PieChartData, VendorScorecardData
from app.graph.act.db import get_engine
from app.services import chart_data

router = APIRouter(prefix="/charts", tags=["charts"])


@router.get("/ota-trend", response_model=ChartSeriesData)
def get_ota_trend(
    days: int = 45, since: date | None = Query(default=None), until: date | None = Query(default=None)
) -> ChartSeriesData:
    return ChartSeriesData(
        **chart_data.ota_trend(get_engine(), default_org_id(), days=days, since=since, until=until)
    )


@router.get("/delay-reasons", response_model=ChartSeriesData)
def get_delay_reasons(
    days: int = 90, since: date | None = Query(default=None), until: date | None = Query(default=None)
) -> ChartSeriesData:
    return ChartSeriesData(
        **chart_data.delay_reason_breakdown(get_engine(), default_org_id(), days=days, since=since, until=until)
    )


@router.get("/no-show-trend", response_model=ChartSeriesData)
def get_no_show_trend(
    days: int = 45, since: date | None = Query(default=None), until: date | None = Query(default=None)
) -> ChartSeriesData:
    return ChartSeriesData(
        **chart_data.no_show_trend(get_engine(), default_org_id(), days=days, since=since, until=until)
    )


@router.get("/absence-split", response_model=PieChartData)
def get_absence_split(
    days: int = 90, since: date | None = Query(default=None), until: date | None = Query(default=None)
) -> PieChartData:
    return PieChartData(
        **chart_data.absence_split(get_engine(), default_org_id(), days=days, since=since, until=until)
    )


@router.get("/billing-discrepancy", response_model=ChartSeriesData)
def get_billing_discrepancy(
    months: int = 6, since: date | None = Query(default=None), until: date | None = Query(default=None)
) -> ChartSeriesData:
    return ChartSeriesData(
        **chart_data.billing_discrepancy(get_engine(), default_org_id(), months=months, since=since, until=until)
    )


@router.get("/emissions-by-fuel", response_model=ChartSeriesData)
def get_emissions_by_fuel(
    days: int = 90, since: date | None = Query(default=None), until: date | None = Query(default=None)
) -> ChartSeriesData:
    return ChartSeriesData(
        **chart_data.emissions_by_fuel(get_engine(), default_org_id(), days=days, since=since, until=until)
    )


@router.get("/vendor-scorecard", response_model=VendorScorecardData)
def get_vendor_scorecard(
    days: int = 90, since: date | None = Query(default=None), until: date | None = Query(default=None)
) -> VendorScorecardData:
    return VendorScorecardData(
        **chart_data.vendor_scorecard(get_engine(), default_org_id(), days=days, since=since, until=until)
    )


@router.get("/signal-gate-funnel", response_model=ChartSeriesData)
def get_signal_gate_funnel(days: int = 30) -> ChartSeriesData:
    return ChartSeriesData(**chart_data.signal_gate_funnel(get_engine(), default_org_id(), days=days))


@router.get("/llm-usage", response_model=ChartSeriesData)
def get_llm_usage(days: int = 14) -> ChartSeriesData:
    import os

    provider = os.environ.get("LLM_PROVIDER", "sarvam")
    redis_url = os.environ["REDIS_URL"]
    return ChartSeriesData(
        **chart_data.llm_call_volume(get_engine(), default_org_id(), provider=provider, redis_url=redis_url, days=days)
    )
