"""Aggregation queries backing GET /api/charts/* (see app/api/charts.py).

Deliberately LLM-free and cheap: these run on every dashboard view, so each
function is one grouped SQL query (or two, for the vendor scorecard's
sparkline) against contract-resolved table/column names -- same discipline
`dashboard_cards.py` documents for the metric cards, extended here to real
Highcharts series shapes instead of notification cards.

Every window is computed relative to the data's own most-recent date, not
wall-clock "now" -- the real dataset only spans May-Jul 2026, so anchoring to
the actual system clock would return empty charts.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.contracts import get_contract

DELAY_BREACH_MINUTES = 15.0
NODELAY_LABEL = "NODELAY"


def _delay_expr(trip_alias: str, trip: Any) -> str:
    return (
        f"EXTRACT(EPOCH FROM ({trip_alias}.{trip.column('actual_time')} - "
        f"{trip_alias}.{trip.column('scheduled_time')})) / 60.0"
    )


def _max_date(conn, table: str, date_col: str, org_id: str, org_col: str) -> date | None:
    return conn.execute(
        text(f"SELECT MAX({date_col}) FROM {table} WHERE {org_col} = :org_id"),
        {"org_id": org_id},
    ).scalar()


def ota_trend(engine: Engine, org_id: str, days: int = 45) -> dict[str, Any]:
    """Daily on-time-arrival rate: PRD F4/§5 -- on-time = actual_time within
    the 15-minute breach threshold (same threshold detect_delay_signal uses)."""
    contract = get_contract()
    trip = contract.entity("trip")
    with engine.begin() as conn:
        anchor = _max_date(conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id"))
        if anchor is None:
            return {"categories": [], "series": [{"name": "On-Time Arrival %", "data": []}]}
        since = anchor - timedelta(days=days)
        rows = conn.execute(
            text(f"""
                SELECT t.{trip.column('trip_date')} AS d,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE {_delay_expr('t', trip)} <= :breach) AS ontime
                FROM {trip.table} t
                WHERE t.{trip.column('org_id')} = :org_id
                  AND t.{trip.column('status')} = 'completed'
                  AND t.{trip.column('actual_time')} IS NOT NULL
                  AND t.{trip.column('scheduled_time')} IS NOT NULL
                  AND t.{trip.column('trip_date')} > :since
                GROUP BY t.{trip.column('trip_date')}
                ORDER BY t.{trip.column('trip_date')}
            """),
            {"org_id": org_id, "since": since, "breach": DELAY_BREACH_MINUTES},
        ).mappings().all()

    categories = [row["d"].isoformat() for row in rows]
    data = [round(100.0 * row["ontime"] / row["total"], 1) if row["total"] else 0.0 for row in rows]
    return {"categories": categories, "series": [{"name": "On-Time Arrival %", "data": data}]}


def delay_reason_breakdown(engine: Engine, org_id: str, days: int = 90) -> dict[str, Any]:
    """PRD F4/§5 delay-reason breakdown (TRAFFIC/DRIVER/EMPLOYEE/...), with
    completed trips carrying no delay_reason bucketed as NODELAY."""
    contract = get_contract()
    trip = contract.entity("trip")
    with engine.begin() as conn:
        anchor = _max_date(conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id"))
        if anchor is None:
            return {"categories": [], "series": [{"name": "Trips", "data": []}]}
        since = anchor - timedelta(days=days)
        rows = conn.execute(
            text(f"""
                SELECT COALESCE(NULLIF(UPPER(TRIM(t.{trip.column('delay_reason')})), ''), :nodelay) AS reason,
                       COUNT(*) AS cnt
                FROM {trip.table} t
                WHERE t.{trip.column('org_id')} = :org_id
                  AND t.{trip.column('status')} = 'completed'
                  AND t.{trip.column('trip_date')} > :since
                GROUP BY reason
                ORDER BY cnt DESC
                LIMIT 8
            """),
            {"org_id": org_id, "since": since, "nodelay": NODELAY_LABEL},
        ).mappings().all()

    return {
        "categories": [row["reason"] for row in rows],
        "series": [{"name": "Trips", "data": [int(row["cnt"]) for row in rows]}],
    }


def no_show_trend(engine: Engine, org_id: str, days: int = 45) -> dict[str, Any]:
    """PRD F3 team no-show rate trend (Line Manager)."""
    contract = get_contract()
    commute = contract.entity("commute")
    with engine.begin() as conn:
        anchor = _max_date(conn, commute.table, commute.column("log_date"), org_id, commute.column("org_id"))
        if anchor is None:
            return {"categories": [], "series": [{"name": "No-Show Rate %", "data": []}]}
        since = anchor - timedelta(days=days)
        rows = conn.execute(
            text(f"""
                SELECT c.{commute.column('log_date')} AS d,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE c.{commute.column('is_no_show')} = TRUE) AS no_shows
                FROM {commute.table} c
                WHERE c.{commute.column('org_id')} = :org_id
                  AND c.{commute.column('log_date')} > :since
                GROUP BY c.{commute.column('log_date')}
                ORDER BY c.{commute.column('log_date')}
            """),
            {"org_id": org_id, "since": since},
        ).mappings().all()

    categories = [row["d"].isoformat() for row in rows]
    data = [round(100.0 * row["no_shows"] / row["total"], 1) if row["total"] else 0.0 for row in rows]
    return {"categories": categories, "series": [{"name": "No-Show Rate %", "data": data}]}


def absence_split(engine: Engine, org_id: str, days: int = 90) -> dict[str, Any]:
    """PRD F3 delay-caused vs. employee-caused no-show split -- mirrors
    detect_attendance_correlation's own transport-correlation test exactly
    (mode='shuttle' AND delay past the same 15-min breach threshold), applied
    here to no-show legs instead of late-clock-in legs."""
    contract = get_contract()
    commute = contract.entity("commute")
    trip = contract.entity("trip")
    with engine.begin() as conn:
        anchor = _max_date(conn, commute.table, commute.column("log_date"), org_id, commute.column("org_id"))
        if anchor is None:
            return {"series": [{"name": "No-Shows", "data": []}]}
        since = anchor - timedelta(days=days)
        row = conn.execute(
            text(f"""
                SELECT
                    COUNT(*) AS total_no_shows,
                    COUNT(*) FILTER (
                        WHERE c.{commute.column('mode')} = 'shuttle'
                          AND t.{trip.column('actual_time')} IS NOT NULL
                          AND t.{trip.column('scheduled_time')} IS NOT NULL
                          AND {_delay_expr('t', trip)} > :breach
                    ) AS delay_caused
                FROM {commute.table} c
                LEFT JOIN {trip.table} t ON t.{trip.column('id')} = c.{commute.column('trip_id')}
                WHERE c.{commute.column('org_id')} = :org_id
                  AND c.{commute.column('is_no_show')} = TRUE
                  AND c.{commute.column('log_date')} > :since
            """),
            {"org_id": org_id, "since": since, "breach": DELAY_BREACH_MINUTES},
        ).mappings().first()

    total = int(row["total_no_shows"] or 0)
    delay_caused = int(row["delay_caused"] or 0)
    employee_caused = max(total - delay_caused, 0)
    return {
        "series": [
            {
                "name": "No-Shows",
                "data": [
                    {"name": "Delay-Caused", "y": delay_caused},
                    {"name": "Employee-Caused", "y": employee_caused},
                ],
            }
        ]
    }


def billing_discrepancy(engine: Engine, org_id: str, months: int = 6) -> dict[str, Any]:
    """PRD F2: billed distance (bill_data.total_trip_km, contract: cost.distance_km)
    vs. actual GPS distance (ride_data_trip.traveled_km, contract:
    trip.traveled_km), monetised at the rate actually billed
    (cost.cost_per_km). Only the overbilled direction (billed > traveled)
    counts as recoverable leakage, matching the PRD's "recover 8-12% of
    spend" framing -- underbilling isn't a dispute-log item."""
    contract = get_contract()
    cost = contract.entity("cost")
    trip = contract.entity("trip")
    with engine.begin() as conn:
        anchor = _max_date(conn, cost.table, cost.column("cost_date"), org_id, cost.column("org_id"))
        if anchor is None:
            return {"categories": [], "series": [{"name": "Billing Discrepancy (₹)", "data": []}]}
        since = anchor - timedelta(days=months * 31)
        rows = conn.execute(
            text(f"""
                SELECT date_trunc('month', c.{cost.column('cost_date')})::date AS m,
                       SUM(GREATEST(c.{cost.column('distance_km')} - t.{trip.column('traveled_km')}, 0)
                           * c.{cost.column('cost_per_km')}) AS discrepancy
                FROM {cost.table} c
                JOIN {trip.table} t ON t.{trip.column('id')} = c.{cost.column('trip_id')}
                WHERE c.{cost.column('org_id')} = :org_id
                  AND c.{cost.column('cost_date')} > :since
                  AND c.{cost.column('distance_km')} IS NOT NULL
                  AND t.{trip.column('traveled_km')} IS NOT NULL
                  AND c.{cost.column('cost_per_km')} IS NOT NULL
                GROUP BY m
                ORDER BY m
            """),
            {"org_id": org_id, "since": since},
        ).mappings().all()

    categories = [row["m"].strftime("%b %Y") for row in rows]
    data = [round(float(row["discrepancy"] or 0.0), 2) for row in rows]
    return {"categories": categories, "series": [{"name": "Billing Discrepancy (₹)", "data": data}]}


def emissions_by_fuel(engine: Engine, org_id: str, days: int = 90) -> dict[str, Any]:
    """PRD F5: weekly CO2 (tonnes) stacked by actual_cab_fuel_type (Diesel/
    Petrol/Electric), using the emission coefficients already baked into
    mis.emission.co2_grams at ingestion time (170/150/0 gCO2/km per the PRD)."""
    contract = get_contract()
    emission = contract.entity("emission")
    with engine.begin() as conn:
        anchor = _max_date(conn, emission.table, emission.column("log_date"), org_id, emission.column("org_id"))
        if anchor is None:
            return {"categories": [], "series": []}
        since = anchor - timedelta(days=days)
        rows = conn.execute(
            text(f"""
                SELECT date_trunc('week', e.{emission.column('log_date')})::date AS wk,
                       e.{emission.column('fuel_type')} AS fuel,
                       SUM(e.{emission.column('co2_grams')}) / 1000000.0 AS tonnes
                FROM {emission.table} e
                WHERE e.{emission.column('org_id')} = :org_id
                  AND e.{emission.column('log_date')} > :since
                  AND e.{emission.column('fuel_type')} IS NOT NULL
                GROUP BY wk, fuel
                ORDER BY wk
            """),
            {"org_id": org_id, "since": since},
        ).mappings().all()

    weeks: list[date] = sorted({row["wk"] for row in rows})
    fuels: list[str] = sorted({row["fuel"] for row in rows})
    by_week_fuel = {(row["wk"], row["fuel"]): float(row["tonnes"]) for row in rows}

    categories = [wk.isoformat() for wk in weeks]
    series = [
        {"name": fuel, "data": [round(by_week_fuel.get((wk, fuel), 0.0), 3) for wk in weeks]}
        for fuel in fuels
    ]
    return {"categories": categories, "series": series}


def vendor_scorecard(engine: Engine, org_id: str, days: int = 90, limit: int = 12) -> dict[str, Any]:
    """PRD TH1: SLA% / cost-per-km / incident count per vendor, plus a
    weekly on-time-rate sparkline. sla_target_pct and cost_per_km are the
    observed values computed once at ingestion (see mis_schema.sql); the
    incident count and sparkline are windowed here."""
    contract = get_contract()
    vendor = contract.entity("vendor")
    route = contract.entity("route")
    incident = contract.entity("incident")
    trip = contract.entity("trip")

    with engine.begin() as conn:
        anchor = _max_date(conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id"))
        # A concrete sentinel (rather than a NULL bind + "::date IS NULL"
        # check) sidesteps a SQLAlchemy text() parsing quirk where a bind
        # param immediately followed by a "::cast" and reused later in the
        # same statement doesn't get substituted correctly.
        since = anchor - timedelta(days=days) if anchor else date(1970, 1, 1)

        main_rows = conn.execute(
            text(f"""
                SELECT v.{vendor.column('id')} AS vendor_id,
                       v.{vendor.column('name')} AS name,
                       v.{vendor.column('sla_target_pct')} AS sla_pct,
                       v.{vendor.column('cost_per_km')} AS cost_per_km,
                       COUNT(i.{incident.column('id')}) AS incident_count
                FROM {vendor.table} v
                LEFT JOIN {route.table} r ON r.{route.column('vendor_id')} = v.{vendor.column('id')}
                LEFT JOIN {incident.table} i ON i.{incident.column('route_id')} = r.{route.column('id')}
                    AND i.{incident.column('org_id')} = :org_id
                    AND i.{incident.column('occurred_at')} > :since
                WHERE v.{vendor.column('org_id')} = :org_id
                GROUP BY v.{vendor.column('id')}, v.{vendor.column('name')}, v.{vendor.column('sla_target_pct')}, v.{vendor.column('cost_per_km')}
                ORDER BY v.{vendor.column('sla_target_pct')} DESC NULLS LAST
                LIMIT :limit
            """),
            {"org_id": org_id, "since": since, "limit": limit},
        ).mappings().all()

        vendor_ids = [row["vendor_id"] for row in main_rows]
        sparkline_by_vendor: dict[int, list[float]] = {vid: [] for vid in vendor_ids}
        if vendor_ids and anchor:
            spark_rows = conn.execute(
                text(f"""
                    SELECT r.{route.column('vendor_id')} AS vendor_id,
                           date_trunc('week', t.{trip.column('trip_date')})::date AS wk,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE {_delay_expr('t', trip)} <= :breach) AS ontime
                    FROM {trip.table} t
                    JOIN {route.table} r ON r.{route.column('id')} = t.{trip.column('route_id')}
                    WHERE t.{trip.column('org_id')} = :org_id
                      AND t.{trip.column('status')} = 'completed'
                      AND t.{trip.column('actual_time')} IS NOT NULL
                      AND t.{trip.column('scheduled_time')} IS NOT NULL
                      AND t.{trip.column('trip_date')} > :since
                      AND r.{route.column('vendor_id')} = ANY(:vendor_ids)
                    GROUP BY r.{route.column('vendor_id')}, wk
                    ORDER BY r.{route.column('vendor_id')}, wk
                """),
                {
                    "org_id": org_id,
                    "since": since,
                    "breach": DELAY_BREACH_MINUTES,
                    "vendor_ids": vendor_ids,
                },
            ).mappings().all()
            for row in spark_rows:
                pct = round(100.0 * row["ontime"] / row["total"], 1) if row["total"] else 0.0
                sparkline_by_vendor.setdefault(row["vendor_id"], []).append(pct)

    vendors = [
        {
            "vendor": row["name"],
            "sla_pct": round(float(row["sla_pct"]), 1) if row["sla_pct"] is not None else 0.0,
            "cost_per_km": round(float(row["cost_per_km"]), 2) if row["cost_per_km"] is not None else 0.0,
            "incident_count": int(row["incident_count"] or 0),
            "sla_trend": sparkline_by_vendor.get(row["vendor_id"], []),
        }
        for row in main_rows
    ]
    return {"vendors": vendors}
