"""Streaming replay demo tool for the real-data dataset.

Selects real historical trip clusters (a trip row plus its linked cost/
incident/emission rows, joined on trip_id) known to trigger a specific
detector, and re-inserts them with fresh ids and every timestamp shifted by
a constant delta so the cluster lands at "now" while preserving its original
relative timing (a trip that was 43 minutes late stays 43 minutes late).

Each insert fires backend/db/real_data/triggers.sql's NOTIFY triggers, so the
scheduler's event-driven path picks it up the same way it would a genuine
new arrival -- this replays real column values through the real production
code path, at demo pace instead of the original multi-month pace.

Usage:
    DATABASE_URL=... python db/real_data/replay.py --scenario delay_spike --count 5 --interval-seconds 2
    DATABASE_URL=... python db/real_data/replay.py --list-scenarios
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

# Candidate-selection SQL deliberately doesn't reproduce each detector's
# exact algorithm (e.g. billing_discrepancy's median-slab-boundary calc in
# sense/nodes.py) -- it only needs to reliably surface real rows the
# detector WILL flag, not bit-for-bit match its internals. Ordered so the
# strongest/cleanest example replays first.
_SCENARIO_QUERIES: dict[str, str] = {
    "delay_spike": """
        SELECT id AS trip_id
        FROM mis.trip
        WHERE org_id = %(org_id)s
          AND actual_departure IS NOT NULL AND scheduled_departure IS NOT NULL
          AND actual_arrival IS NOT NULL AND scheduled_arrival IS NOT NULL
        ORDER BY (EXTRACT(EPOCH FROM (actual_arrival - scheduled_arrival)) / 60.0) DESC
        LIMIT %(limit)s
    """,
    "escort_violation": """
        SELECT t.id AS trip_id
        FROM mis.trip t
        JOIN mis.commute c ON c.trip_id = t.id
        JOIN mis.employee e ON e.id = c.employee_id
        WHERE t.org_id = %(org_id)s
          AND t.trip_direction = 'LOGOUT'
          AND e.gender = 'FEMALE'
          AND t.actual_escort = FALSE
          AND t.actual_departure IS NOT NULL
          AND (EXTRACT(HOUR FROM t.actual_departure) >= 21 OR EXTRACT(HOUR FROM t.actual_departure) < 6)
        GROUP BY t.id, t.actual_departure
        ORDER BY t.actual_departure DESC
        LIMIT %(limit)s
    """,
    "billing_discrepancy": """
        SELECT c.trip_id AS trip_id
        FROM mis.cost c
        JOIN mis.trip t ON t.id = c.trip_id
        WHERE c.org_id = %(org_id)s
          AND c.slab_name IS NOT NULL AND trim(c.slab_name) <> ''
          AND t.traveled_km IS NOT NULL
          AND (
                (t.traveled_km < 15 AND c.slab_name ILIKE '%%long%%')
             OR (t.traveled_km > 25 AND (c.slab_name ILIKE '%%short%%' OR c.slab_name ILIKE '%%medium%%'))
          )
        ORDER BY c.total_cost_inr DESC
        LIMIT %(limit)s
    """,
    "emissions_over_target": """
        SELECT trip_id
        FROM mis.emission
        WHERE org_id = %(org_id)s
          AND trip_id IS NOT NULL
          AND fuel_type = 'Diesel'
        ORDER BY co2_per_passenger_km DESC
        LIMIT %(limit)s
    """,
}

_TS_COLUMNS: dict[str, list[str]] = {
    "trip": ["scheduled_departure", "scheduled_arrival", "actual_departure", "actual_arrival"],
    "cost": ["cost_date"],
    "incident": ["occurred_at", "reported_at", "acknowledge_time"],
    "emission": ["log_date"],
    "commute": ["log_date", "boarding_time", "alighting_time"],
}


def _fetch_trip_cluster(conn: psycopg.Connection, trip_id: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM mis.trip WHERE id = %s", (trip_id,))
        trip = cur.fetchone()
        cur.execute("SELECT * FROM mis.cost WHERE trip_id = %s", (trip_id,))
        costs = cur.fetchall()
        cur.execute("SELECT * FROM mis.incident WHERE trip_id = %s", (trip_id,))
        incidents = cur.fetchall()
        cur.execute("SELECT * FROM mis.emission WHERE trip_id = %s", (trip_id,))
        emissions = cur.fetchall()
        cur.execute("SELECT * FROM mis.commute WHERE trip_id = %s", (trip_id,))
        commutes = cur.fetchall()
    return {"trip": trip, "costs": costs, "incidents": incidents, "emissions": emissions, "commutes": commutes}


def _shift(row: dict[str, Any], columns: list[str], delta) -> dict[str, Any]:
    shifted = dict(row)
    for col in columns:
        val = shifted.get(col)
        if val is None:
            continue
        shifted[col] = val + delta
    return shifted


def _insert_row(conn: psycopg.Connection, table: str, row: dict[str, Any], *, id_column: str = "id", explicit_id: bool = False) -> Any:
    cols = list(row.keys()) if explicit_id else [c for c in row.keys() if c != id_column]
    placeholders = ", ".join(f"%({c})s" for c in cols)
    col_list = ", ".join(cols)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) RETURNING {id_column}",
            {c: row[c] for c in cols},
        )
        return cur.fetchone()[0]


def replay_one(
    conn: psycopg.Connection,
    trip_id: int,
    now: datetime,
    *,
    preserve_time_of_day: bool = False,
) -> dict[str, Any]:
    cluster = _fetch_trip_cluster(conn, trip_id)
    trip = cluster["trip"]
    if trip is None:
        raise ValueError(f"trip {trip_id} not found")

    anchor = trip.get("actual_departure") or trip.get("scheduled_departure")
    if preserve_time_of_day and anchor is not None:
        # Keep the original time-of-day (e.g. a 21:13 late-night drop) rather
        # than collapsing every timestamp onto wall-clock "now". Hour-of-day-
        # sensitive detectors -- specifically escort_compliance's 21:00-06:00
        # night window (sense/nodes.py) -- only fire when actual_departure
        # still lands in that window, which a plain `now - anchor` shift
        # destroys when the demo is run during the day. Land the cluster on the
        # most recent PAST date that preserves the original clock time, so it's
        # both recent (inside the detector's lookback) and still late-night.
        candidate = anchor.replace(year=now.year, month=now.month, day=now.day)
        if candidate > now:
            candidate = candidate - timedelta(days=1)
        delta = candidate - anchor
    else:
        delta = now - anchor

    new_trip = _shift(trip, _TS_COLUMNS["trip"], delta)
    shifted_departure = new_trip.get("actual_departure") or new_trip.get("scheduled_departure")
    new_trip["trip_date"] = shifted_departure.date() if shifted_departure else now.date()
    new_trip["source_month"] = f"replay-{now.strftime('%Y-%m')}"
    # mis.trip.id has no sequence default (ingest assigns ids explicitly) --
    # generate one in a range well clear of the real ~1M-5M dataset.
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 900000000) + 1 FROM mis.trip WHERE id >= 900000000")
        new_trip["id"] = cur.fetchone()[0]
    new_trip_id = _insert_row(conn, "mis.trip", new_trip, id_column="id", explicit_id=True)

    result = {"source_trip_id": trip_id, "new_trip_id": new_trip_id, "costs": [], "incidents": [], "emissions": [], "commutes": []}

    for cost in cluster["costs"]:
        new_cost = _shift(cost, _TS_COLUMNS["cost"], delta)
        new_cost["trip_id"] = new_trip_id
        result["costs"].append(_insert_row(conn, "mis.cost", new_cost))

    for incident in cluster["incidents"]:
        new_incident = _shift(incident, _TS_COLUMNS["incident"], delta)
        new_incident["trip_id"] = new_trip_id
        new_incident["id"] = f"replay-{new_trip_id}-{incident['id']}"
        result["incidents"].append(_insert_row(conn, "mis.incident", new_incident, explicit_id=True))

    for emission in cluster["emissions"]:
        new_emission = _shift(emission, _TS_COLUMNS["emission"], delta)
        new_emission["trip_id"] = new_trip_id
        new_emission["log_date"] = new_trip["trip_date"]
        result["emissions"].append(_insert_row(conn, "mis.emission", new_emission))

    # Replay the rider legs too: the escort-compliance detector joins
    # mis.trip -> mis.commute -> mis.employee (a female rider on an unescorted
    # late-night LOGOUT), so without the commute leg the injected trip is
    # invisible to it. mis.commute.id is BIGSERIAL, so let the DB assign it
    # (explicit_id defaults to False); employee_id/route_id already exist.
    for commute in cluster["commutes"]:
        new_commute = _shift(commute, _TS_COLUMNS["commute"], delta)
        new_commute["trip_id"] = new_trip_id
        result["commutes"].append(_insert_row(conn, "mis.commute", new_commute))

    conn.commit()
    return result


def fetch_candidate_trip_ids(conn: psycopg.Connection, scenario: str, org_id: str, limit: int) -> list[int]:
    query = _SCENARIO_QUERIES[scenario]
    with conn.cursor() as cur:
        cur.execute(query, {"org_id": org_id, "limit": limit})
        return [row[0] for row in cur.fetchall() if row[0] is not None]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=sorted(_SCENARIO_QUERIES), default="delay_spike")
    parser.add_argument("--org-id", default=None, help="defaults to the contract's default_org_id")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args()

    if args.list_scenarios:
        for name in sorted(_SCENARIO_QUERIES):
            print(name)
        return

    database_url = os.environ["DATABASE_URL"]
    conn = psycopg.connect(database_url)
    try:
        org_id = args.org_id
        if org_id is None:
            with conn.cursor() as cur:
                cur.execute("SELECT org_id, COUNT(*) c FROM mis.trip GROUP BY org_id ORDER BY c DESC LIMIT 1")
                row = cur.fetchone()
                if row is None:
                    print("replay: mis.trip is empty -- run backend/db/real_data/ingest.py first", file=sys.stderr)
                    sys.exit(1)
                org_id = row[0]

        candidates = fetch_candidate_trip_ids(conn, args.scenario, org_id, args.count)
        if not candidates:
            print(f"replay: no real rows found for scenario={args.scenario!r} org_id={org_id!r} -- nothing to replay", file=sys.stderr)
            sys.exit(1)

        print(f"replay: scenario={args.scenario} org_id={org_id} candidates={len(candidates)}")
        for i, trip_id in enumerate(candidates, start=1):
            now = datetime.now(timezone.utc)
            result = replay_one(conn, trip_id, now)
            print(
                f"  [{i}/{len(candidates)}] replayed trip {result['source_trip_id']} -> new trip {result['new_trip_id']} "
                f"(costs={len(result['costs'])}, incidents={len(result['incidents'])}, emissions={len(result['emissions'])}) at {now.isoformat()}"
            )
            if i < len(candidates):
                time.sleep(args.interval_seconds)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
