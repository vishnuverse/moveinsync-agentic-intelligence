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
# Imported, not re-declared: escort_compliance_trend below must define
# "late night" exactly as detect_escort_compliance_signal does, or the Head's
# compliance trend and the Transport Manager's violation alerts would drift
# apart on the same underlying trips.
from app.graph.sense.nodes import NIGHT_WINDOW_END_HOUR, NIGHT_WINDOW_START_HOUR
from app.services.date_window import dense_anchor_date

# SP-B (plan §2d): this used to be a second, independent hardcoded copy of
# sense/nodes.py's DEFAULT_DELAY_BREACH_MINUTES -- a Settings-page change to
# the delay_breach threshold would silently leave every chart still drawing
# its breach line at 15 minutes while the detector itself used the new
# value. DEFAULT_DELAY_BREACH_MINUTES is now the fallback default only;
# _resolve_delay_breach_minutes() below reads the same `alert_rules` row the
# detector reads (via the same 30s-TTL-cached loader), so charts and
# detectors can never disagree again.
DEFAULT_DELAY_BREACH_MINUTES = 15.0
NODELAY_LABEL = "NODELAY"
# Mirrors sense/nodes.py's own constant of the same name/value (the
# fallback used only when sustainability_targets has no row for this org --
# both modules read the same real target when one exists).
DEFAULT_ICE_BASELINE_GCO2_PER_PAX_KM = 82.0


def _resolve_delay_breach_minutes(engine: Engine, org_id: str) -> float:
    from app.rules import get_rules

    rules = get_rules(engine, org_id).get("delay_breach")
    if rules is None:
        return DEFAULT_DELAY_BREACH_MINUTES
    return float(rules.get("delay_threshold_minutes", DEFAULT_DELAY_BREACH_MINUTES))


def _delay_expr(trip_alias: str, trip: Any) -> str:
    return (
        f"EXTRACT(EPOCH FROM ({trip_alias}.{trip.column('actual_time')} - "
        f"{trip_alias}.{trip.column('scheduled_time')})) / 60.0"
    )


def _max_date(conn, table: str, date_col: str, org_id: str, org_col: str) -> date | None:
    """BUGFIX: was a literal MAX(date) -- broke once a sparse live-replay
    trickle became every org's newest row, landing every "last N days"
    window in the empty gap before it (see date_window.py's docstring).
    Now anchors on the most recent date with real volume instead."""
    return dense_anchor_date(conn, table, date_col, org_id, org_col)


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


def ota_trend(
    engine: Engine, org_id: str, days: int = 45, *, since: date | None = None, until: date | None = None
) -> dict[str, Any]:
    """Daily on-time-arrival rate: PRD F4/§5 -- on-time = actual_time within
    the 15-minute breach threshold (same threshold detect_delay_signal uses).

    Also surfaces the curated `sla_timeliness_pct` benchmark (95% target /
    92% breach floor) and a vs-previous-window comparison, so the chart reads
    as "78% against a 95% target, two vendors behind it" rather than a bare
    trend line -- the same "context, not just a number" bar the metric cards
    already clear (dashboard_cards.py's context_note).

    `since`/`until` (plan: sliding date-range picker) override the
    days-back-from-anchor default entirely -- when given, they ARE the
    window, and the "vs previous period" comparison mirrors that same
    window's length immediately before it."""
    contract = get_contract()
    trip = contract.entity("trip")
    breach_minutes = _resolve_delay_breach_minutes(engine, org_id)
    with engine.begin() as conn:
        if until is not None:
            anchor = until
        else:
            anchor = _max_date(conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id"))
            if anchor is None:
                return {"categories": [], "series": [{"name": "On-Time Arrival %", "data": []}]}
        window_since = since if since is not None else anchor - timedelta(days=days)
        prev_since = window_since - (anchor - window_since)
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
                  AND t.{trip.column('trip_date')} <= :until
                GROUP BY t.{trip.column('trip_date')}
                ORDER BY t.{trip.column('trip_date')}
            """),
            {"org_id": org_id, "since": window_since, "until": anchor, "breach": breach_minutes},
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
            {"org_id": org_id, "prev_since": prev_since, "since": window_since, "breach": breach_minutes},
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


def delay_reason_breakdown(
    engine: Engine, org_id: str, days: int = 90, *, since: date | None = None, until: date | None = None
) -> dict[str, Any]:
    """PRD F4/§5 delay-reason breakdown (TRAFFIC/DRIVER/EMPLOYEE/...), with
    completed trips carrying no delay_reason bucketed as NODELAY."""
    contract = get_contract()
    trip = contract.entity("trip")
    with engine.begin() as conn:
        if until is not None:
            anchor = until
        else:
            anchor = _max_date(conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id"))
            if anchor is None:
                return {"categories": [], "series": [{"name": "Trips", "data": []}]}
        window_since = since if since is not None else anchor - timedelta(days=days)
        prev_since = window_since - (anchor - window_since)
        rows = conn.execute(
            text(f"""
                SELECT COALESCE(NULLIF(UPPER(TRIM(t.{trip.column('delay_reason')})), ''), :nodelay) AS reason,
                       COUNT(*) AS cnt
                FROM {trip.table} t
                WHERE t.{trip.column('org_id')} = :org_id
                  AND t.{trip.column('status')} = 'completed'
                  AND t.{trip.column('trip_date')} > :since
                  AND t.{trip.column('trip_date')} <= :until
                GROUP BY reason
                ORDER BY cnt DESC
                LIMIT 8
            """),
            {"org_id": org_id, "since": window_since, "until": anchor, "nodelay": NODELAY_LABEL},
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
            {"org_id": org_id, "prev_since": prev_since, "since": window_since},
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


def no_show_trend(
    engine: Engine, org_id: str, days: int = 45, *, since: date | None = None, until: date | None = None
) -> dict[str, Any]:
    """PRD F3 team no-show rate trend (Line Manager)."""
    contract = get_contract()
    commute = contract.entity("commute")
    with engine.begin() as conn:
        if until is not None:
            anchor = until
        else:
            anchor = _max_date(conn, commute.table, commute.column("log_date"), org_id, commute.column("org_id"))
            if anchor is None:
                return {"categories": [], "series": [{"name": "No-Show Rate %", "data": []}]}
        window_since = since if since is not None else anchor - timedelta(days=days)
        prev_since = window_since - (anchor - window_since)
        rows = conn.execute(
            text(f"""
                SELECT c.{commute.column('log_date')} AS d,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE c.{commute.column('is_no_show')} = TRUE) AS no_shows
                FROM {commute.table} c
                WHERE c.{commute.column('org_id')} = :org_id
                  AND c.{commute.column('log_date')} > :since
                  AND c.{commute.column('log_date')} <= :until
                GROUP BY c.{commute.column('log_date')}
                ORDER BY c.{commute.column('log_date')}
            """),
            {"org_id": org_id, "since": window_since, "until": anchor},
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
            {"org_id": org_id, "prev_since": prev_since, "since": window_since},
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


def absence_split(
    engine: Engine, org_id: str, days: int = 90, *, since: date | None = None, until: date | None = None
) -> dict[str, Any]:
    """PRD F3 delay-caused vs. employee-caused no-show split -- mirrors
    detect_attendance_correlation's own transport-correlation test exactly
    (mode IN ('shuttle','cab') AND delay past the same breach threshold),
    applied here to no-show legs instead of late-clock-in legs."""
    contract = get_contract()
    commute = contract.entity("commute")
    trip = contract.entity("trip")
    breach_minutes = _resolve_delay_breach_minutes(engine, org_id)
    with engine.begin() as conn:
        if until is not None:
            anchor = until
        else:
            anchor = _max_date(conn, commute.table, commute.column("log_date"), org_id, commute.column("org_id"))
            if anchor is None:
                return {"series": [{"name": "No-Shows", "data": []}]}
        window_since = since if since is not None else anchor - timedelta(days=days)
        row = conn.execute(
            text(f"""
                SELECT
                    COUNT(*) AS total_no_shows,
                    COUNT(*) FILTER (
                        -- BUGFIX: verified live some orgs' no-shows are 100%
                        -- mode='cab' with zero 'shuttle' rows (e.g.
                        -- vanta-Aus) -- a bare `mode = 'shuttle'` filter
                        -- silently zeroed "Delay-Caused" on the Line
                        -- Manager's Team Commute Overview chart for those
                        -- orgs regardless of real delay data. Both real
                        -- mis.commute.mode values are company-provided
                        -- transport, so both belong here -- same fix as
                        -- sense/nodes.py::detect_attendance_correlation.
                        WHERE c.{commute.column('mode')} IN ('shuttle', 'cab')
                          AND t.{trip.column('actual_time')} IS NOT NULL
                          AND t.{trip.column('scheduled_time')} IS NOT NULL
                          AND {_delay_expr('t', trip)} > :breach
                    ) AS delay_caused
                FROM {commute.table} c
                LEFT JOIN {trip.table} t ON t.{trip.column('id')} = c.{commute.column('trip_id')}
                WHERE c.{commute.column('org_id')} = :org_id
                  AND c.{commute.column('is_no_show')} = TRUE
                  AND c.{commute.column('log_date')} > :since
                  AND c.{commute.column('log_date')} <= :until
            """),
            {"org_id": org_id, "since": window_since, "until": anchor, "breach": breach_minutes},
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


def billing_discrepancy(
    engine: Engine,
    org_id: str,
    months: int = 6,
    top_n: int = 4,
    *,
    since: date | None = None,
    until: date | None = None,
) -> dict[str, Any]:
    """PRD F2 billing-slab leakage, same basis as detect_billing_discrepancy_signal
    (sense/nodes.py): a trip billed under a pricier slab than its ACTUAL
    traveled_km (trip.traveled_km) warrants. The raw billed-distance column
    (cost.distance_km / bill_data.total_trip_km) is empty for ~all real rows,
    so a distance-difference metric is structurally zero -- the real leakage
    is slab-tier mispricing. Overbilling per trip = trip_cost minus the
    "calculated slab cost" (avg trip_cost of correctly-slabbed trips of that
    length), overbilled direction only, matching the PRD's "recover 8-12% of
    spend" framing.

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
        if until is not None:
            anchor = until
        else:
            anchor = _max_date(conn, cost.table, cost.column("cost_date"), org_id, cost.column("org_id"))
            if anchor is None:
                return {"categories": [], "series": [{"name": "Billing Discrepancy (₹)", "data": []}]}
        window_since = since if since is not None else anchor - timedelta(days=months * 31)
        # Slab-tier leakage (PRD F2), mirroring detect_billing_discrepancy_signal
        # in sense/nodes.py: the raw billed-distance column (cost.distance_km /
        # bill_data.total_trip_km) is empty (<=0.1) for ~all real rows, so a
        # "billed_km - gps_km" metric is structurally 0. Real overbilling is a
        # trip billed under a pricier slab than its ACTUAL traveled_km warrants.
        # Per-org slab->distance bands are derived empirically (median traveled_km
        # per slab, boundary at the midpoint of adjacent medians); "calculated
        # slab cost" is the avg trip_cost of correctly-slabbed trips of that
        # length; overbilling = trip_cost - calculated_slab_cost, overbilled
        # direction only, aggregated per month per vendor.
        rows = conn.execute(
            text(f"""
                WITH billed AS (
                    SELECT c.{cost.column('id')} AS cost_id,
                           v.{vendor.column('name')} AS vendor_name,
                           c.{cost.column('slab_name')} AS slab_name,
                           c.{cost.column('amount')} AS trip_cost,
                           t.{trip.column('traveled_km')} AS traveled_km,
                           date_trunc('month', c.{cost.column('cost_date')})::date AS m
                    FROM {cost.table} c
                    JOIN {trip.table} t ON t.{trip.column('id')} = c.{cost.column('trip_id')}
                    LEFT JOIN {vendor.table} v ON v.{vendor.column('id')} = c.{cost.column('vendor_id')}
                    WHERE c.{cost.column('org_id')} = :org_id
                      AND c.{cost.column('cost_date')} > :since
                      AND c.{cost.column('cost_date')} <= :until
                      AND c.{cost.column('slab_name')} IS NOT NULL AND trim(c.{cost.column('slab_name')}) <> ''
                      AND t.{trip.column('traveled_km')} IS NOT NULL
                      AND c.{cost.column('amount')} IS NOT NULL
                ),
                slab_bands AS (
                    SELECT slab_name, percentile_cont(0.5) WITHIN GROUP (ORDER BY traveled_km) AS median_km
                    FROM billed GROUP BY slab_name HAVING COUNT(*) >= :min_slab_sample
                ),
                slab_bounds AS (
                    SELECT slab_name, median_km,
                           LAG(median_km) OVER (ORDER BY median_km) AS prev_median,
                           LEAD(median_km) OVER (ORDER BY median_km) AS next_median
                    FROM slab_bands
                ),
                slab_ranges AS (
                    SELECT slab_name,
                           COALESCE((prev_median + median_km) / 2.0, 0.0) AS lower_km,
                           COALESCE((median_km + next_median) / 2.0, median_km * 2 + 1000) AS upper_km
                    FROM slab_bounds
                ),
                correct_slab AS (
                    SELECT b.cost_id, r.slab_name AS correct_slab_name
                    FROM billed b JOIN slab_ranges r
                      ON b.traveled_km >= r.lower_km AND b.traveled_km < r.upper_km
                ),
                slab_expected_cost AS (
                    SELECT b.slab_name, AVG(b.trip_cost) AS avg_cost_for_slab
                    FROM billed b JOIN slab_ranges r ON r.slab_name = b.slab_name
                    WHERE b.traveled_km >= r.lower_km AND b.traveled_km < r.upper_km
                    GROUP BY b.slab_name HAVING COUNT(*) >= :min_slab_sample
                )
                SELECT b.m AS m,
                       b.vendor_name AS vendor_name,
                       SUM(GREATEST(b.trip_cost - sec.avg_cost_for_slab, 0)) AS discrepancy
                FROM billed b
                JOIN correct_slab cs ON cs.cost_id = b.cost_id
                JOIN slab_expected_cost sec ON sec.slab_name = cs.correct_slab_name
                WHERE cs.correct_slab_name <> b.slab_name
                GROUP BY b.m, b.vendor_name
                ORDER BY b.m
            """),
            {"org_id": org_id, "since": window_since, "until": anchor, "min_slab_sample": 30},
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


def emissions_by_fuel(
    engine: Engine, org_id: str, days: int = 90, *, since: date | None = None, until: date | None = None
) -> dict[str, Any]:
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
        if until is not None:
            anchor = until
        else:
            anchor = _max_date(conn, emission.table, emission.column("log_date"), org_id, emission.column("org_id"))
            if anchor is None:
                return {"categories": [], "series": []}
        window_since = since if since is not None else anchor - timedelta(days=days)
        rows = conn.execute(
            text(f"""
                SELECT date_trunc('week', e.{emission.column('log_date')})::date AS wk,
                       e.{emission.column('fuel_type')} AS fuel,
                       SUM(e.{emission.column('co2_grams')}) / 1000000.0 AS tonnes
                FROM {emission.table} e
                WHERE e.{emission.column('org_id')} = :org_id
                  AND e.{emission.column('log_date')} > :since
                  AND e.{emission.column('log_date')} <= :until
                  AND e.{emission.column('fuel_type')} IS NOT NULL
                GROUP BY wk, fuel
                ORDER BY wk
            """),
            {"org_id": org_id, "since": window_since, "until": anchor},
        ).mappings().all()

        rate_row = conn.execute(
            text(f"""
                SELECT AVG(e.{emission.column('co2_per_passenger_km')}) AS avg_rate
                FROM {emission.table} e
                WHERE e.{emission.column('org_id')} = :org_id
                  AND e.{emission.column('log_date')} > :since
                  AND e.{emission.column('log_date')} <= :until
                  AND e.{emission.column('co2_per_passenger_km')} IS NOT NULL
            """),
            {"org_id": org_id, "since": window_since, "until": anchor},
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


def escort_compliance_trend(
    engine: Engine, org_id: str, days: int = 90, *, since: date | None = None, until: date | None = None
) -> dict[str, Any]:
    """PRD F1, Transport Head view: weekly fleet-wide late-night female escort
    compliance %, against the 100% policy target.

    Deliberately the SAME population `detect_escort_compliance_signal`
    (app/graph/sense/nodes.py) alerts on -- trip JOIN commute JOIN employee,
    `gender = 'FEMALE'`, `actual_departure` inside the 21:00-06:00 window --
    so the Head's trend line and the Transport Manager's individual violation
    alerts can never disagree about what a violation is. The one difference is
    deliberate: the detector runs each `trip_direction` leg as its own
    sub-detection, while this aggregates both legs, because the strategic
    question is "how exposed are we overall", not "which leg failed".

    Compliance is expressed as the share of those trips that DID carry an
    escort, so the line reads the intuitive way round: up is good, and the
    gap to 100 is the exposure.
    """
    contract = get_contract()
    trip = contract.entity("trip")
    commute = contract.entity("commute")
    employee = contract.entity("employee")

    with engine.begin() as conn:
        if until is not None:
            anchor = until
        else:
            anchor = dense_anchor_date(
                conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id")
            )
            if anchor is None:
                return {"categories": [], "series": []}
        window_since = since if since is not None else anchor - timedelta(days=days)
        prev_since = window_since - (anchor - window_since)

        night = (
            f"(EXTRACT(HOUR FROM t.{trip.column('actual_departure')}) >= {NIGHT_WINDOW_START_HOUR}"
            f" OR EXTRACT(HOUR FROM t.{trip.column('actual_departure')}) < {NIGHT_WINDOW_END_HOUR})"
        )
        base_from = f"""
            FROM {trip.table} t
            JOIN {commute.table} c ON c.{commute.column('trip_id')} = t.{trip.column('id')}
            JOIN {employee.table} e ON e.{employee.column('id')} = c.{commute.column('employee_id')}
            WHERE t.{trip.column('org_id')} = :org_id
              AND e.{employee.column('gender')} = 'FEMALE'
              AND t.{trip.column('actual_departure')} IS NOT NULL
              AND t.{trip.column('trip_date')} > :since
              AND t.{trip.column('trip_date')} <= :until
              AND {night}
        """

        rows = conn.execute(
            text(f"""
                SELECT date_trunc('week', t.{trip.column('trip_date')})::date AS wk,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE t.{trip.column('actual_escort')}) AS escorted
                {base_from}
                GROUP BY wk
                ORDER BY wk
            """),
            {"org_id": org_id, "since": window_since, "until": anchor},
        ).mappings().all()

        def _window_pct(w_since: date, w_until: date) -> tuple[float | None, int, int]:
            row = conn.execute(
                text(f"""
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE t.{trip.column('actual_escort')}) AS escorted
                    {base_from}
                """),
                {"org_id": org_id, "since": w_since, "until": w_until},
            ).mappings().first()
            total = int(row["total"] or 0) if row else 0
            escorted = int(row["escorted"] or 0) if row else 0
            if total == 0:
                return None, 0, 0
            return round(100.0 * escorted / total, 1), total, total - escorted

        current_pct, current_total, current_unescorted = _window_pct(window_since, anchor)
        prev_pct, _, _ = _window_pct(prev_since, window_since)

    categories = [row["wk"].isoformat() for row in rows]
    data = [
        round(100.0 * int(row["escorted"] or 0) / int(row["total"]), 1) if int(row["total"] or 0) else 0.0
        for row in rows
    ]

    result: dict[str, Any] = {
        "categories": categories,
        "series": [{"name": "Escort compliance %", "data": data}],
        "target": 100.0,
        "target_label": "Policy target (100%)",
    }
    if current_pct is not None:
        result["summary"] = (
            f"{current_unescorted:,} of {current_total:,} late-night female trips ran unescorted"
        )
        if prev_pct is not None:
            result["comparison"] = {
                "label": f"vs previous {days}d",
                "current_value": current_pct,
                "previous_value": prev_pct,
                "delta_pct": round(current_pct - prev_pct, 1),
            }
    return result


def vendor_scorecard(
    engine: Engine,
    org_id: str,
    days: int = 90,
    limit: int = 12,
    *,
    since: date | None = None,
    until: date | None = None,
) -> dict[str, Any]:
    """PRD TH1: SLA% / cost-per-km / incident count per vendor, plus a
    weekly on-time-rate sparkline. sla_target_pct and cost_per_km are the
    observed values computed once at ingestion (see mis_schema.sql); the
    incident count and sparkline are windowed here."""
    contract = get_contract()
    vendor = contract.entity("vendor")
    route = contract.entity("route")
    incident = contract.entity("incident")
    trip = contract.entity("trip")
    breach_minutes = _resolve_delay_breach_minutes(engine, org_id)

    with engine.begin() as conn:
        anchor = until if until is not None else _max_date(
            conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id")
        )
        # A concrete sentinel (rather than a NULL bind + "::date IS NULL"
        # check) sidesteps a SQLAlchemy text() parsing quirk where a bind
        # param immediately followed by a "::cast" and reused later in the
        # same statement doesn't get substituted correctly.
        if anchor is None:
            since = date(1970, 1, 1)
        else:
            since = since if since is not None else anchor - timedelta(days=days)

        main_rows = conn.execute(
            text(f"""
                SELECT v.{vendor.column('id')} AS vendor_id,
                       v.{vendor.column('name')} AS name,
                       v.{vendor.column('sla_target_pct')} AS sla_pct,
                       v.{vendor.column('cost_per_km')} AS cost_per_km,
                       COUNT(i.{incident.column('id')}) AS incident_count
                FROM {vendor.table} v
                LEFT JOIN {trip.table} t ON t.{trip.column('vendor_id')} = v.{vendor.column('id')}
                    AND t.{trip.column('org_id')} = :org_id
                LEFT JOIN {incident.table} i ON i.{incident.column('trip_id')} = t.{trip.column('id')}
                    AND i.{incident.column('org_id')} = :org_id
                    AND i.{incident.column('occurred_at')} > :since
                    AND i.{incident.column('occurred_at')} <= :until_bound
                WHERE v.{vendor.column('org_id')} = :org_id
                GROUP BY v.{vendor.column('id')}, v.{vendor.column('name')}, v.{vendor.column('sla_target_pct')}, v.{vendor.column('cost_per_km')}
                ORDER BY v.{vendor.column('sla_target_pct')} DESC NULLS LAST
                LIMIT :limit
            """),
            {"org_id": org_id, "since": since, "until_bound": anchor or date(2999, 1, 1), "limit": limit},
        ).mappings().all()

        vendor_ids = [row["vendor_id"] for row in main_rows]
        sparkline_by_vendor: dict[int, list[float]] = {vid: [] for vid in vendor_ids}
        if vendor_ids and anchor:
            spark_rows = conn.execute(
                text(f"""
                    SELECT t.{trip.column('vendor_id')} AS vendor_id,
                           date_trunc('week', t.{trip.column('trip_date')})::date AS wk,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE {_delay_expr('t', trip)} <= :breach) AS ontime
                    FROM {trip.table} t
                    WHERE t.{trip.column('org_id')} = :org_id
                      AND t.{trip.column('status')} = 'completed'
                      AND t.{trip.column('actual_time')} IS NOT NULL
                      AND t.{trip.column('scheduled_time')} IS NOT NULL
                      AND t.{trip.column('trip_date')} > :since
                      AND t.{trip.column('trip_date')} <= :until_bound
                      AND t.{trip.column('vendor_id')} = ANY(:vendor_ids)
                    GROUP BY t.{trip.column('vendor_id')}, wk
                    ORDER BY t.{trip.column('vendor_id')}, wk
                """),
                {
                    "org_id": org_id,
                    "since": since,
                    "until_bound": anchor,
                    "breach": breach_minutes,
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
                    SELECT t.{trip.column('vendor_id')} AS vendor_id,
                           (t.{trip.column('trip_date')} > :since) AS is_current,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE {_delay_expr('t', trip)} <= :breach) AS ontime
                    FROM {trip.table} t
                    WHERE t.{trip.column('org_id')} = :org_id
                      AND t.{trip.column('status')} = 'completed'
                      AND t.{trip.column('actual_time')} IS NOT NULL
                      AND t.{trip.column('scheduled_time')} IS NOT NULL
                      AND t.{trip.column('trip_date')} > :prev_since
                      AND t.{trip.column('trip_date')} <= :until_bound
                      AND t.{trip.column('vendor_id')} = ANY(:vendor_ids)
                    GROUP BY t.{trip.column('vendor_id')}, is_current
                """),
                {
                    "org_id": org_id,
                    "prev_since": prev_since,
                    "since": since,
                    "until_bound": anchor,
                    "breach": breach_minutes,
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


def hotspot_timeline(
    engine: Engine, org_id: str, days: int = 90, *, since: date | None = None, until: date | None = None
) -> dict[str, Any]:
    """Daily count of Major Risk Hotspot events (plan SP-B §B0: unescorted
    late-night female trips -- both drop and pickup legs -- plus incidents
    at critical/high severity), for the dashboard's colored hotspot timeline
    (click a day/range to set the date-range picker to it).

    Deliberately grounded in each event's own real date
    (mis.trip.trip_date / mis.incident.occurred_at), not
    agent_notifications.created_at -- the latter only reflects when the demo
    pipeline happened to process an already-months-old trip, not when the
    underlying safety event actually occurred, so it would draw a timeline
    that's almost entirely about "today" instead of the real historical
    pattern a viewer is trying to explore. Same join shape
    detect_escort_compliance_signal uses (trip -> commute -> employee),
    just grouped by day across a bounded window instead of capped at
    `violation_limit` and scanning unbounded history."""
    contract = get_contract()
    trip = contract.entity("trip")
    commute = contract.entity("commute")
    employee = contract.entity("employee")
    incident = contract.entity("incident")

    with engine.begin() as conn:
        if until is not None:
            anchor = until
        else:
            anchor = dense_anchor_date(conn, trip.table, trip.column("trip_date"), org_id, trip.column("org_id"))
            if anchor is None:
                return {"days": []}
        window_since = since if since is not None else anchor - timedelta(days=days)

        escort_rows = conn.execute(
            text(f"""
                SELECT t.{trip.column('trip_date')} AS d, COUNT(*) AS n
                FROM {trip.table} t
                JOIN {commute.table} c ON c.{commute.column('trip_id')} = t.{trip.column('id')}
                JOIN {employee.table} e ON e.{employee.column('id')} = c.{commute.column('employee_id')}
                WHERE t.{trip.column('org_id')} = :org_id
                  AND e.{employee.column('gender')} = 'FEMALE'
                  AND t.{trip.column('actual_escort')} = FALSE
                  AND t.{trip.column('actual_departure')} IS NOT NULL
                  AND (EXTRACT(HOUR FROM t.{trip.column('actual_departure')}) >= 21
                       OR EXTRACT(HOUR FROM t.{trip.column('actual_departure')}) < 6)
                  AND t.{trip.column('trip_date')} > :since
                  AND t.{trip.column('trip_date')} <= :until
                GROUP BY d
            """),
            {"org_id": org_id, "since": window_since, "until": anchor},
        ).mappings().all()

        incident_rows = conn.execute(
            text(f"""
                SELECT i.{incident.column('occurred_at')}::date AS d,
                       i.{incident.column('severity')} AS severity,
                       COUNT(*) AS n
                FROM {incident.table} i
                WHERE i.{incident.column('org_id')} = :org_id
                  AND i.{incident.column('occurred_at')}::date > :since
                  AND i.{incident.column('occurred_at')}::date <= :until
                  AND i.{incident.column('severity')} IN ('critical', 'high')
                GROUP BY d, i.{incident.column('severity')}
            """),
            {"org_id": org_id, "since": window_since, "until": anchor},
        ).mappings().all()

    by_day: dict[date, dict[str, int]] = {}

    def bump(d: date, key: str, n: int) -> None:
        row = by_day.setdefault(d, {"escort_violations": 0, "critical_incidents": 0, "high_incidents": 0})
        row[key] += n

    for row in escort_rows:
        bump(row["d"], "escort_violations", int(row["n"]))
    for row in incident_rows:
        key = "critical_incidents" if row["severity"] == "critical" else "high_incidents"
        bump(row["d"], key, int(row["n"]))

    days_out = [
        {
            "date": d.isoformat(),
            "escort_violations": counts["escort_violations"],
            "critical_incidents": counts["critical_incidents"],
            "high_incidents": counts["high_incidents"],
        }
        for d, counts in sorted(by_day.items())
    ]
    return {"days": days_out, "window_since": str(window_since), "window_until": str(anchor)}


def signal_timeline(
    engine: Engine, org_id: str, persona: str, days: int = 90, *, since: date | None = None, until: date | None = None
) -> dict[str, Any]:
    """Line Manager / Transport Head's own analog of `hotspot_timeline` --
    same generic shape ({date, primary_count, marker_count}), grounded in
    each persona's own routed signal types (plan §A's domain-scope table)
    rather than a fabricated one-size-fits-all metric:

    - line_manager: `primary_count` = total late-attendance marks that day,
      `marker_count` = 1 on a day where the MAJORITY of that day's lates
      correlate with a real transport delay (same join/filter
      detect_attendance_correlation uses) -- "this was a shuttle-caused
      lateness day," not just a generic busy day.
    - transport_head: `primary_count` = overbilled INR that day (same
      formula billing_discrepancy uses, day-grained instead of month-
      grained), `marker_count` = count of individual trips whose
      co2_per_passenger_km exceeded the sustainability baseline that day
      (a real, naturally day-groupable event, distinct from
      detect_emissions_signal's own route-AVERAGE-over-the-whole-window
      check, which has no single-day granularity to plot).

    transport_manager is NOT handled here -- it keeps the richer,
    already-built `hotspot_timeline` (distinct critical/high incident
    counts, not collapsible into this simpler 2-field shape without losing
    real information)."""
    if persona == "line_manager":
        return _line_manager_timeline(engine, org_id, days, since=since, until=until)
    if persona == "transport_head":
        return _transport_head_timeline(engine, org_id, days, since=since, until=until)
    return {"days": [], "window_since": None, "window_until": None}


def _line_manager_timeline(
    engine: Engine, org_id: str, days: int, *, since: date | None, until: date | None
) -> dict[str, Any]:
    contract = get_contract()
    attendance = contract.entity("attendance")
    commute = contract.entity("commute")
    trip = contract.entity("trip")
    breach_minutes = _resolve_delay_breach_minutes(engine, org_id)

    with engine.begin() as conn:
        if until is not None:
            anchor = until
        else:
            anchor = dense_anchor_date(
                conn, attendance.table, attendance.column("work_date"), org_id, attendance.column("org_id")
            )
            if anchor is None:
                return {"days": [], "window_since": None, "window_until": None}
        window_since = since if since is not None else anchor - timedelta(days=days)

        rows = conn.execute(
            text(f"""
                SELECT a.{attendance.column('work_date')} AS d,
                       COUNT(*) AS late_count,
                       COUNT(*) FILTER (
                           WHERE c.{commute.column('mode')} IN ('shuttle', 'cab')
                             AND t.{trip.column('actual_time')} IS NOT NULL
                             AND t.{trip.column('scheduled_time')} IS NOT NULL
                             AND {_delay_expr('t', trip)} > :breach
                       ) AS transport_caused_count
                FROM {attendance.table} a
                LEFT JOIN {commute.table} c ON c.{commute.column('employee_id')} = a.{attendance.column('employee_id')}
                                             AND c.{commute.column('log_date')} = a.{attendance.column('work_date')}
                LEFT JOIN {trip.table} t ON t.{trip.column('id')} = c.{commute.column('trip_id')}
                WHERE a.{attendance.column('org_id')} = :org_id
                  AND a.{attendance.column('status')} = 'late'
                  AND a.{attendance.column('work_date')} > :since
                  AND a.{attendance.column('work_date')} <= :until
                GROUP BY d
            """),
            {"org_id": org_id, "since": window_since, "until": anchor, "breach": breach_minutes},
        ).mappings().all()

    days_out = []
    for row in rows:
        late_count = int(row["late_count"])
        transport_caused = int(row["transport_caused_count"] or 0)
        days_out.append(
            {
                "date": row["d"].isoformat(),
                "primary_count": late_count,
                "marker_count": 1 if late_count and transport_caused / late_count >= 0.5 else 0,
            }
        )
    days_out.sort(key=lambda r: r["date"])
    return {"days": days_out, "window_since": str(window_since), "window_until": str(anchor)}


def _transport_head_timeline(
    engine: Engine, org_id: str, days: int, *, since: date | None, until: date | None
) -> dict[str, Any]:
    """`primary_count` = total daily spend (mis.cost.amount, i.e.
    total_cost_inr) -- NOT the overbilled-distance formula billing_discrepancy
    uses, which depends on cost_per_km_inr; verified live that column is
    populated for only 23 of 70,946 vanta-Aus cost rows (0.03%, vs 99%+ for
    every other org -- a real, pre-existing data-ingestion gap, not
    something to route around silently: flagged separately for a fix).
    total_cost_inr has no such gap, so this stays meaningful for every org.

    `marker_count` = 1 on a day whose AVERAGE co2_per_passenger_km across
    all trips exceeds the sustainability baseline -- a day-level version of
    detect_emissions_signal's own "average over baseline" philosophy (that
    detector checks a route's average over the whole window; this checks a
    day's average across routes). Deliberately NOT "count of individual
    over-baseline trips" -- verified live that's ~44% of all trips on a
    typical day (a below-baseline org-wide average with high per-trip
    variance from a mixed EV/ICE fleet), so it would mark nearly every day
    and defeat the point of a marker."""
    contract = get_contract()
    cost = contract.entity("cost")
    emission = contract.entity("emission")

    with engine.begin() as conn:
        if until is not None:
            anchor = until
        else:
            anchor = dense_anchor_date(conn, cost.table, cost.column("cost_date"), org_id, cost.column("org_id"))
            if anchor is None:
                return {"days": [], "window_since": None, "window_until": None}
        window_since = since if since is not None else anchor - timedelta(days=days)

        cost_rows = conn.execute(
            text(f"""
                SELECT c.{cost.column('cost_date')} AS d, SUM(c.{cost.column('amount')}) AS total_inr
                FROM {cost.table} c
                WHERE c.{cost.column('org_id')} = :org_id
                  AND c.{cost.column('cost_date')} > :since
                  AND c.{cost.column('cost_date')} <= :until
                GROUP BY d
            """),
            {"org_id": org_id, "since": window_since, "until": anchor},
        ).mappings().all()

        baseline_row = conn.execute(
            text(
                "SELECT target_value, threshold_value FROM sustainability_targets "
                "WHERE org_id = :org_id AND metric_name = 'carbon_gco2_per_passenger_km' "
                "ORDER BY effective_from DESC LIMIT 1"
            ),
            {"org_id": org_id},
        ).mappings().first()
        baseline = (
            float(baseline_row["threshold_value"] or baseline_row["target_value"])
            if baseline_row is not None
            else DEFAULT_ICE_BASELINE_GCO2_PER_PAX_KM
        )

        emission_rows = conn.execute(
            text(f"""
                SELECT e.{emission.column('log_date')} AS d, AVG(e.{emission.column('co2_per_passenger_km')}) AS avg_co2
                FROM {emission.table} e
                WHERE e.{emission.column('org_id')} = :org_id
                  AND e.{emission.column('log_date')} > :since
                  AND e.{emission.column('log_date')} <= :until
                  AND e.{emission.column('co2_per_passenger_km')} IS NOT NULL
                GROUP BY d
            """),
            {"org_id": org_id, "since": window_since, "until": anchor},
        ).mappings().all()

    by_day: dict[date, dict[str, int]] = {}
    for row in cost_rows:
        by_day.setdefault(row["d"], {"total_inr": 0, "over_baseline": 0})["total_inr"] = round(
            float(row["total_inr"] or 0.0)
        )
    for row in emission_rows:
        entry = by_day.setdefault(row["d"], {"total_inr": 0, "over_baseline": 0})
        entry["over_baseline"] = 1 if row["avg_co2"] is not None and float(row["avg_co2"]) > baseline else 0

    days_out = [
        {"date": d.isoformat(), "primary_count": v["total_inr"], "marker_count": v["over_baseline"]}
        for d, v in sorted(by_day.items())
    ]
    return {"days": days_out, "window_since": str(window_since), "window_until": str(anchor)}


def signal_gate_funnel(engine: Engine, org_id: str, days: int = 30) -> dict[str, Any]:
    """SP-B §5/§6: daily count of suppress/rule_only/escalate gate decisions,
    stacked -- reuses BreakdownBarChart's existing `stacked` mode (already
    used for the per-vendor billing-discrepancy stack above), same pivot
    pattern emissions_by_fuel uses for its fuel-type stack."""
    gd = get_contract().entity("gate_decision")
    c = gd.column
    with engine.begin() as conn:
        anchor = conn.execute(
            text(f"SELECT MAX({c('created_at')})::date FROM {gd.table} WHERE {c('org_id')} = :org_id"),
            {"org_id": org_id},
        ).scalar()
        if anchor is None:
            return {"categories": [], "series": []}
        since = anchor - timedelta(days=days)
        rows = conn.execute(
            text(f"""
                SELECT {c('created_at')}::date AS d, {c('action')} AS action, COUNT(*) AS n
                FROM {gd.table}
                WHERE {c('org_id')} = :org_id AND {c('created_at')}::date > :since
                GROUP BY d, action
                ORDER BY d
            """),
            {"org_id": org_id, "since": since},
        ).mappings().all()

    dates = sorted({row["d"] for row in rows})
    categories = [d.isoformat() for d in dates]
    by_action_date: dict[tuple[str, date], int] = {}
    for row in rows:
        by_action_date[(row["action"], row["d"])] = int(row["n"])

    series = [
        {
            "name": label,
            "data": [by_action_date.get((action, d), 0) for d in dates],
        }
        for action, label in (("suppress", "Suppressed"), ("rule_only", "Rule-Only"), ("escalate", "Escalated"))
    ]
    return {"categories": categories, "series": series}


def llm_call_volume(engine: Engine, org_id: str, *, provider: str, redis_url: str, days: int = 14) -> dict[str, Any]:
    """SP-B §5/§6: reuses TrendLineChart's existing `target`/`breach_threshold`
    fields (already designed for exactly "actual vs. a line") for "daily LLM
    calls vs. LLM_DAILY_CALL_LIMIT". Redis only retains each day's key for
    ~26h (see app/llm/provider.py's _DAILY_KEY_TTL_SECONDS), so this combines
    today's live Redis count with a `gate_decisions`-derived historical
    `escalate` count (every escalate IS one LLM call) for real trend depth --
    documented honestly rather than faking multi-day Redis history that
    doesn't exist."""
    import os
    from datetime import timedelta as _td

    gd = get_contract().entity("gate_decision")
    c = gd.column
    with engine.begin() as conn:
        anchor = conn.execute(
            text(f"SELECT MAX({c('created_at')})::date FROM {gd.table} WHERE {c('org_id')} = :org_id"),
            {"org_id": org_id},
        ).scalar()
        since = (anchor - _td(days=days)) if anchor else (date.today() - _td(days=days))
        rows = conn.execute(
            text(f"""
                SELECT {c('created_at')}::date AS d, COUNT(*) AS n
                FROM {gd.table}
                WHERE {c('org_id')} = :org_id AND {c('action')} = 'escalate'
                  AND {c('created_at')}::date > :since
                GROUP BY d ORDER BY d
            """),
            {"org_id": org_id, "since": since},
        ).mappings().all()

    counts_by_date = {row["d"].isoformat(): int(row["n"]) for row in rows}
    today = date.today()
    categories = [(since + _td(days=i)).isoformat() for i in range((today - since).days + 1)]

    from app.llm.provider import get_daily_call_count

    live_count = get_daily_call_count(provider, redis_url)

    data = [live_count if cat == today.isoformat() else counts_by_date.get(cat, 0) for cat in categories]

    daily_limit = int(os.environ.get("LLM_DAILY_CALL_LIMIT", "500"))
    return {
        "categories": categories,
        "series": [{"name": "LLM calls (escalate-derived history + live today)", "data": data}],
        "breach_threshold": float(daily_limit),
        "target_label": "Daily budget",
    }
