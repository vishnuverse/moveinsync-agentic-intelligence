"""Aggregation queries backing GET /api/charts/* (see app/api/charts.py).

Deliberately LLM-free and cheap: these run on every dashboard view, so every
query here is a grouped SQL aggregate (plus, where a chart benchmarks against
a target or a previous window, one or two more equally cheap aggregates/
lookups -- never an LLM call) against contract-resolved table/column names --
same discipline `dashboard_cards.py` documents for the metric cards, extended
here to real Highcharts series shapes instead of notification cards.

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


# `sustainability_targets` is a fixed reference/infra table, referenced by its
# literal name rather than through the contract -- same precedent already set
# by app/graph/reason/research_agent/lookup.py (see that module's docstring),
# whose curated benchmark values this reuses instead of duplicating magic
# numbers in chart code or the frontend.
_SUSTAINABILITY_TARGETS_TABLE = "sustainability_targets"


def _benchmark(conn, org_id: str, metric_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            f"""
            SELECT target_value, threshold_value, unit, notes
            FROM {_SUSTAINABILITY_TARGETS_TABLE}
            WHERE org_id = :org_id AND metric_name = :metric_name
            ORDER BY effective_from DESC
            LIMIT 1
            """
        ),
        {"org_id": org_id, "metric_name": metric_name},
    ).mappings().first()
    return dict(row) if row is not None else None


def ota_trend(engine: Engine, org_id: str, days: int = 45) -> dict[str, Any]:
    """Daily on-time-arrival rate: PRD F4/§5 -- on-time = actual_time within
    the 15-minute breach threshold (same threshold detect_delay_signal uses).

    Also surfaces the curated `sla_timeliness_pct` benchmark (95% target /
    92% breach floor) and a vs-previous-window comparison, so the chart reads
    as "78% against a 95% target, two vendors behind it" rather than a bare
    trend line -- the same "context, not just a number" bar the metric cards
    already clear (dashboard_cards.py's context_note)."""
    contract = get_contract()
    trip = contract.entity("trip")
    with engine.begin() as conn:
        anchor = _max_date(conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id"))
        if anchor is None:
            return {"categories": [], "series": [{"name": "On-Time Arrival %", "data": []}]}
        since = anchor - timedelta(days=days)
        prev_since = anchor - timedelta(days=2 * days)
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

        prev_row = conn.execute(
            text(f"""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE {_delay_expr('t', trip)} <= :breach) AS ontime
                FROM {trip.table} t
                WHERE t.{trip.column('org_id')} = :org_id
                  AND t.{trip.column('status')} = 'completed'
                  AND t.{trip.column('actual_time')} IS NOT NULL
                  AND t.{trip.column('scheduled_time')} IS NOT NULL
                  AND t.{trip.column('trip_date')} > :prev_since
                  AND t.{trip.column('trip_date')} <= :since
            """),
            {"org_id": org_id, "prev_since": prev_since, "since": since, "breach": DELAY_BREACH_MINUTES},
        ).mappings().first()

        benchmark = _benchmark(conn, org_id, "sla_timeliness_pct")

    categories = [row["d"].isoformat() for row in rows]
    data = [round(100.0 * row["ontime"] / row["total"], 1) if row["total"] else 0.0 for row in rows]
    current_total = sum(row["total"] for row in rows)
    current_ontime = sum(row["ontime"] for row in rows)
    current_pct = round(100.0 * current_ontime / current_total, 1) if current_total else 0.0

    result: dict[str, Any] = {"categories": categories, "series": [{"name": "On-Time Arrival %", "data": data}]}
    if benchmark is not None:
        result["target"] = float(benchmark["target_value"])
        result["breach_threshold"] = float(benchmark["threshold_value"]) if benchmark["threshold_value"] is not None else None
        result["target_label"] = f"SLA target ({benchmark['target_value']:.0f}%)"
    if prev_row and prev_row["total"]:
        prev_pct = round(100.0 * prev_row["ontime"] / prev_row["total"], 1)
        result["comparison"] = {
            "label": f"vs previous {days}d",
            "current_value": current_pct,
            "previous_value": prev_pct,
            "delta_pct": round(current_pct - prev_pct, 1),
        }
    return result


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
        prev_since = anchor - timedelta(days=2 * days)
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

        prev_total = conn.execute(
            text(f"""
                SELECT COUNT(*) AS cnt
                FROM {trip.table} t
                WHERE t.{trip.column('org_id')} = :org_id
                  AND t.{trip.column('status')} = 'completed'
                  AND t.{trip.column('delay_reason')} IS NOT NULL
                  AND TRIM(t.{trip.column('delay_reason')}) <> ''
                  AND t.{trip.column('trip_date')} > :prev_since
                  AND t.{trip.column('trip_date')} <= :since
            """),
            {"org_id": org_id, "prev_since": prev_since, "since": since},
        ).scalar() or 0

    result: dict[str, Any] = {
        "categories": [row["reason"] for row in rows],
        "series": [{"name": "Trips", "data": [int(row["cnt"]) for row in rows]}],
    }
    current_flagged = sum(int(row["cnt"]) for row in rows if row["reason"] != NODELAY_LABEL)
    if prev_total:
        delta_pct = round((current_flagged - prev_total) / prev_total * 100.0, 1)
        result["comparison"] = {
            "label": f"flagged delays vs previous {days}d",
            "current_value": current_flagged,
            "previous_value": int(prev_total),
            "delta_pct": delta_pct,
        }
    return result


def no_show_trend(engine: Engine, org_id: str, days: int = 45) -> dict[str, Any]:
    """PRD F3 team no-show rate trend (Line Manager)."""
    contract = get_contract()
    commute = contract.entity("commute")
    with engine.begin() as conn:
        anchor = _max_date(conn, commute.table, commute.column("log_date"), org_id, commute.column("org_id"))
        if anchor is None:
            return {"categories": [], "series": [{"name": "No-Show Rate %", "data": []}]}
        since = anchor - timedelta(days=days)
        prev_since = anchor - timedelta(days=2 * days)
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

        prev_row = conn.execute(
            text(f"""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE c.{commute.column('is_no_show')} = TRUE) AS no_shows
                FROM {commute.table} c
                WHERE c.{commute.column('org_id')} = :org_id
                  AND c.{commute.column('log_date')} > :prev_since
                  AND c.{commute.column('log_date')} <= :since
            """),
            {"org_id": org_id, "prev_since": prev_since, "since": since},
        ).mappings().first()

    categories = [row["d"].isoformat() for row in rows]
    data = [round(100.0 * row["no_shows"] / row["total"], 1) if row["total"] else 0.0 for row in rows]
    result: dict[str, Any] = {"categories": categories, "series": [{"name": "No-Show Rate %", "data": data}]}

    current_total = sum(row["total"] for row in rows)
    current_no_shows = sum(row["no_shows"] for row in rows)
    if current_total and prev_row and prev_row["total"]:
        current_pct = round(100.0 * current_no_shows / current_total, 1)
        prev_pct = round(100.0 * prev_row["no_shows"] / prev_row["total"], 1)
        result["comparison"] = {
            "label": f"vs previous {days}d",
            "current_value": current_pct,
            "previous_value": prev_pct,
            "delta_pct": round(current_pct - prev_pct, 1),
        }
    return result


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


def billing_discrepancy(engine: Engine, org_id: str, months: int = 6, top_n: int = 4) -> dict[str, Any]:
    """PRD F2: billed distance (bill_data.total_trip_km, contract: cost.distance_km)
    vs. actual GPS distance (ride_data_trip.traveled_km, contract:
    trip.traveled_km), monetised at the rate actually billed
    (cost.cost_per_km). Only the overbilled direction (billed > traveled)
    counts as recoverable leakage, matching the PRD's "recover 8-12% of
    spend" framing -- underbilling isn't a dispute-log item.

    Broken down per-vendor (top `top_n` by total discrepancy, rest bucketed
    as "Other") rather than a single aggregate line -- the PRD's own framing
    for this number is "two vendors are responsible for the gap", so the
    chart needs to show which vendors, not just the trend (cost.vendor_id is
    a direct FK, no route/trip hop needed)."""
    contract = get_contract()
    cost = contract.entity("cost")
    trip = contract.entity("trip")
    vendor = contract.entity("vendor")
    with engine.begin() as conn:
        anchor = _max_date(conn, cost.table, cost.column("cost_date"), org_id, cost.column("org_id"))
        if anchor is None:
            return {"categories": [], "series": [{"name": "Billing Discrepancy (₹)", "data": []}]}
        since = anchor - timedelta(days=months * 31)
        rows = conn.execute(
            text(f"""
                SELECT date_trunc('month', c.{cost.column('cost_date')})::date AS m,
                       v.{vendor.column('name')} AS vendor_name,
                       SUM(GREATEST(c.{cost.column('distance_km')} - t.{trip.column('traveled_km')}, 0)
                           * c.{cost.column('cost_per_km')}) AS discrepancy
                FROM {cost.table} c
                JOIN {trip.table} t ON t.{trip.column('id')} = c.{cost.column('trip_id')}
                LEFT JOIN {vendor.table} v ON v.{vendor.column('id')} = c.{cost.column('vendor_id')}
                WHERE c.{cost.column('org_id')} = :org_id
                  AND c.{cost.column('cost_date')} > :since
                  AND c.{cost.column('distance_km')} IS NOT NULL
                  AND t.{trip.column('traveled_km')} IS NOT NULL
                  AND c.{cost.column('cost_per_km')} IS NOT NULL
                GROUP BY m, v.{vendor.column('name')}
                ORDER BY m
            """),
            {"org_id": org_id, "since": since},
        ).mappings().all()

    months_list: list[date] = sorted({row["m"] for row in rows})
    totals_by_vendor: dict[str, float] = {}
    by_month_vendor: dict[tuple[date, str], float] = {}
    for row in rows:
        name = row["vendor_name"] or "Unattributed"
        amount = float(row["discrepancy"] or 0.0)
        by_month_vendor[(row["m"], name)] = amount
        totals_by_vendor[name] = totals_by_vendor.get(name, 0.0) + amount

    ranked = sorted(totals_by_vendor.items(), key=lambda kv: kv[1], reverse=True)
    top_vendors = [name for name, _ in ranked[:top_n]]
    grand_total = sum(totals_by_vendor.values())

    categories = [m.strftime("%b %Y") for m in months_list]
    series = [
        {
            "name": name,
            "data": [round(by_month_vendor.get((m, name), 0.0), 2) for m in months_list],
        }
        for name in top_vendors
    ]
    other_total = grand_total - sum(totals_by_vendor.get(name, 0.0) for name in top_vendors)
    if other_total > 0.01:
        other_names = [name for name in totals_by_vendor if name not in top_vendors]
        series.append(
            {
                "name": "Other vendors",
                "data": [
                    round(sum(by_month_vendor.get((m, name), 0.0) for name in other_names), 2)
                    for m in months_list
                ],
            }
        )

    contributors = [
        {
            "name": name,
            "value": round(total, 2),
            "pct": round(100.0 * total / grand_total, 1) if grand_total else 0.0,
        }
        for name, total in ranked[:3]
    ]

    return {"categories": categories, "series": series, "contributors": contributors}


def emissions_by_fuel(engine: Engine, org_id: str, days: int = 90) -> dict[str, Any]:
    """PRD F5: weekly CO2 (tonnes) stacked by actual_cab_fuel_type (Diesel/
    Petrol/Electric), using the emission coefficients already baked into
    mis.emission.co2_grams at ingestion time (170/150/0 gCO2/km per the PRD).

    The stacked series is a fleet-wide tonnes total (right unit for "how much
    CO2 did we emit"), while the curated `carbon_gco2_per_passenger_km`
    benchmark is a per-passenger-km *rate* -- the two aren't the same unit, so
    rather than draw a dimensionally-meaningless 82 line across a tonnes
    axis, the window's actual fleet-average rate (already precomputed
    per-row in emission.co2_per_passenger_km) is surfaced alongside the chart
    as its own comparison against that same 82 gCO2/pkm ICE baseline."""
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

        rate_row = conn.execute(
            text(f"""
                SELECT AVG(e.{emission.column('co2_per_passenger_km')}) AS avg_rate
                FROM {emission.table} e
                WHERE e.{emission.column('org_id')} = :org_id
                  AND e.{emission.column('log_date')} > :since
                  AND e.{emission.column('co2_per_passenger_km')} IS NOT NULL
            """),
            {"org_id": org_id, "since": since},
        ).mappings().first()

        benchmark = _benchmark(conn, org_id, "carbon_gco2_per_passenger_km")

    weeks: list[date] = sorted({row["wk"] for row in rows})
    fuels: list[str] = sorted({row["fuel"] for row in rows})
    by_week_fuel = {(row["wk"], row["fuel"]): float(row["tonnes"]) for row in rows}

    categories = [wk.isoformat() for wk in weeks]
    series = [
        {"name": fuel, "data": [round(by_week_fuel.get((wk, fuel), 0.0), 3) for wk in weeks]}
        for fuel in fuels
    ]

    result: dict[str, Any] = {"categories": categories, "series": series}
    if benchmark is not None:
        target = float(benchmark["target_value"])
        result["target"] = target
        result["breach_threshold"] = float(benchmark["threshold_value"]) if benchmark["threshold_value"] is not None else None
        result["target_label"] = f"ICE baseline ({target:.0f} gCO2/pkm)"
        if rate_row and rate_row["avg_rate"] is not None:
            avg_rate = round(float(rate_row["avg_rate"]), 1)
            result["comparison"] = {
                "label": "fleet avg gCO2/passenger-km vs ICE baseline",
                "current_value": avg_rate,
                "previous_value": target,
                "delta_pct": round((avg_rate - target) / target * 100.0, 1) if target else 0.0,
            }
    return result


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

        # Windowed on-time % per vendor for the current period and the
        # equal-length period before it -- distinct from `sla_pct` above
        # (the static, ingestion-time observed value); this pair is what
        # drives the scorecard's "vs previous period" delta so a viewer sees
        # whether a vendor is trending, not just its current standing.
        current_pct_by_vendor: dict[int, float] = {}
        prev_pct_by_vendor: dict[int, float] = {}
        if vendor_ids and anchor:
            prev_since = since - timedelta(days=days)
            windowed_rows = conn.execute(
                text(f"""
                    SELECT r.{route.column('vendor_id')} AS vendor_id,
                           (t.{trip.column('trip_date')} > :since) AS is_current,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE {_delay_expr('t', trip)} <= :breach) AS ontime
                    FROM {trip.table} t
                    JOIN {route.table} r ON r.{route.column('id')} = t.{trip.column('route_id')}
                    WHERE t.{trip.column('org_id')} = :org_id
                      AND t.{trip.column('status')} = 'completed'
                      AND t.{trip.column('actual_time')} IS NOT NULL
                      AND t.{trip.column('scheduled_time')} IS NOT NULL
                      AND t.{trip.column('trip_date')} > :prev_since
                      AND r.{route.column('vendor_id')} = ANY(:vendor_ids)
                    GROUP BY r.{route.column('vendor_id')}, is_current
                """),
                {
                    "org_id": org_id,
                    "prev_since": prev_since,
                    "since": since,
                    "breach": DELAY_BREACH_MINUTES,
                    "vendor_ids": vendor_ids,
                },
            ).mappings().all()
            for row in windowed_rows:
                if not row["total"]:
                    continue
                pct = round(100.0 * row["ontime"] / row["total"], 1)
                (current_pct_by_vendor if row["is_current"] else prev_pct_by_vendor)[row["vendor_id"]] = pct

    vendors = [
        {
            "vendor": row["name"],
            "sla_pct": round(float(row["sla_pct"]), 1) if row["sla_pct"] is not None else 0.0,
            "cost_per_km": round(float(row["cost_per_km"]), 2) if row["cost_per_km"] is not None else 0.0,
            "incident_count": int(row["incident_count"] or 0),
            "sla_trend": sparkline_by_vendor.get(row["vendor_id"], []),
            "ontime_pct_current": current_pct_by_vendor.get(row["vendor_id"]),
            "ontime_pct_prev": prev_pct_by_vendor.get(row["vendor_id"]),
        }
        for row in main_rows
    ]
    return {"vendors": vendors}
