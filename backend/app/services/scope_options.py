"""GET /api/chat/scope-options?persona= -- the "select something to chat
with" feature's data source. One cheap, LLM-free read per persona (same cost
discipline as app/services/chart_data.py: this loads on every "new thread"
modal open, so it must never touch an LLM), entirely through the data
contract so it keeps working unchanged in both synthetic and real-data mode.

Persona -> entity mapping follows the build brief's own breakdown: Transport
Manager thinks in routes/vendors, Line Manager in teams, Transport Head in
vendor portfolio/region. No auth/session exists in this build (see
app/api/chat.py's module docstring), so a Line Manager can't be scoped to
"their own" team specifically -- every team is offered, the same latitude
the rest of this no-login build already takes elsewhere.

Each option's `id` is deliberately a human/DB-grounded display value (a
vendor name, a route_code, a team name, a region string) rather than a
numeric surrogate key: it gets stored verbatim on chat_threads.scope_entity_id
and prepended straight into the NL question the chat endpoint sends to
run_chat_turn (e.g. "Regarding vendor 'GreenLine Mobility': ..."), so it
needs to already be a value the SQL agent would recognise in the data, not an
id it would have to look up first.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.contracts import get_contract

_LIMIT = 50


def _rows(engine: Engine, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    with engine.begin() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def list_scope_options(engine: Engine, org_id: str, persona: str) -> list[dict[str, str]]:
    contract = get_contract()
    options: list[dict[str, str]] = []

    if persona in ("transport_manager", "transport_head"):
        vendor = contract.entity("vendor")
        rows = _rows(
            engine,
            f"SELECT DISTINCT {vendor.column('name')} AS name FROM {vendor.table} "
            f"WHERE {vendor.column('org_id')} = :org_id AND {vendor.column('name')} IS NOT NULL "
            f"ORDER BY name LIMIT :limit",
            {"org_id": org_id, "limit": _LIMIT},
        )
        options += [{"type": "vendor", "id": r["name"], "label": r["name"]} for r in rows]

    if persona == "transport_manager":
        route = contract.entity("route")
        rows = _rows(
            engine,
            f"SELECT {route.column('route_code')} AS code, {route.column('name')} AS name "
            f"FROM {route.table} WHERE {route.column('org_id')} = :org_id "
            f"AND {route.column('route_code')} IS NOT NULL "
            f"ORDER BY {route.column('route_code')} LIMIT :limit",
            {"org_id": org_id, "limit": _LIMIT},
        )
        options += [
            {"type": "route", "id": r["code"], "label": f"{r['code']} — {r['name']}" if r["name"] else r["code"]}
            for r in rows
        ]

    if persona == "line_manager":
        team = contract.entity("team")
        rows = _rows(
            engine,
            f"SELECT DISTINCT {team.column('name')} AS name FROM {team.table} "
            f"WHERE {team.column('org_id')} = :org_id AND {team.column('name')} IS NOT NULL "
            f"ORDER BY name LIMIT :limit",
            {"org_id": org_id, "limit": _LIMIT},
        )
        options += [{"type": "team", "id": r["name"], "label": r["name"]} for r in rows]

    if persona == "transport_head":
        vendor = contract.entity("vendor")
        rows = _rows(
            engine,
            f"SELECT DISTINCT {vendor.column('region')} AS region FROM {vendor.table} "
            f"WHERE {vendor.column('org_id')} = :org_id AND {vendor.column('region')} IS NOT NULL "
            f"ORDER BY region LIMIT :limit",
            {"org_id": org_id, "limit": _LIMIT},
        )
        options += [{"type": "region", "id": r["region"], "label": r["region"]} for r in rows]

    return options
