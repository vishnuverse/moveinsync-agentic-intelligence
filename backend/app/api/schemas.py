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
TraceStepType = Literal["signal_detected", "sql_generated", "sql_executed", "context_built", "decision"]
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
