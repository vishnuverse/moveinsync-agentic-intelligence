"""Chat thread CRUD (chat history feature) over backend/db/api_schema.sql's
`chat_threads` table -- same "runtime output, not seed data" home as
pipeline_runs (see that table's comment in api_schema.sql and
app/services/activity_log.py), reached through the data contract the same
way, never a literal "chat_threads" string outside this module.

`id` IS the LangGraph checkpoint thread_id (app.graph.supervisor.build_thread_id's
own `{persona}:{scope}:{ref}` shape, scope="chat") -- there is deliberately
no second, separate "chat thread id" identifier space. That keeps a thread's
Trace Drawer trivially resolvable (GET /threads/{id}/trace already takes
exactly this string) and is what app/api/chat.py's module docstring means by
repurposing the episodic-memory namespace's `user_id` slot to hold this same
value.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.contracts import get_contract
from app.graph.supervisor import build_thread_id

_TITLE_MAX_LEN = 40

_COLUMNS = ("id", "org_id", "persona", "title", "scope_entity_type", "scope_entity_id", "created_at", "updated_at")


def default_title(first_message: str | None = None) -> str:
    """Cheap, deterministic auto-titling from the first user message (a
    truncation, not a second LLM call) -- matches this project's existing
    "no LLM call where a cheap deterministic option exists" cost discipline
    (same reasoning app/api/charts.py's module docstring states for its own
    LLM-free aggregation endpoints). A user can always rename manually via
    PATCH /chat/threads/{id} if the truncation reads oddly."""
    if first_message and first_message.strip():
        flattened = " ".join(first_message.strip().split())
        if len(flattened) > _TITLE_MAX_LEN:
            return flattened[:_TITLE_MAX_LEN].rstrip() + "…"
        return flattened
    return "New conversation"


def new_thread_id(persona: str) -> str:
    return build_thread_id(persona, "chat", uuid.uuid4().hex[:12])


def _entity():
    return get_contract().entity("chat_thread")


def _select_clause(contract) -> str:
    c = contract.column
    return ", ".join(f"{c(col)} AS {col}" for col in _COLUMNS)


def create_thread(
    engine: Engine,
    org_id: str,
    persona: str,
    *,
    title: str | None = None,
    scope_entity_type: str | None = None,
    scope_entity_id: str | None = None,
) -> dict[str, Any]:
    contract = _entity()
    c = contract.column
    thread_id = new_thread_id(persona)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {contract.table} "
                f"({c('id')}, {c('org_id')}, {c('persona')}, {c('title')}, "
                f"{c('scope_entity_type')}, {c('scope_entity_id')}) "
                f"VALUES (:id, :org_id, :persona, :title, :scope_entity_type, :scope_entity_id)"
            ),
            {
                "id": thread_id,
                "org_id": org_id,
                "persona": persona,
                "title": title or default_title(),
                "scope_entity_type": scope_entity_type,
                "scope_entity_id": scope_entity_id,
            },
        )
    thread = get_thread(engine, thread_id)
    assert thread is not None  # just inserted in the same transaction scope
    return thread


def get_thread(engine: Engine, thread_id: str) -> dict[str, Any] | None:
    contract = _entity()
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT {_select_clause(contract)} FROM {contract.table} WHERE {contract.column('id')} = :id"),
            {"id": thread_id},
        ).mappings().first()
    return dict(row) if row else None


def list_threads(engine: Engine, org_id: str, persona: str) -> list[dict[str, Any]]:
    contract = _entity()
    c = contract.column
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"SELECT {_select_clause(contract)} FROM {contract.table} "
                f"WHERE {c('org_id')} = :org_id AND {c('persona')} = :persona "
                f"ORDER BY {c('updated_at')} DESC"
            ),
            {"org_id": org_id, "persona": persona},
        ).mappings().all()
    return [dict(r) for r in rows]


def rename_thread(engine: Engine, thread_id: str, title: str) -> dict[str, Any] | None:
    contract = _entity()
    c = contract.column
    with engine.begin() as conn:
        result = conn.execute(
            text(f"UPDATE {contract.table} SET {c('title')} = :title, {c('updated_at')} = now() WHERE {c('id')} = :id"),
            {"title": title, "id": thread_id},
        )
        if result.rowcount == 0:
            return None
    return get_thread(engine, thread_id)


def touch_thread(engine: Engine, thread_id: str) -> None:
    """Bumps updated_at after every message so GET /chat/threads' most-
    recent-first ordering reflects actual conversation activity rather than
    only creation time."""
    contract = _entity()
    c = contract.column
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {contract.table} SET {c('updated_at')} = now() WHERE {c('id')} = :id"),
            {"id": thread_id},
        )


def delete_thread(engine: Engine, thread_id: str) -> bool:
    """Deletes only the chat_threads row -- the caller (app/api/chat.py) is
    responsible for also calling app.memory.forget_thread() for the same
    persona/thread_id so the episodic memory doesn't outlive the thread it
    belongs to. Kept as two calls rather than one because this module has no
    business reaching into app.memory's store, same separation
    activity_log.py keeps from app.graph.act.db."""
    contract = _entity()
    c = contract.column
    with engine.begin() as conn:
        result = conn.execute(text(f"DELETE FROM {contract.table} WHERE {c('id')} = :id"), {"id": thread_id})
    return result.rowcount > 0
