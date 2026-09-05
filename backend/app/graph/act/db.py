"""SQLAlchemy Core engine + contract-resolved read/write helpers for the act
subgraph's two output tables (plan §3: table/column names resolved through
`app.contracts.get_contract()`, never a literal "agent_notifications" string
in node logic).

`upsert_notification`/`upsert_report` are the idempotency primitive the plan's
"Resume responsiveness" note requires (plan §4): keyed on `thread_id`, so a
LangGraph node that re-executes on interrupt-resume updates the same row
instead of inserting a duplicate.
"""

from __future__ import annotations

import functools
import os
from typing import Any

from sqlalchemy import Engine, create_engine, text

from app.contracts import get_contract


def _normalize_pg_url(database_url: str) -> str:
    """Force the psycopg3 SQLAlchemy dialect -- same rewrite the sql_agent
    subgraph applies (backend/app/graph/reason/sql_agent/subgraph.py), kept
    duplicated here rather than imported so this module stays independently
    embeddable without reaching into a sibling subgraph's internals."""
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


@functools.lru_cache(maxsize=4)
def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.environ["DATABASE_URL"]
    return create_engine(_normalize_pg_url(url), pool_pre_ping=True)


def upsert_notification(
    engine: Engine,
    *,
    org_id: str,
    persona: str,
    scope: str,
    severity: str,
    title: str,
    message: str,
    status: str,
    thread_id: str | None,
    related_entity_type: str | None = None,
    related_entity_id: int | None = None,
    scheduled_for: Any = None,
) -> dict[str, Any]:
    """Insert or update the one `agent_notifications` row for this thread_id.

    Without a thread_id (no LangGraph config supplied it), idempotency isn't
    possible -- falls back to a plain insert, which is fine for direct/ad-hoc
    node calls that aren't part of a resumable graph run.

    `scheduled_for` (plan SP-B §3) is only ever set on INSERT, never touched
    on an UPDATE (a resume-replay of the same thread_id must not silently
    reschedule an already-decided visibility delay) -- `None` means
    "immediate," matching every notification written before this plan.
    """
    contract = get_contract().entity("notification")
    table = contract.table
    c = contract.column

    with engine.begin() as conn:
        existing = None
        if thread_id:
            existing = conn.execute(
                text(
                    f"SELECT {c('id')} AS id FROM {table} "
                    f"WHERE {c('org_id')} = :org_id AND {c('thread_id')} = :thread_id "
                    f"ORDER BY {c('id')} DESC LIMIT 1"
                ),
                {"org_id": org_id, "thread_id": thread_id},
            ).mappings().first()

        if existing:
            conn.execute(
                text(
                    f"UPDATE {table} SET {c('status')} = :status, {c('severity')} = :severity, "
                    f"{c('title')} = :title, {c('message')} = :message, "
                    f"{c('updated_at')} = now() WHERE {c('id')} = :id"
                ),
                {
                    "status": status,
                    "severity": severity,
                    "title": title,
                    "message": message,
                    "id": existing["id"],
                },
            )
            return {"id": existing["id"], "status": status, "created": False}

        row = conn.execute(
            text(
                f"INSERT INTO {table} "
                f"({c('org_id')}, {c('persona')}, {c('scope')}, {c('severity')}, {c('title')}, "
                f"{c('message')}, {c('related_entity_type')}, {c('related_entity_id')}, "
                f"{c('status')}, {c('thread_id')}, {c('scheduled_for')}) "
                f"VALUES (:org_id, :persona, :scope, :severity, :title, :message, "
                f":related_entity_type, :related_entity_id, :status, :thread_id, :scheduled_for) "
                f"RETURNING {c('id')} AS id"
            ),
            {
                "org_id": org_id,
                "persona": persona,
                "scope": scope,
                "severity": severity,
                "title": title,
                "message": message,
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
                "status": status,
                "thread_id": thread_id,
                "scheduled_for": scheduled_for,
            },
        ).mappings().first()
        return {"id": row["id"], "status": status, "created": True}


def mark_false_positive(engine: Engine, *, notification_id: int, note: str | None) -> None:
    """Plan SP-B §7's false-positive feedback loop -- a human's judgment that
    a dispatched alert was wrong, not the gate's own automatic call. Sets
    `is_false_positive=TRUE` AND transitions `status` to the existing
    'resolved' value in one UPDATE: false-positive-ness is an orthogonal
    quality judgment (was the alert even valid?), not a new workflow-
    lifecycle stage, so it rides on the same CHECK-constrained `status`
    column rather than needing a new value in it."""
    contract = get_contract().entity("notification")
    table = contract.table
    c = contract.column
    with engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE {table} SET {c('is_false_positive')} = TRUE, "
                f"{c('false_positive_note')} = :note, {c('false_positive_marked_at')} = now(), "
                f"{c('status')} = 'resolved', {c('updated_at')} = now() WHERE {c('id')} = :id"
            ),
            {"note": note, "id": notification_id},
        )


def mark_notification_status(engine: Engine, *, notification_id: int, status: str) -> None:
    contract = get_contract().entity("notification")
    table = contract.table
    c = contract.column
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {table} SET {c('status')} = :status, {c('updated_at')} = now() WHERE {c('id')} = :id"),
            {"status": status, "id": notification_id},
        )


def notification_exists_for_thread(engine: Engine, *, org_id: str, thread_id: str) -> bool:
    """BUGFIX (found live: the scheduler's very first real tick against real
    data started 471 sequential real LLM calls -- one per detected signal,
    including dozens of the SAME historical rows the sense layer has no
    reason not to re-detect every tick, since detectors scan a rolling
    window/limit rather than tracking "already seen"). `upsert_notification`
    above is already thread_id-idempotent at the DB-row level, but that
    idempotency kicks in only AFTER reason's LLM call already ran -- this
    lets `supervisor.run_pipeline` check BEFORE invoking the reason->act
    graph at all, so a signal whose thread_id already produced a notification
    on a prior tick is skipped without burning another LLM call or Sarvam-
    budget unit reprocessing something already reasoned about once."""
    contract = get_contract().entity("notification")
    table = contract.table
    c = contract.column
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {c('org_id')} = :org_id AND {c('thread_id')} = :thread_id LIMIT 1"),
            {"org_id": org_id, "thread_id": thread_id},
        ).first()
        return row is not None


def get_notification(engine: Engine, notification_id: int) -> dict[str, Any] | None:
    contract = get_contract().entity("notification")
    table = contract.table
    c = contract.column
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT * FROM {table} WHERE {c('id')} = :id"),
            {"id": notification_id},
        ).mappings().first()
        return dict(row) if row else None


def upsert_report(
    engine: Engine,
    *,
    org_id: str,
    report_type: str,
    persona: str,
    title: str,
    period_start: Any,
    period_end: Any,
    storage_ref: str,
    format: str,
    thread_id: str | None,
) -> dict[str, Any]:
    """Insert or update the one `agent_reports` row for this thread_id +
    report_type -- same idempotency rationale as upsert_notification."""
    contract = get_contract().entity("report")
    table = contract.table
    c = contract.column

    with engine.begin() as conn:
        existing = None
        if thread_id:
            existing = conn.execute(
                text(
                    f"SELECT {c('id')} AS id FROM {table} "
                    f"WHERE {c('org_id')} = :org_id AND {c('thread_id')} = :thread_id "
                    f"AND {c('report_type')} = :report_type ORDER BY {c('id')} DESC LIMIT 1"
                ),
                {"org_id": org_id, "thread_id": thread_id, "report_type": report_type},
            ).mappings().first()

        if existing:
            conn.execute(
                text(
                    f"UPDATE {table} SET {c('title')} = :title, {c('period_start')} = :period_start, "
                    f"{c('period_end')} = :period_end, {c('storage_ref')} = :storage_ref, "
                    f"{c('format')} = :format, {c('generated_at')} = now() WHERE {c('id')} = :id"
                ),
                {
                    "title": title,
                    "period_start": period_start,
                    "period_end": period_end,
                    "storage_ref": storage_ref,
                    "format": format,
                    "id": existing["id"],
                },
            )
            return {"id": existing["id"], "created": False}

        row = conn.execute(
            text(
                f"INSERT INTO {table} "
                f"({c('org_id')}, {c('report_type')}, {c('persona')}, {c('title')}, "
                f"{c('period_start')}, {c('period_end')}, {c('storage_ref')}, {c('format')}, "
                f"{c('thread_id')}) "
                f"VALUES (:org_id, :report_type, :persona, :title, :period_start, :period_end, "
                f":storage_ref, :format, :thread_id) "
                f"RETURNING {c('id')} AS id"
            ),
            {
                "org_id": org_id,
                "report_type": report_type,
                "persona": persona,
                "title": title,
                "period_start": period_start,
                "period_end": period_end,
                "storage_ref": storage_ref,
                "format": format,
                "thread_id": thread_id,
            },
        ).mappings().first()
        return {"id": row["id"], "created": True}


def log_gate_decision(
    engine: Engine,
    *,
    org_id: str,
    persona: str,
    signal_type: str,
    scope: str,
    entity_id: str | None,
    severity: str | None,
    thread_id: str | None,
    action: str,
    reason: str,
    matched_rule: str,
    confidence: float | None,
) -> None:
    """Plain INSERT into gate_decisions (plan SP-B §1c) -- called from
    supervisor.run_pipeline for EVERY (signal, persona) pair regardless of
    the gate's action, including `suppress` (which never produces an
    agent_notifications row, so this table is the only audit trail for it).
    Never updates an existing row -- each evaluation is its own row, which is
    exactly what the recurrence/hysteresis and suppression-heartbeat checks
    in app.graph.reason.gate need to look back over."""
    contract = get_contract().entity("gate_decision")
    table = contract.table
    c = contract.column
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {table} "
                f"({c('org_id')}, {c('persona')}, {c('signal_type')}, {c('scope')}, "
                f"{c('entity_id')}, {c('severity')}, {c('action')}, {c('reason')}, "
                f"{c('matched_rule')}, {c('confidence')}, {c('thread_id')}) "
                f"VALUES (:org_id, :persona, :signal_type, :scope, :entity_id, :severity, "
                f":action, :reason, :matched_rule, :confidence, :thread_id)"
            ),
            {
                "org_id": org_id,
                "persona": persona,
                "signal_type": signal_type,
                "scope": scope,
                "entity_id": entity_id,
                "severity": severity,
                "action": action,
                "reason": reason,
                "matched_rule": matched_rule,
                "confidence": confidence,
                "thread_id": thread_id,
            },
        )
