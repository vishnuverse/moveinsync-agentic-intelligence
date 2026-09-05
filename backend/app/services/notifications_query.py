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


def list_notifications(
    org_id: str, persona: str, *, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    """BUGFIX (found live: chat Q&A answers -- "which vendor has the fewest
    incidents", "no trips found this week" -- were showing up as permanent
    entries in the Notification Inbox and, via dashboard_cards.py, as
    dashboard KPI cards, crowding out real autonomous findings). Root cause:
    `bridge_to_act` (app/graph/graph.py) has no `signal` to inspect for a
    run_chat_turn() call (chat bypasses sense entirely), so every chat turn
    falls through to the default action_type="notification" and gets a
    permanent row here, the same table real signal-driven alerts use.
    `app.graph.supervisor.run_chat_turn` sets scope="chat" explicitly, and no
    real signal-driven scope is ever literally that string (they're always
    entity-derived, e.g. "route:RT-001") -- the cheapest available way to
    exclude chat noise from every reader of this table (this function backs
    both the dashboard and the inbox, plus run_report()'s digest content) in
    one place, without a schema change or touching the graph itself."""
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
                f"AND {c('scope')} != 'chat' "
                f"ORDER BY {c('created_at')} DESC LIMIT :limit OFFSET :offset"
            ),
            {"org_id": org_id, "persona": persona, "limit": limit, "offset": offset},
        ).mappings().all()
    return [dict(r) for r in rows]


def count_notifications(org_id: str, persona: str) -> int:
    """Total count of the rows list_notifications() paginates over -- same
    org/persona filter and the same `scope != 'chat'` exclusion, so the
    `total` the API returns matches what a fully-paged-through `items` would
    yield. Ignores limit/offset by design (that's what makes it a total)."""
    contract = get_contract().entity("notification")
    table = contract.table
    c = contract.column
    engine = get_engine()
    with engine.begin() as conn:
        total = conn.execute(
            text(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {c('org_id')} = :org_id AND {c('persona')} = :persona "
                f"AND {c('scope')} != 'chat'"
            ),
            {"org_id": org_id, "persona": persona},
        ).scalar()
    return int(total or 0)
