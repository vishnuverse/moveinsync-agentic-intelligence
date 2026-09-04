"""Read helpers around `agent_reports` (plan §11's GET /api/reports and
GET /api/reports/{id}/html). Contract-resolved (plan §3).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.contracts import get_contract
from app.graph.act.db import get_engine


def list_reports(org_id: str, persona: str, *, limit: int = 20) -> list[dict[str, Any]]:
    contract = get_contract().entity("report")
    table = contract.table
    c = contract.column
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"SELECT {c('id')} AS id, {c('title')} AS title, {c('report_type')} AS report_type, "
                f"{c('period_start')} AS period_start, {c('period_end')} AS period_end, "
                f"{c('generated_at')} AS generated_at "
                f"FROM {table} WHERE {c('org_id')} = :org_id AND {c('persona')} = :persona "
                f"ORDER BY {c('generated_at')} DESC LIMIT :limit"
            ),
            {"org_id": org_id, "persona": persona, "limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


def get_report(report_id: int) -> dict[str, Any] | None:
    contract = get_contract().entity("report")
    table = contract.table
    c = contract.column
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT * FROM {table} WHERE {c('id')} = :id"),
            {"id": report_id},
        ).mappings().first()
    return dict(row) if row else None
