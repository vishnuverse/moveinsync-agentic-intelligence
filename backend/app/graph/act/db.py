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
) -> dict[str, Any]:
    """Insert or update the one `agent_notifications` row for this thread_id.

    Without a thread_id (no LangGraph config supplied it), idempotency isn't
    possible -- falls back to a plain insert, which is fine for direct/ad-hoc
    node calls that aren't part of a resumable graph run.
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
                f"{c('status')}, {c('thread_id')}) "
                f"VALUES (:org_id, :persona, :scope, :severity, :title, :message, "
                f":related_entity_type, :related_entity_id, :status, :thread_id) "
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
            },
        ).mappings().first()
        return {"id": row["id"], "status": status, "created": True}


def mark_notification_status(engine: Engine, *, notification_id: int, status: str) -> None:
    contract = get_contract().entity("notification")
    table = contract.table
    c = contract.column
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {table} SET {c('status')} = :status, {c('updated_at')} = now() WHERE {c('id')} = :id"),
            {"status": status, "id": notification_id},
        )


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
