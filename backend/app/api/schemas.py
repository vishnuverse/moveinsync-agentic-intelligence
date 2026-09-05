"""Pydantic response/request models mirroring frontend/src/api/types.ts
field-for-field (same snake_case names on both sides -- no aliasing needed).
This is the API/boundary layer, so Pydantic here is intentional (plan §4:
"TypedDict inside the graph, Pydantic only at API boundaries").
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator

CHAT_MESSAGE_MAX_LEN = 2000

PersonaId = Literal["transport_manager", "line_manager", "transport_head"]
MetricSeverity = Literal["good", "warning", "critical", "neutral"]
MetricTrend = Literal["up", "down", "flat"]
NotificationSeverity = Literal["info", "warning", "critical"]
NotificationStatus = Literal["open", "acked", "needs-intervention"]
TraceStepType = Literal[
    "signal_detected", "gate_decision", "sql_generated", "sql_executed", "context_built", "decision"
]
ActivityTrigger = Literal["schedule", "event"]


class Role(BaseModel):
    id: PersonaId
    name: str
    description: str


class MetricCardData(BaseModel):
    id: str
    label: str
    value: str
    trend: MetricTrend
    severity: MetricSeverity
    context_note: str
    thread_id: str


class NotificationItem(BaseModel):
    id: str
    severity: NotificationSeverity
    message: str
    status: NotificationStatus
    thread_id: str
    created_at: str
    is_false_positive: bool = False


class MarkFalsePositiveRequest(BaseModel):
    note: Optional[str] = None


class NotificationListResponse(BaseModel):
    # Paginated wrapper (plan API contract): `items` are the current page,
    # `total` is the count of ALL matching rows for this persona/org ignoring
    # limit/offset -- so the frontend can render "showing 25 of 137".
    items: list[NotificationItem]
    total: int


class ResumeDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    edited_text: Optional[str] = None


class ResumeDecisionResponse(BaseModel):
    id: str
    status: NotificationStatus
    resolved_at: str


class TraceStep(BaseModel):
    step: TraceStepType
    label: str
    detail: str
    sql: Optional[str] = None
    retry_count: Optional[int] = None
    timestamp: str
    # plan SP-B §9a: populated only on the "decision" step, when the
    # decision carries a recommendation -- lets the frontend render a
    # visually distinct "Recommended Action" block instead of re-parsing it
    # out of `detail`.
    recommendation: Optional[str] = None


class ReportMeta(BaseModel):
    id: str
    title: str
    period: str
    generated_at: str
    preview_url: str


class ReportGenerateRequest(BaseModel):
    persona: PersonaId
    # Optional: when omitted the route picks the persona's default report_type
    # (transport_manager -> daily_digest, line_manager -> weekly_digest,
    # transport_head -> monthly_leadership).
    report_type: Optional[str] = None


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "agent"]
    text: str
    thread_id: Optional[str] = None
    created_at: str


class ChatRequest(BaseModel):
    persona: PersonaId
    message: str
    # Optional so the primary "create a thread, then post into it" flow and
    # the backward-compatible "post with no thread yet" flow (the endpoint
    # creates one implicitly) share one request shape -- see app/api/chat.py.
    thread_id: Optional[str] = None

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        # Bare-minimum input guardrail (plan brief's "reject empty/
        # whitespace-only, enforce a max length with a clear error, not a
        # silent truncation or a confusing LLM failure"): fail loud here,
        # at the API boundary, well before this string reaches an LLM call
        # or the SQL agent.
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be empty")
        if len(stripped) > CHAT_MESSAGE_MAX_LEN:
            raise ValueError(f"message must be {CHAT_MESSAGE_MAX_LEN} characters or fewer")
        return value


class ChatResponse(BaseModel):
    message: ChatMessage


class ChatThread(BaseModel):
    id: str
    persona: PersonaId
    title: str
    scope_entity_type: Optional[str] = None
    scope_entity_id: Optional[str] = None
    created_at: str
    updated_at: str


class ChatThreadCreateRequest(BaseModel):
    persona: PersonaId
    scope_entity_type: Optional[str] = None
    scope_entity_id: Optional[str] = None


class ChatThreadRenameRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be empty")
        if len(stripped) > 200:
            raise ValueError("title must be 200 characters or fewer")
        return stripped


class ScopeOption(BaseModel):
    type: str
    id: str
    label: str


class ActivityEntry(BaseModel):
    id: str
    persona: PersonaId
    action: str
    timestamp: str
    triggered_by: ActivityTrigger


class ActivityListResponse(BaseModel):
    # Paginated wrapper for GET /api/activity (same contract as
    # NotificationListResponse: `total` counts ALL rows, ignoring limit/offset).
    items: list[ActivityEntry]
    total: int


class DataCoverage(BaseModel):
    # GET /api/data-coverage: the span and volume of the underlying `trip`
    # data, so the UI can anchor "as of <date>" copy to the data instead of
    # wall-clock now (the dataset only spans a fixed historical window).
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    trip_count: int
    # `dense_end_date`: the most recent date with substantial trip volume --
    # distinct from `end_date` (the literal MAX(trip_date), which can be a
    # sparse, disconnected live-replay tail day). The date-range picker uses
    # this, not `end_date`, as the default window's end so first-load always
    # shows meaningful data; `start_date`/`end_date` remain the picker's
    # outer slider bounds, so a viewer can still manually reach the live tail.
    dense_end_date: Optional[str] = None


class ErrorBody(BaseModel):
    detail: Any


# -- chart endpoints (app/api/charts.py) -- shaped to feed straight into a
# Highcharts series config on the frontend (frontend/src/charts/), field for
# field with frontend/src/api/types.ts's ChartSeries* interfaces.
class ChartSeries(BaseModel):
    name: str
    data: list[float]


class ChartSeriesData(BaseModel):
    categories: list[str]
    series: list[ChartSeries]


class PieSlice(BaseModel):
    name: str
    y: float


class PieSeries(BaseModel):
    name: str
    data: list[PieSlice]


class PieChartData(BaseModel):
    series: list[PieSeries]


class VendorScorecardEntry(BaseModel):
    vendor: str
    sla_pct: float
    cost_per_km: float
    incident_count: int
    sla_trend: list[float]


class VendorScorecardData(BaseModel):
    vendors: list[VendorScorecardEntry]


# ---------------------------------------------------------------------------
# SP-B Settings page (plan §4): GET/PUT /api/settings/rules, GET /api/settings/usage
# ---------------------------------------------------------------------------

GateMode = Literal["auto", "force_suppress", "force_rule_only", "force_escalate"]
NotificationCadence = Literal["immediate", "hourly", "every_2_hours", "daily", "weekly"]


class SignalRuleParams(BaseModel):
    signal_type: str
    params: dict[str, float | int | str]
    gate_mode: GateMode
    notification_cadence: NotificationCadence
    updated_at: str
    updated_by: Optional[str] = None


class GateSettingsModel(BaseModel):
    recurrence_window_hours: int
    recurrence_suppress_after: int
    max_consecutive_suppressions: int
    rule_only_margin_ratio: float
    max_fp_rate_for_rule_only: float
    min_confidence_for_rule_only: float
    max_healthy_suppression_rate: float
    escalation_after_hours_critical: float
    escalation_after_hours_high: float
    escalation_after_hours_medium: float
    updated_at: str
    updated_by: Optional[str] = None


class RulesResponse(BaseModel):
    signal_rules: list[SignalRuleParams]
    gate_settings: GateSettingsModel


class RulesUpdateRequest(BaseModel):
    # Partial by design: only the signal_types/settings actually supplied are
    # written -- everything else in alert_rules/gate_settings is left as-is.
    signal_rules: Optional[list[SignalRuleParams]] = None
    gate_settings: Optional[GateSettingsModel] = None
    updated_by: Optional[str] = None


class FalsePositiveRateEntry(BaseModel):
    signal_type: str
    dispatched_count: int
    false_positive_count: int
    false_positive_rate_pct: float


class AggregatedInsightsResponse(BaseModel):
    # Plan SP-B §9b: per-persona moving-average KPI rollups. Fields are
    # populated per-persona domain scope (§A's table) -- a field a given
    # persona's view doesn't use is simply omitted (None), not zeroed, so
    # the frontend can tell "not applicable to this persona" from "value is
    # actually zero."
    no_shows_today: Optional[int] = None
    no_shows_this_week: Optional[int] = None
    no_shows_trend_pct: Optional[float] = None
    no_shows_trend_direction: Optional[MetricTrend] = None
    flagged_driver_count: Optional[int] = None
    total_drivers_evaluated: Optional[int] = None


class CostOptimizationOpportunity(BaseModel):
    vendor_name: str
    cv_pct: float
    recommendation: str


class CostOptimizationResponse(BaseModel):
    window_start: str
    window_end: str
    window_total_inr: float
    baseline_avg_per_day_inr: float
    trend_pct: Optional[float] = None
    trend_direction: MetricTrend
    opportunities: list[CostOptimizationOpportunity]


class UsageStatsResponse(BaseModel):
    llm_calls_today: int
    llm_daily_limit: int
    gate_counts_today: dict[str, int]  # {"suppress": n, "rule_only": n, "escalate": n}
    false_positive_rate_by_signal_type: list[FalsePositiveRateEntry]
    # False-negative safeguard (plan §1): a human-visible, non-automatic
    # nudge when a signal_type's suppress rate looks too high to be healthy.
    suppression_warnings: list[str]
