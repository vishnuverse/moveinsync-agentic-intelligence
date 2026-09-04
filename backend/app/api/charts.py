"""GET /api/charts/* -- lightweight, LLM-free aggregation endpoints backing
the Highcharts panels on each persona dashboard (frontend/src/charts/).

Same cost discipline as app/api/dashboard.py: every route below is one (or
two, for the vendor scorecard) grouped SQL query via app/services/chart_data.py,
no LLM call on the request path -- this data loads on every dashboard view.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import default_org_id
from app.api.schemas import ChartSeriesData, PieChartData, VendorScorecardData
from app.graph.act.db import get_engine
from app.services import chart_data

router = APIRouter(prefix="/charts", tags=["charts"])


@router.get("/ota-trend", response_model=ChartSeriesData)
def get_ota_trend(days: int = 45) -> ChartSeriesData:
    return ChartSeriesData(**chart_data.ota_trend(get_engine(), default_org_id(), days=days))


@router.get("/delay-reasons", response_model=ChartSeriesData)
def get_delay_reasons(days: int = 90) -> ChartSeriesData:
    return ChartSeriesData(**chart_data.delay_reason_breakdown(get_engine(), default_org_id(), days=days))


@router.get("/no-show-trend", response_model=ChartSeriesData)
def get_no_show_trend(days: int = 45) -> ChartSeriesData:
    return ChartSeriesData(**chart_data.no_show_trend(get_engine(), default_org_id(), days=days))


@router.get("/absence-split", response_model=PieChartData)
def get_absence_split(days: int = 90) -> PieChartData:
    return PieChartData(**chart_data.absence_split(get_engine(), default_org_id(), days=days))


@router.get("/billing-discrepancy", response_model=ChartSeriesData)
def get_billing_discrepancy(months: int = 6) -> ChartSeriesData:
    return ChartSeriesData(**chart_data.billing_discrepancy(get_engine(), default_org_id(), months=months))


@router.get("/emissions-by-fuel", response_model=ChartSeriesData)
def get_emissions_by_fuel(days: int = 90) -> ChartSeriesData:
    return ChartSeriesData(**chart_data.emissions_by_fuel(get_engine(), default_org_id(), days=days))


@router.get("/vendor-scorecard", response_model=VendorScorecardData)
def get_vendor_scorecard(days: int = 90) -> VendorScorecardData:
    return VendorScorecardData(**chart_data.vendor_scorecard(get_engine(), default_org_id(), days=days))
