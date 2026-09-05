"""GET /api/data-coverage -- the date span and volume of the underlying
`trip` data (plan API contract).

The real dataset only covers a fixed historical window, so the UI can't
anchor "as of" copy to wall-clock now; this exposes MIN/MAX(trip_date) and a
row count over the contract's `trip` entity so the frontend can say "data
through <end_date>" instead. Contract-resolved (plan §3), engine reused from
the shared pool like the other services (app.graph.act.db.get_engine).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import default_org_id
from app.api.schemas import DataCoverage
from app.contracts import get_contract
from app.graph.act.db import get_engine
from app.services.date_window import dense_anchor_date
from sqlalchemy import text

router = APIRouter(tags=["meta"])


def _iso(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


@router.get("/data-coverage", response_model=DataCoverage)
def data_coverage(org_id: str | None = Query(default=None)) -> DataCoverage:
    resolved_org = org_id or default_org_id()
    trip = get_contract().entity("trip")
    c = trip.column
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"SELECT MIN({c('trip_date')}) AS start_date, "
                f"MAX({c('trip_date')}) AS end_date, COUNT(*) AS trip_count "
                f"FROM {trip.table} WHERE {c('org_id')} = :org_id"
            ),
            {"org_id": resolved_org},
        ).mappings().first()
        dense_end = dense_anchor_date(conn, trip.table, c("trip_date"), resolved_org, c("org_id"))

    start = row["start_date"] if row else None
    end = row["end_date"] if row else None
    return DataCoverage(
        start_date=_iso(start),
        end_date=_iso(end),
        trip_count=int(row["trip_count"]) if row and row["trip_count"] is not None else 0,
        dense_end_date=_iso(dense_end),
    )
