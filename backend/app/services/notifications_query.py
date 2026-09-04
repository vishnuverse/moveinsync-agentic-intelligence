"""Read helpers around `agent_notifications` that app/graph/act/db.py doesn't
already provide (it has get_notification-by-id, upsert, mark_status -- no
"list by persona" query, since the act subgraph itself never needs one).
Contract-resolved (plan §3), same convention as app/graph/act/db.py.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.contracts import get_contract
from app.graph.act.db import get_engine

# DB status -> frontend NotificationStatus ("open" | "acked" | "needs-intervention").
# The frontend contract has no "resolved" state (a rejected/completed sign-off
# still reads as a settled, acknowledged item in the inbox), so it collapses
# into "acked" here rather than leaking a status value the UI can't render.
_STATUS_TO_FRONTEND = {
    "open": "open",
    "acked": "acked",
    "needs_intervention": "needs-intervention",
    "resolved": "acked",
}


def to_frontend_status(db_status: str) -> str:
    return _STATUS_TO_FRONTEND.get(db_status, "open")


def list_notifications(org_id: str, persona: str, *, limit: int = 50) -> list[dict[str, Any]]:
    contract = get_contract().entity("notification")
    table = contract.table
    c = contract.column
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"SELECT {c('id')} AS id, {c('severity')} AS severity, {c('title')} AS title, "
                f"{c('message')} AS message, {c('status')} AS status, {c('thread_id')} AS thread_id, "
                f"{c('scope')} AS scope, {c('created_at')} AS created_at "
                f"FROM {table} WHERE {c('org_id')} = :org_id AND {c('persona')} = :persona "
                f"ORDER BY {c('created_at')} DESC LIMIT :limit"
            ),
            {"org_id": org_id, "persona": persona, "limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]
