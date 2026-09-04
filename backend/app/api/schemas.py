"""Pydantic response/request models mirroring frontend/src/api/types.ts
field-for-field (same snake_case names on both sides -- no aliasing needed).
This is the API/boundary layer, so Pydantic here is intentional (plan §4:
"TypedDict inside the graph, Pydantic only at API boundaries").
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

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


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "agent"]
    text: str
    thread_id: Optional[str] = None
    created_at: str


class ChatRequest(BaseModel):
    persona: PersonaId
    message: str


class ChatResponse(BaseModel):
    message: ChatMessage


class ActivityEntry(BaseModel):
    id: str
    persona: PersonaId
    action: str
    timestamp: str
    triggered_by: ActivityTrigger


class ErrorBody(BaseModel):
    detail: Any
