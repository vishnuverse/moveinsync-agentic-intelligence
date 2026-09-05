"""Shared window-anchoring helper for chart_data.py and aggregated_insights.py.

BUGFIX (found live, 2026-09-05): every rollup in this codebase used to anchor
its "last N days" window on the literal MAX(date) for the org. That broke
once the live-ops demo replay started inserting a handful of rows dated at
real wall-clock "today" -- disconnected by a real multi-week gap from the
actual historical bulk. Verified live: vanta-Aus's real dense data runs
2026-05-01..2026-07-31 (hundreds-to-thousands of trips/day), then a 36-day
gap with zero rows, then exactly 3 trip rows on 2026-09-05 from the replay.
A naive "7 days back from MAX(trip_date)" window lands entirely in that
empty gap, so every trend chart and rollup card showed 0%/near-empty numbers
despite months of real, dense data sitting just a few weeks further back.

`dense_anchor_date` fixes this generically: it picks the most recent date
with at least `min_rows` rows for the org, falling back to the literal
MAX(date) only if no day ever clears that bar (so a genuinely tiny dataset
still gets *a* value, never silently None). `min_rows` defaults to a value
comfortably above what the replay trickle inserts per day (observed: 3) and
comfortably below every real org's typical daily volume (observed: even the
lowest legitimate low-volume day -- a weekend -- still runs ~17-180 rows) --
this is a robustness heuristic for picking a sane *default* anchor, not a
precise real-vs-fake classifier. Every caller that knows exactly which
window it wants (the date-range picker, ultimately) passes explicit
`since`/`until` instead and bypasses this entirely.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

DEFAULT_MIN_DENSE_ROWS = 20


def dense_anchor_date(
    conn, table: str, date_col: str, org_id: str, org_col: str, *, min_rows: int = DEFAULT_MIN_DENSE_ROWS
) -> date | None:
    row = conn.execute(
        text(
            f"SELECT {date_col} AS d FROM {table} WHERE {org_col} = :org_id "
            f"GROUP BY {date_col} HAVING COUNT(*) >= :min_rows "
            f"ORDER BY {date_col} DESC LIMIT 1"
        ),
        {"org_id": org_id, "min_rows": min_rows},
    ).mappings().first()
    if row is not None:
        return row["d"]
    return conn.execute(
        text(f"SELECT MAX({date_col}) FROM {table} WHERE {org_col} = :org_id"),
        {"org_id": org_id},
    ).scalar()
