"""Builds GET /api/dashboard's MetricCardData[] from already-computed,
already contract-carrying state -- the most recent `agent_notifications` rows
for a persona, enriched (cheap Postgres reads via the shared checkpointer,
NO new LLM call) with the reasoning state already persisted for that row's
thread_id. This is the design call the build brief asked for explicitly:
computing fresh LLM-reasoned metrics on every dashboard load would be slow
and burn LLM budget for nothing new -- the checkpointer already has the
answer, the same "no new instrumentation, just formatting what's already
there" philosophy trace_builder.py uses for the Trace Drawer.

`_ensure_seeded` is the one place this module calls run_pipeline() -- and
only when the org has literally zero notifications yet (a fresh DB), exactly
once per process, guarded by a lock so concurrent first-load requests don't
all trigger a redundant pipeline run.
"""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import text

from app.api.deps import get_top_graph
from app.contracts import get_contract
from app.graph.act.db import get_engine
from app.services.notifications_query import list_notifications

_SEED_LOCK = threading.Lock()
_SEEDED_ORGS: set[str] = set()

# Mirrors app/graph/reason/impact_context.py's _SIGNAL_METRIC_SPEC (private to
# that module, deliberately not imported so this presentation-layer concern
# doesn't reach into a private symbol of a restricted module) -- kept in sync
# manually; it's a tiny, stable, documented mapping.
_SIGNAL_METRIC_SPEC: dict[str, tuple[str, str, str]] = {
    "delay_breach": ("avg_delay_minutes", "min", "avg delay"),
    "cost_divergence": ("observed_cost_per_km", "INR/km", "observed cost/km"),
    "emissions_over_target": ("avg_co2_per_passenger_km", "gCO2/pax-km", "avg emissions"),
    "attendance_correlated_with_transport": ("correlation_ratio", "ratio", "transport-correlation ratio"),
    "attendance_unrelated_late": ("correlation_ratio", "ratio", "transport-correlation ratio"),
}

_SHORT_LABELS: dict[str, str] = {
    "delay_breach": "On-Time Delay",
    "cost_divergence": "Cost Divergence",
    "emissions_over_target": "Emissions vs Target",
    "attendance_correlated_with_transport": "Attendance-Transport Link",
    "attendance_unrelated_late": "Attendance (Unrelated Lateness)",
    "incident": "Safety Incident",
    "data_quality_issue": "Data Quality Issue",
}


def _ensure_seeded(org_id: str) -> None:
    """BUGFIX (found live against the real dataset): this used to call
    `run_pipeline(org_id)` synchronously, inline in the GET /api/dashboard
    request handler. Against the synthetic seed (small, fast) that's a
    sub-second no-op; against the real dataset (hundreds of real signals,
    each a real LLM call over the network) it blocks the request for many
    minutes -- long past Caddy's proxy timeout, surfacing as a 502 to the
    browser -- AND, since the scheduler's own first tick already fires
    immediately on container start (see app/schedulers/interval.py), a page
    load landing while that tick is still running would kick off a SECOND,
    fully redundant full pipeline sweep concurrently, double-spending real
    LLM budget on the same signals (the thread_id dedup check in
    supervisor.run_pipeline is a TOCTOU race across two concurrent sweeps,
    not a lock).

    Fire-and-forget in a background thread instead: the request returns
    immediately (empty cards on a genuinely fresh DB, same as before the
    scheduler's first tick completes -- honest, not a regression), and a
    lock still guards against launching more than one background seed pass
    per org per process."""
    if org_id in _SEEDED_ORGS:
        return
    with _SEED_LOCK:
        if org_id in _SEEDED_ORGS:
            return
        _SEEDED_ORGS.add(org_id)
        contract = get_contract().entity("notification")
        engine = get_engine()
        with engine.begin() as conn:
            count = conn.execute(
                text(f"SELECT COUNT(*) FROM {contract.table} WHERE {contract.column('org_id')} = :org_id"),
                {"org_id": org_id},
            ).scalar()
        if not count:
            from app.graph.supervisor import run_pipeline

            threading.Thread(target=run_pipeline, args=(org_id,), daemon=True).start()


def _fetch_thread_values(thread_id: str) -> dict[str, Any]:
    try:
        snapshot = get_top_graph().get_state({"configurable": {"thread_id": thread_id}})
        return dict(snapshot.values or {})
    except Exception:  # noqa: BLE001 - a missing/corrupt checkpoint degrades to a plain notification card
        return {}


def _metric_from_signal(signal: dict[str, Any]) -> dict[str, Any]:
    context = signal.get("context") or {}
    entity_label = (
        context.get("route_code")
        or context.get("vendor_name")
        or context.get("employee_name")
        or f"{signal.get('entity_type')} {signal.get('entity_id')}"
    )
    spec = _SIGNAL_METRIC_SPEC.get(signal.get("signal_type"))
    if spec is None:
        return {
            "label": _SHORT_LABELS.get(signal.get("signal_type"), entity_label),
            "value": None,
            "unit": "",
        }
    key, unit, headline = spec
    raw_metric = signal.get("raw_metric") or {}
    value = raw_metric.get(key)
    return {
        "label": f"{entity_label} {headline}",
        "value": float(value) if value is not None else None,
        "unit": unit,
    }


def _format_value(value: float | None, unit: str, fallback: str) -> str:
    if value is None:
        return fallback or "N/A"
    if unit == "INR/km":
        return f"₹{value:.2f}"
    if unit == "gCO2/pax-km":
        return f"{value:.0f} gCO2/pkm"
    if unit == "min":
        return f"{value:.0f} min"
    if unit == "ratio":
        return f"{value * 100:.0f}%"
    text_value = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text_value} {unit}".strip()


def _map_severity(db_severity: str, severity_band: str | None) -> str:
    if db_severity == "critical":
        return "critical"
    if db_severity == "warning":
        return "warning"
    if severity_band == "low":
        return "good"
    return "neutral"


def _truncate(value: str, max_len: int = 48) -> str:
    return value if len(value) <= max_len else value[: max_len - 1].rstrip() + "…"


def _card_from_notification(row: dict[str, Any]) -> dict[str, Any]:
    thread_id = row.get("thread_id") or ""
    values = _fetch_thread_values(thread_id) if thread_id else {}
    signal = values.get("signal")
    decision = values.get("decision") or {}
    impact_context = values.get("impact_context") or {}

    metric = _metric_from_signal(signal) if signal else None
    label = _truncate((metric or {}).get("label") or row["title"])
    value = (metric or {}).get("value") if metric else None
    unit = (metric or {}).get("unit", "") if metric else ""
    value_str = _format_value(value, unit, fallback=row["severity"].title())

    trend = impact_context.get("trend_direction", "flat")
    if trend not in ("up", "down", "flat"):
        trend = "flat"

    severity = _map_severity(row["severity"], impact_context.get("severity_band"))
    context_note = decision.get("summary") or impact_context.get("business_impact") or row["message"]

    return {
        "id": str(row["id"]),
        "label": label,
        "value": value_str,
        "trend": trend,
        "severity": severity,
        "context_note": context_note,
        "thread_id": thread_id,
    }


def build_dashboard(org_id: str, persona: str, *, limit: int = 5) -> list[dict[str, Any]]:
    # list_notifications() already excludes chat-originated rows (scope="chat")
    # -- see its own docstring for the incident this guards against.
    _ensure_seeded(org_id)
    rows = list_notifications(org_id, persona, limit=limit)
    return [_card_from_notification(row) for row in rows]
