"""Per-persona moving-average KPI rollups (plan SP-B §9b) -- deliberately
plain SQL aggregates (window functions, no LLM), grounded in each persona's
real domain scope (§A's table): Transport Manager (billing/on-time/no-shows/
glitches), Line Manager (team attendance), Transport Head (route
optimization/no-shows/cost). Every number is returned alongside its own
moving-average baseline so a card never shows a bare figure without context
("142, up 18% vs the 30-day average" rather than just "142").

Also backs the cost-optimization-for-a-window capability: `cost_optimization_outlook`
takes an explicit [since, until] window (not just a trailing-days lookback)
so a persona can ask "what did cost look like Jun 1-15" as well as "the last
30 days" -- the same function serves both a dashboard card and a future
date-range-scoped view.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Engine, text

from app.contracts import get_contract
from app.services.date_window import dense_anchor_date


def _pct_delta(current: float, baseline: float) -> float | None:
    if not baseline:
        return None
    return round((current - baseline) / baseline * 100.0, 1)


def _direction(pct: float | None) -> str:
    if pct is None or abs(pct) < 1.0:
        return "flat"
    return "up" if pct > 0 else "down"


def no_shows_today_and_week(engine: Engine, org_id: str, *, team_id: int | None = None) -> dict[str, Any]:
    """TM: org-wide no-shows. LM: pass `team_id` to scope to one team's
    Team Commute Overview.

    "Today"/"this week" stay anchored to the table's literal most-recent
    date -- that framing should be honest about the live-replay tail even
    when it's sparse. But the 30-day moving-average *baseline* used to judge
    whether today is normal is a separate anchor: BUGFIX (found live) -- it
    used to span the same `anchor.d - 30 days .. anchor.d` window as
    "today," which for an org whose most recent row is a sparse, disconnected
    live-replay day (observed: 3 rows, 36 days after the real historical
    bulk ends) meant the baseline averaged almost entirely empty days,
    producing a meaningless comparison. The baseline instead ends at
    `dense_anchor_date` -- the most recent date with real volume -- so
    "vs. the 30-day average" always compares against actual history."""
    contract = get_contract()
    commute = contract.entity("commute")
    employee = contract.entity("employee")
    c = commute.column

    team_join = ""
    team_filter = ""
    params: dict[str, Any] = {"org_id": org_id}
    if team_id is not None:
        emp = employee.table
        team_join = f"JOIN {emp} e ON e.{employee.column('id')} = c.{c('employee_id')}"
        team_filter = f"AND e.{employee.column('team_id')} = :team_id"
        params["team_id"] = team_id

    with engine.connect() as conn:
        baseline_anchor = dense_anchor_date(conn, commute.table, c("log_date"), org_id, c("org_id"))
        params["baseline_anchor"] = baseline_anchor

        sql = f"""
            WITH anchor AS (
                SELECT MAX({c('log_date')}) AS d FROM {commute.table} WHERE {c('org_id')} = :org_id
            ),
            recent AS (
                SELECT c.{c('log_date')} AS d, COUNT(*) FILTER (WHERE c.{c('is_no_show')}) AS no_shows
                FROM {commute.table} c
                {team_join}
                CROSS JOIN anchor
                WHERE c.{c('org_id')} = :org_id
                  AND c.{c('log_date')} > anchor.d - INTERVAL '7 days'
                  {team_filter}
                GROUP BY c.{c('log_date')}
            ),
            baseline AS (
                SELECT c.{c('log_date')} AS d, COUNT(*) FILTER (WHERE c.{c('is_no_show')}) AS no_shows
                FROM {commute.table} c
                {team_join}
                WHERE c.{c('org_id')} = :org_id
                  AND c.{c('log_date')} > CAST(:baseline_anchor AS date) - INTERVAL '30 days'
                  AND c.{c('log_date')} <= CAST(:baseline_anchor AS date)
                  {team_filter}
                GROUP BY c.{c('log_date')}
            )
            SELECT
                (SELECT d FROM anchor) AS anchor_date,
                (SELECT no_shows FROM recent r, anchor WHERE r.d = anchor.d) AS today,
                (SELECT COALESCE(SUM(no_shows), 0) FROM recent) AS this_week,
                (SELECT COALESCE(AVG(no_shows), 0) FROM baseline) AS trailing_30d_avg_per_day
        """
        row = conn.execute(text(sql), params).mappings().first()

    if row is None or row["anchor_date"] is None:
        return {"today": 0, "this_week": 0, "trend_pct": None, "trend_direction": "flat"}

    today = int(row["today"] or 0)
    week = int(row["this_week"] or 0)
    baseline_per_day = float(row["trailing_30d_avg_per_day"] or 0.0)
    trend_pct = _pct_delta(float(today), baseline_per_day)
    return {
        "today": today,
        "this_week": week,
        "trailing_30d_avg_per_day": round(baseline_per_day, 1),
        "trend_pct": trend_pct,
        "trend_direction": _direction(trend_pct),
    }


def flagged_drivers(engine: Engine, org_id: str, *, days: int = 30, nc_rate_threshold: float = 0.20) -> dict[str, Any]:
    """TM: drivers whose non-compliance (is_driver_nc OR is_cab_nc) rate over
    the window exceeds `nc_rate_threshold` -- the same coefficient-style
    threshold concept as performance_variability's CV, applied to a
    compliance-flag rate instead of a numeric std-dev."""
    contract = get_contract()
    trip = contract.entity("trip")
    c = trip.column

    # BUGFIX (found live): anchoring on literal MAX(trip_date) lands this
    # trailing-`days` window in a sparse, disconnected live-replay tail for
    # orgs like vanta-Aus (see date_window.py's docstring) -- almost every
    # driver would then show near-zero trips and never clear the HAVING
    # floor. dense_anchor_date anchors on the most recent date with real
    # volume instead, same fix as every chart_data.py rollup.
    sql = f"""
        SELECT {c('driver_id')} AS driver_id, COUNT(*) AS total,
               COUNT(*) FILTER (WHERE t.{c('is_driver_nc')} OR t.{c('is_cab_nc')}) AS flagged
        FROM {trip.table} t
        WHERE t.{c('org_id')} = :org_id
          AND t.{c('trip_date')} > CAST(:anchor AS date) - (:days || ' days')::interval
          AND t.{c('trip_date')} <= CAST(:anchor AS date)
          AND t.{c('driver_id')} IS NOT NULL
        GROUP BY {c('driver_id')}
        HAVING COUNT(*) >= 10
    """
    with engine.connect() as conn:
        anchor = dense_anchor_date(conn, trip.table, c("trip_date"), org_id, c("org_id"))
        rows = conn.execute(text(sql), {"org_id": org_id, "days": days, "anchor": anchor}).mappings().all()

    flagged_count = sum(1 for row in rows if row["total"] and row["flagged"] / row["total"] >= nc_rate_threshold)
    return {"flagged_driver_count": flagged_count, "total_drivers_evaluated": len(rows), "window_days": days}


def cost_optimization_outlook(
    engine: Engine,
    org_id: str,
    *,
    since: date | None = None,
    until: date | None = None,
    baseline_days: int = 30,
) -> dict[str, Any]:
    """TH cost card + the cost-optimization-for-a-window capability. When
    `since`/`until` are omitted, defaults to "today" vs. its own trailing
    `baseline_days`-day average (matching the plan's "cost is up 18% vs the
    30-day average" phrasing exactly); when both are supplied, computes the
    total for that explicit window instead, still compared against the same
    daily baseline -- so a persona can ask about an arbitrary past window
    (e.g. "what did June 1-15 look like"), not just "today."

    "Optimization opportunities" are NOT a new detector -- they're the
    existing `performance_variability` cost sub-metric's flagged vendors
    within the window, reframed as an actionable top-N list (deliberately
    reuses real signal-detection logic already built and tested, rather than
    inventing a second cost-inconsistency query).
    """
    contract = get_contract()
    cost = contract.entity("cost")
    vendor = contract.entity("vendor")
    c = cost.column

    with engine.connect() as conn:
        # BUGFIX (found live): when no `since`/`until` is given (the default
        # "TH cost card" path), this used to anchor on literal MAX(cost_date)
        # -- for an org whose newest row is a sparse, disconnected live-replay
        # day, that showed a nearly-empty single day as "today's cost" with
        # no real content. dense_anchor_date anchors on the most recent date
        # with real volume instead; this is on top of (not a duplicate of)
        # the baseline-window fix below, which was about comparing a real
        # *explicit* window to the wrong baseline period -- this fixes the
        # *default*, no-window-given case similarly landing on the wrong day.
        anchor = dense_anchor_date(conn, cost.table, c("cost_date"), org_id, c("org_id"))
        if anchor is None:
            return {"window_total_inr": 0.0, "baseline_avg_per_day_inr": 0.0, "trend_pct": None, "trend_direction": "flat", "opportunities": []}

        window_start = since or anchor
        window_end = until or anchor
        # BUGFIX (found live): anchoring the baseline lookback to the
        # database's global MAX(cost_date) breaks for any org whose most
        # recent activity is a sparse, disconnected "live demo" tail far
        # past the real historical bulk (verified live: vanta-Aus's global
        # anchor is a single injected day with ~4,000 INR total, vs. ~93M
        # INR across the real May-Jul backlog) -- comparing a real window to
        # that thin tail produced a nonsensical 25,000%+ "trend." The
        # baseline must be anchored to the WINDOW being analyzed (the
        # `baseline_days` immediately preceding it), not to wherever the
        # database's latest row happens to be -- this is also the more
        # correct definition of "moving average for this window" regardless
        # of the demo-data quirk.
        baseline_end = window_start - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=baseline_days)

        window_row = conn.execute(
            text(
                f"SELECT COALESCE(SUM({c('amount')}), 0) AS total, "
                f"GREATEST((CAST(:window_end AS date) - CAST(:window_start AS date)) + 1, 1) AS days "
                f"FROM {cost.table} WHERE {c('org_id')} = :org_id "
                f"AND {c('cost_date')} BETWEEN :window_start AND :window_end"
            ),
            {"org_id": org_id, "window_start": window_start, "window_end": window_end},
        ).mappings().first()
        baseline_row = conn.execute(
            text(
                f"SELECT COALESCE(AVG(daily_total), 0) AS avg_per_day FROM ("
                f"SELECT {c('cost_date')} AS d, SUM({c('amount')}) AS daily_total FROM {cost.table} "
                f"WHERE {c('org_id')} = :org_id AND {c('cost_date')} > :baseline_start AND {c('cost_date')} <= :baseline_end "
                f"GROUP BY {c('cost_date')}) daily"
            ),
            {"org_id": org_id, "baseline_start": baseline_start, "baseline_end": baseline_end},
        ).mappings().first()

        # Optimization opportunities: reuse the existing performance_variability
        # cost sub-metric's own query shape (grouped by vendor, CV of cost_per_km)
        # scoped to this window, not a new detector.
        opp_rows = conn.execute(
            text(
                f"SELECT v.{vendor.column('name')} AS vendor_name, COUNT(*) AS sample_count, "
                f"AVG(c.{c('cost_per_km')}) AS avg_cost_per_km, STDDEV_POP(c.{c('cost_per_km')}) AS stddev_cost_per_km "
                f"FROM {cost.table} c JOIN {vendor.table} v ON v.{vendor.column('id')} = c.{c('vendor_id')} "
                f"WHERE c.{c('org_id')} = :org_id AND c.{c('cost_date')} BETWEEN :window_start AND :window_end "
                f"AND c.{c('cost_per_km')} > 0 "
                f"GROUP BY v.{vendor.column('id')}, v.{vendor.column('name')} HAVING COUNT(*) >= 15"
            ),
            {"org_id": org_id, "window_start": window_start, "window_end": window_end},
        ).mappings().all()

    opportunities = []
    for row in opp_rows:
        avg_cost = float(row["avg_cost_per_km"] or 0.0)
        stddev = float(row["stddev_cost_per_km"] or 0.0)
        if avg_cost <= 0:
            continue
        cv_pct = round(100.0 * stddev / avg_cost, 1)
        if cv_pct >= 20.0:
            opportunities.append(
                {
                    "vendor_name": row["vendor_name"],
                    "cv_pct": cv_pct,
                    "recommendation": (
                        f"{row['vendor_name']}'s per-km billing is {cv_pct:.0f}% inconsistent in this "
                        "window -- an invoice audit or rate-card renegotiation is worth prioritizing here."
                    ),
                }
            )
    opportunities.sort(key=lambda o: o["cv_pct"], reverse=True)

    window_total = float(window_row["total"]) if window_row else 0.0
    window_days = int(window_row["days"]) if window_row else 1
    baseline_per_day = float(baseline_row["avg_per_day"]) if baseline_row else 0.0
    window_avg_per_day = window_total / window_days if window_days else window_total
    trend_pct = _pct_delta(window_avg_per_day, baseline_per_day)

    return {
        "window_start": str(window_start),
        "window_end": str(window_end),
        "window_total_inr": round(window_total, 2),
        "baseline_avg_per_day_inr": round(baseline_per_day, 2),
        "trend_pct": trend_pct,
        "trend_direction": _direction(trend_pct),
        "opportunities": opportunities[:5],
    }


def org_wide_no_show_trend(engine: Engine, org_id: str, *, baseline_days: int = 30) -> dict[str, Any]:
    """TH: same shape as no_shows_today_and_week but org-wide, no team scope."""
    return no_shows_today_and_week(engine, org_id, team_id=None)
