"""Sense-layer detector nodes (plan §2 Sense paragraph, §4 Sense subgraph).

Every detector is a pure function: (Connection, org_id, since, ...tunable
thresholds) -> list[Signal]. No detector mutates anything except
`flag_data_quality`, which writes rows into `data_quality_flags` (that is its
entire job) and still never raises past its own boundary.

Table/column names for every *business* entity (trip, cost, incident,
emission, commute, attendance, vendor, employee, route, driver) are resolved
through `app.contracts.get_contract()` -- never a literal string. The two
exceptions are `data_quality_flags` and `sustainability_targets`: both are
fixed infra/reference tables, not logical business entities modeled in
`data_contract.yaml` (see backend/config/data_contract.yaml's entity list),
so they're referenced by the literal names schema.sql defines for them. This
is a deliberate, narrow exception -- flagged in the module docstring rather
than silently done -- not an oversight of the "never a literal string" rule.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.contracts import get_contract
from app.graph.sense.state import Signal

logger = logging.getLogger(__name__)

DATA_QUALITY_FLAGS_TABLE = "data_quality_flags"
SUSTAINABILITY_TARGETS_TABLE = "sustainability_targets"

DEFAULT_LOOKBACK = timedelta(days=7)
DEFAULT_DELAY_BREACH_MINUTES = 15.0
DEFAULT_ICE_BASELINE_GCO2_PER_PAX_KM = 82.0
DEFAULT_COST_DIVERGENCE_PCT = 0.20
DEFAULT_MIN_LATE_SAMPLES = 3
DEFAULT_TRANSPORT_CORRELATION_RATIO = 0.6
DEFAULT_UNRELATED_CORRELATION_RATIO = 0.15

INCIDENT_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
DEFAULT_INCIDENT_SEVERITY_THRESHOLD = "medium"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _since_date(since: datetime) -> date:
    return since.date() if isinstance(since, datetime) else since


def _resolve_since(since: datetime | None) -> datetime:
    return since if since is not None else _utcnow() - DEFAULT_LOOKBACK


def safe_detect(fn: Callable[..., list[Signal]]) -> Callable[..., list[Signal]]:
    """Wrap a detector so a query/connectivity failure in one never takes down
    the others -- the sense subgraph runs detectors as parallel branches, and
    a single bad branch failing the whole `graph.invoke()` would be worse than
    losing that one detector's signals for this pass."""

    def wrapped(*args: Any, **kwargs: Any) -> list[Signal]:
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("sense detector %s failed; returning no signals for this pass", fn.__name__)
            return []

    wrapped.__name__ = fn.__name__
    return wrapped


# ---------------------------------------------------------------------------
# detect_delay_signal
# ---------------------------------------------------------------------------


@safe_detect
def detect_delay_signal(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
    delay_threshold_minutes: float = DEFAULT_DELAY_BREACH_MINUTES,
) -> list[Signal]:
    """Flags routes where completed trips' actual_time significantly exceeds
    scheduled_time. Aggregated per route over the window (rather than one
    signal per trip) so a sustained spike reads as one clear route-level
    signal instead of dozens of near-duplicate trip-level ones."""

    contract = get_contract()
    trip = contract.entity("trip")
    route = contract.entity("route")
    vendor = contract.entity("vendor")
    since = _resolve_since(since)

    delay_expr = (
        f"EXTRACT(EPOCH FROM (t.{trip.column('actual_time')} - t.{trip.column('scheduled_time')})) / 60.0"
    )

    sql = text(f"""
        SELECT
            t.{trip.column('route_id')} AS route_id,
            r.{route.column('route_code')} AS route_code,
            r.{route.column('name')} AS route_name,
            r.{route.column('vendor_id')} AS vendor_id,
            v.{vendor.column('name')} AS vendor_name,
            v.{vendor.column('sla_target_pct')} AS vendor_sla_target_pct,
            COUNT(*) AS trip_count,
            AVG({delay_expr}) AS avg_delay_minutes,
            MAX({delay_expr}) AS max_delay_minutes,
            COUNT(*) FILTER (WHERE {delay_expr} > :delay_threshold) AS breach_count
        FROM {trip.table} t
        JOIN {route.table} r ON r.{route.column('id')} = t.{trip.column('route_id')}
        LEFT JOIN {vendor.table} v ON v.{vendor.column('id')} = r.{route.column('vendor_id')}
        WHERE t.{trip.column('org_id')} = :org_id
          AND t.{trip.column('status')} = 'completed'
          AND t.{trip.column('actual_time')} IS NOT NULL
          AND t.{trip.column('scheduled_time')} IS NOT NULL
          AND t.{trip.column('trip_date')} >= :since_date
        GROUP BY t.{trip.column('route_id')}, r.{route.column('route_code')}, r.{route.column('name')},
                 r.{route.column('vendor_id')}, v.{vendor.column('name')}, v.{vendor.column('sla_target_pct')}
        HAVING COUNT(*) >= 3
    """)

    rows = conn.execute(
        sql, {"org_id": org_id, "delay_threshold": delay_threshold_minutes, "since_date": _since_date(since)}
    ).mappings().all()

    signals: list[Signal] = []
    for row in rows:
        trip_count = row["trip_count"]
        breach_count = row["breach_count"] or 0
        avg_delay = float(row["avg_delay_minutes"] or 0.0)
        max_delay = float(row["max_delay_minutes"] or 0.0)
        breach_pct = 100.0 * breach_count / trip_count if trip_count else 0.0

        flagged = breach_pct >= 25.0 or avg_delay >= 20.0
        if not flagged:
            continue

        if avg_delay >= 35.0 or breach_pct >= 80.0:
            severity = "critical"
        elif avg_delay >= 20.0 or breach_pct >= 50.0:
            severity = "high"
        else:
            severity = "medium"

        signals.append(
            Signal(
                signal_type="delay_breach",
                entity_type="route",
                entity_id=str(row["route_id"]),
                severity=severity,
                summary=(
                    f"Route {row['route_code']} ({row['route_name']}) breached the "
                    f"{delay_threshold_minutes:.0f}-min delay threshold on {breach_pct:.0f}% of "
                    f"{trip_count} trips since {since.date()}, avg delay {avg_delay:.1f} min "
                    f"(max {max_delay:.1f} min)."
                ),
                raw_metric={
                    "trip_count": trip_count,
                    "breach_count": breach_count,
                    "breach_pct": round(breach_pct, 2),
                    "avg_delay_minutes": round(avg_delay, 2),
                    "max_delay_minutes": round(max_delay, 2),
                    "delay_threshold_minutes": delay_threshold_minutes,
                    "vendor_sla_target_pct": float(row["vendor_sla_target_pct"]) if row["vendor_sla_target_pct"] is not None else None,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_delay_signal",
                context={
                    "route_code": row["route_code"],
                    "route_name": row["route_name"],
                    "vendor_id": row["vendor_id"],
                    "vendor_name": row["vendor_name"],
                },
            )
        )
    return signals


# ---------------------------------------------------------------------------
# detect_incident_signal
# ---------------------------------------------------------------------------


@safe_detect
def detect_incident_signal(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
    severity_threshold: str = DEFAULT_INCIDENT_SEVERITY_THRESHOLD,
) -> list[Signal]:
    """Flags new/unresolved incident rows at or above `severity_threshold`."""

    contract = get_contract()
    incident = contract.entity("incident")
    route = contract.entity("route")
    since = _resolve_since(since)
    min_rank = INCIDENT_SEVERITY_RANK.get(severity_threshold, 1)

    sql = text(f"""
        SELECT
            i.{incident.column('id')} AS id,
            i.{incident.column('route_id')} AS route_id,
            r.{route.column('route_code')} AS route_code,
            i.{incident.column('driver_id')} AS driver_id,
            i.{incident.column('incident_type')} AS incident_type,
            i.{incident.column('severity')} AS severity,
            i.{incident.column('status')} AS status,
            i.{incident.column('occurred_at')} AS occurred_at,
            i.{incident.column('description')} AS description
        FROM {incident.table} i
        LEFT JOIN {route.table} r ON r.{route.column('id')} = i.{incident.column('route_id')}
        WHERE i.{incident.column('org_id')} = :org_id
          AND i.{incident.column('occurred_at')} >= :since
          AND i.{incident.column('status')} IN ('open', 'investigating')
        ORDER BY i.{incident.column('occurred_at')} DESC
    """)

    rows = conn.execute(sql, {"org_id": org_id, "since": since}).mappings().all()

    signals: list[Signal] = []
    for row in rows:
        severity = row["severity"]
        if INCIDENT_SEVERITY_RANK.get(severity, 0) < min_rank:
            continue
        signals.append(
            Signal(
                signal_type="incident",
                entity_type="incident",
                entity_id=str(row["id"]),
                severity=severity,
                summary=(
                    f"Unresolved {severity} {row['incident_type']} incident on route "
                    f"{row['route_code'] or row['route_id']} (status: {row['status']}), "
                    f"occurred {row['occurred_at']}."
                ),
                raw_metric={
                    "incident_type": row["incident_type"],
                    "status": row["status"],
                    "occurred_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_incident_signal",
                context={
                    "route_id": row["route_id"],
                    "route_code": row["route_code"],
                    "driver_id": row["driver_id"],
                    "description": row["description"],
                },
            )
        )
    return signals


# ---------------------------------------------------------------------------
# detect_cost_anomaly
# ---------------------------------------------------------------------------


@safe_detect
def detect_cost_anomaly(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
    divergence_pct: float = DEFAULT_COST_DIVERGENCE_PCT,
) -> list[Signal]:
    """Flags vendors whose observed cost_per_km diverges from either their own
    contracted rate or the average of every other vendor by more than
    `divergence_pct`."""

    contract = get_contract()
    cost = contract.entity("cost")
    vendor = contract.entity("vendor")
    since = _resolve_since(since)

    sql = text(f"""
        SELECT
            c.{cost.column('vendor_id')} AS vendor_id,
            v.{vendor.column('name')} AS vendor_name,
            v.{vendor.column('cost_per_km')} AS contracted_cost_per_km,
            COUNT(*) AS sample_count,
            AVG(c.{cost.column('cost_per_km')}) AS observed_cost_per_km
        FROM {cost.table} c
        JOIN {vendor.table} v ON v.{vendor.column('id')} = c.{cost.column('vendor_id')}
        WHERE c.{cost.column('org_id')} = :org_id
          AND c.{cost.column('cost_date')} >= :since_date
          AND c.{cost.column('cost_per_km')} IS NOT NULL
        GROUP BY c.{cost.column('vendor_id')}, v.{vendor.column('name')}, v.{vendor.column('cost_per_km')}
        HAVING COUNT(*) >= 3
    """)

    rows = conn.execute(sql, {"org_id": org_id, "since_date": _since_date(since)}).mappings().all()
    if not rows:
        return []

    observed_by_vendor = {row["vendor_id"]: float(row["observed_cost_per_km"]) for row in rows}

    signals: list[Signal] = []
    for row in rows:
        vendor_id = row["vendor_id"]
        observed = observed_by_vendor[vendor_id]
        contracted = float(row["contracted_cost_per_km"]) if row["contracted_cost_per_km"] is not None else None

        others = [v for vid, v in observed_by_vendor.items() if vid != vendor_id]
        others_avg = sum(others) / len(others) if others else None

        div_vs_contract = abs(observed - contracted) / contracted if contracted else None
        div_vs_fleet = abs(observed - others_avg) / others_avg if others_avg else None
        candidates = [d for d in (div_vs_contract, div_vs_fleet) if d is not None]
        if not candidates:
            continue
        max_divergence = max(candidates)
        if max_divergence < divergence_pct:
            continue

        if max_divergence >= 0.5:
            severity = "critical"
        elif max_divergence >= 0.3:
            severity = "high"
        else:
            severity = "medium"

        signals.append(
            Signal(
                signal_type="cost_divergence",
                entity_type="vendor",
                entity_id=str(vendor_id),
                severity=severity,
                summary=(
                    f"Vendor {row['vendor_name']} observed cost/km of INR {observed:.2f} diverges "
                    f"{max_divergence * 100:.0f}% from "
                    f"{'contracted rate' if div_vs_contract == max_divergence else 'fleet average'} "
                    f"(contracted INR {contracted}, fleet avg INR {others_avg:.2f} across {len(others)} other vendor(s))."
                    if others_avg is not None
                    else f"Vendor {row['vendor_name']} observed cost/km of INR {observed:.2f} diverges {max_divergence * 100:.0f}% from its contracted rate INR {contracted}."
                ),
                raw_metric={
                    "sample_count": row["sample_count"],
                    "observed_cost_per_km": round(observed, 2),
                    "contracted_cost_per_km": contracted,
                    "fleet_avg_cost_per_km": round(others_avg, 2) if others_avg is not None else None,
                    "divergence_vs_contract_pct": round(div_vs_contract * 100, 2) if div_vs_contract is not None else None,
                    "divergence_vs_fleet_pct": round(div_vs_fleet * 100, 2) if div_vs_fleet is not None else None,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_cost_anomaly",
                context={"vendor_name": row["vendor_name"]},
            )
        )
    return signals


# ---------------------------------------------------------------------------
# detect_emissions_signal
# ---------------------------------------------------------------------------


@safe_detect
def detect_emissions_signal(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
) -> list[Signal]:
    """Flags routes whose average co2_per_passenger_km trends above the ICE
    baseline pulled from `sustainability_targets` (not hardcoded)."""

    contract = get_contract()
    emission = contract.entity("emission")
    route = contract.entity("route")
    since = _resolve_since(since)

    baseline_row = conn.execute(
        text(
            f"""
            SELECT target_value, threshold_value FROM {SUSTAINABILITY_TARGETS_TABLE}
            WHERE org_id = :org_id AND metric_name = 'carbon_gco2_per_passenger_km'
            ORDER BY effective_from DESC LIMIT 1
            """
        ),
        {"org_id": org_id},
    ).mappings().first()

    if baseline_row is not None:
        baseline = float(baseline_row["threshold_value"] or baseline_row["target_value"])
    else:
        logger.warning("no carbon_gco2_per_passenger_km row in sustainability_targets; using hardcoded fallback")
        baseline = DEFAULT_ICE_BASELINE_GCO2_PER_PAX_KM

    sql = text(f"""
        SELECT
            e.{emission.column('route_id')} AS route_id,
            r.{route.column('route_code')} AS route_code,
            r.{route.column('name')} AS route_name,
            COUNT(*) AS sample_count,
            AVG(e.{emission.column('co2_per_passenger_km')}) AS avg_co2_per_passenger_km,
            MAX(e.{emission.column('co2_per_passenger_km')}) AS max_co2_per_passenger_km
        FROM {emission.table} e
        JOIN {route.table} r ON r.{route.column('id')} = e.{emission.column('route_id')}
        WHERE e.{emission.column('org_id')} = :org_id
          AND e.{emission.column('log_date')} >= :since_date
          AND e.{emission.column('co2_per_passenger_km')} IS NOT NULL
        GROUP BY e.{emission.column('route_id')}, r.{route.column('route_code')}, r.{route.column('name')}
        HAVING COUNT(*) >= 3
    """)

    rows = conn.execute(sql, {"org_id": org_id, "since_date": _since_date(since)}).mappings().all()

    signals: list[Signal] = []
    for row in rows:
        avg_co2 = float(row["avg_co2_per_passenger_km"])
        ratio = avg_co2 / baseline
        # A few % over baseline is noise inherent to the ICE emission-factor
        # spread, not a trend worth surfacing -- require a meaningful margin.
        if ratio < 1.05:
            continue

        if ratio >= 1.5:
            severity = "critical"
        elif ratio >= 1.2:
            severity = "high"
        else:
            severity = "medium"

        signals.append(
            Signal(
                signal_type="emissions_over_target",
                entity_type="route",
                entity_id=str(row["route_id"]),
                severity=severity,
                summary=(
                    f"Route {row['route_code']} ({row['route_name']}) averaging {avg_co2:.1f} "
                    f"gCO2/passenger-km since {since.date()}, {ratio * 100 - 100:.0f}% above the "
                    f"{baseline:.0f} gCO2/passenger-km ICE baseline."
                ),
                raw_metric={
                    "sample_count": row["sample_count"],
                    "avg_co2_per_passenger_km": round(avg_co2, 2),
                    "max_co2_per_passenger_km": round(float(row["max_co2_per_passenger_km"]), 2),
                    "baseline_gco2_per_passenger_km": baseline,
                    "pct_above_baseline": round((ratio - 1.0) * 100, 2),
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_emissions_signal",
                context={"route_code": row["route_code"], "route_name": row["route_name"]},
            )
        )
    return signals


# ---------------------------------------------------------------------------
# detect_attendance_correlation
# ---------------------------------------------------------------------------


@safe_detect
def detect_attendance_correlation(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
    delay_threshold_minutes: float = DEFAULT_DELAY_BREACH_MINUTES,
    min_late_samples: int = DEFAULT_MIN_LATE_SAMPLES,
) -> list[Signal]:
    """Joins attendance to commute (via trip) to classify each employee's late
    marks as transport-caused vs. unrelated, and flags both directions:
    - high transport-correlation -> the employee's attendance record shouldn't
      be held against them, it's a shuttle problem.
    - late marks with ~zero transport correlation -> a genuine attendance
      pattern worth a manager's attention, not a transport issue.
    """

    contract = get_contract()
    attendance = contract.entity("attendance")
    commute = contract.entity("commute")
    trip = contract.entity("trip")
    employee = contract.entity("employee")
    since = _resolve_since(since)

    delay_expr = (
        f"EXTRACT(EPOCH FROM (t.{trip.column('actual_time')} - t.{trip.column('scheduled_time')})) / 60.0"
    )

    sql = text(f"""
        SELECT
            a.{attendance.column('employee_id')} AS employee_id,
            emp.{employee.column('name')} AS employee_name,
            emp.{employee.column('team_id')} AS team_id,
            COUNT(*) AS late_count,
            COUNT(*) FILTER (
                WHERE c.{commute.column('mode')} = 'shuttle'
                  AND t.{trip.column('actual_time')} IS NOT NULL
                  AND t.{trip.column('scheduled_time')} IS NOT NULL
                  AND {delay_expr} > :delay_threshold
            ) AS transport_caused_count
        FROM {attendance.table} a
        JOIN {employee.table} emp ON emp.{employee.column('id')} = a.{attendance.column('employee_id')}
        LEFT JOIN {commute.table} c ON c.{commute.column('employee_id')} = a.{attendance.column('employee_id')}
                                     AND c.{commute.column('log_date')} = a.{attendance.column('work_date')}
        LEFT JOIN {trip.table} t ON t.{trip.column('id')} = c.{commute.column('trip_id')}
        WHERE a.{attendance.column('org_id')} = :org_id
          AND a.{attendance.column('status')} = 'late'
          AND a.{attendance.column('work_date')} >= :since_date
        GROUP BY a.{attendance.column('employee_id')}, emp.{employee.column('name')}, emp.{employee.column('team_id')}
        HAVING COUNT(*) >= :min_late_samples
    """)

    rows = conn.execute(
        sql,
        {
            "org_id": org_id,
            "delay_threshold": delay_threshold_minutes,
            "since_date": _since_date(since),
            "min_late_samples": min_late_samples,
        },
    ).mappings().all()

    signals: list[Signal] = []
    for row in rows:
        late_count = row["late_count"]
        transport_count = row["transport_caused_count"] or 0
        ratio = transport_count / late_count if late_count else 0.0

        if ratio >= DEFAULT_TRANSPORT_CORRELATION_RATIO:
            signals.append(
                Signal(
                    signal_type="attendance_correlated_with_transport",
                    entity_type="employee",
                    entity_id=str(row["employee_id"]),
                    severity="high" if ratio >= 0.85 else "medium",
                    summary=(
                        f"{row['employee_name']} was late {late_count} time(s) since {since.date()}; "
                        f"{transport_count} ({ratio * 100:.0f}%) coincide with shuttle delays over "
                        f"{delay_threshold_minutes:.0f} min -- likely not an attendance issue."
                    ),
                    raw_metric={
                        "late_count": late_count,
                        "transport_caused_count": transport_count,
                        "correlation_ratio": round(ratio, 3),
                    },
                    org_id=org_id,
                    detected_at=_utcnow(),
                    source="detect_attendance_correlation",
                    context={"team_id": row["team_id"], "employee_name": row["employee_name"]},
                )
            )
        elif ratio <= DEFAULT_UNRELATED_CORRELATION_RATIO:
            signals.append(
                Signal(
                    signal_type="attendance_unrelated_late",
                    entity_type="employee",
                    entity_id=str(row["employee_id"]),
                    severity="low" if late_count < min_late_samples * 2 else "medium",
                    summary=(
                        f"{row['employee_name']} was late {late_count} time(s) since {since.date()} "
                        f"with no meaningful correlation to shuttle delays -- likely unrelated to transport."
                    ),
                    raw_metric={
                        "late_count": late_count,
                        "transport_caused_count": transport_count,
                        "correlation_ratio": round(ratio, 3),
                    },
                    org_id=org_id,
                    detected_at=_utcnow(),
                    source="detect_attendance_correlation",
                    context={"team_id": row["team_id"], "employee_name": row["employee_name"]},
                )
            )
    return signals


# ---------------------------------------------------------------------------
# flag_data_quality
# ---------------------------------------------------------------------------


@safe_detect
def flag_data_quality(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
) -> list[Signal]:
    """Runs on every delta pass regardless of what the other detectors find.
    Checks for nulls in required fields, duplicate-looking rows, and malformed
    timestamps in `trip`, then writes each finding into `data_quality_flags`
    (never silently drops messy data). Never raises -- any failure here must
    not affect the other detectors, so this function follows the same
    safe_detect contract as everything else in this module."""

    contract = get_contract()
    trip = contract.entity("trip")
    employee = contract.entity("employee")
    driver = contract.entity("driver")
    since = _resolve_since(since)
    since_date = _since_date(since)

    issues: list[dict[str, Any]] = []

    null_actual_rows = conn.execute(
        text(f"""
            SELECT {trip.column('id')} AS id FROM {trip.table}
            WHERE {trip.column('org_id')} = :org_id
              AND {trip.column('status')} = 'completed'
              AND {trip.column('actual_time')} IS NULL
              AND {trip.column('trip_date')} >= :since_date
        """),
        {"org_id": org_id, "since_date": since_date},
    ).mappings().all()
    for row in null_actual_rows:
        issues.append({
            "source_table": trip.table, "source_pk": str(row["id"]),
            "issue_type": "null_required_field",
            "issue_detail": "trip marked completed but actual_time is null",
            "severity": "medium",
        })

    malformed_rows = conn.execute(
        text(f"""
            SELECT {trip.column('id')} AS id, {trip.column('trip_date')} AS trip_date, {trip.column('actual_time')} AS actual_time
            FROM {trip.table}
            WHERE {trip.column('org_id')} = :org_id
              AND {trip.column('actual_time')} IS NOT NULL
              AND {trip.column('trip_date')} >= :since_date
              AND ABS(EXTRACT(EPOCH FROM ({trip.column('actual_time')} - {trip.column('trip_date')}::timestamptz)) / 86400.0) > 1
        """),
        {"org_id": org_id, "since_date": since_date},
    ).mappings().all()
    for row in malformed_rows:
        issues.append({
            "source_table": trip.table, "source_pk": str(row["id"]),
            "issue_type": "malformed_timestamp",
            "issue_detail": f"actual_time {row['actual_time']} implausibly far from trip_date {row['trip_date']}",
            "severity": "medium",
        })

    duplicate_rows = conn.execute(
        text(f"""
            SELECT {trip.column('route_id')} AS route_id, {trip.column('trip_date')} AS trip_date,
                   array_agg({trip.column('id')}) AS ids
            FROM {trip.table}
            WHERE {trip.column('org_id')} = :org_id
              AND {trip.column('status')} = 'completed'
              AND {trip.column('trip_date')} >= :since_date
            GROUP BY {trip.column('route_id')}, {trip.column('trip_date')}
            HAVING COUNT(*) > 1
        """),
        {"org_id": org_id, "since_date": since_date},
    ).mappings().all()
    for row in duplicate_rows:
        ids = row["ids"]
        issues.append({
            "source_table": trip.table, "source_pk": ",".join(str(i) for i in ids),
            "issue_type": "duplicate_row",
            "issue_detail": f"{len(ids)} completed trips for route {row['route_id']} on {row['trip_date']}",
            "severity": "low",
        })

    out_of_range_rows = conn.execute(
        text(f"""
            SELECT {trip.column('id')} AS id, {trip.column('passenger_count')} AS passenger_count
            FROM {trip.table}
            WHERE {trip.column('org_id')} = :org_id
              AND {trip.column('trip_date')} >= :since_date
              AND ({trip.column('passenger_count')} < 0 OR {trip.column('passenger_count')} > 100)
        """),
        {"org_id": org_id, "since_date": since_date},
    ).mappings().all()
    for row in out_of_range_rows:
        issues.append({
            "source_table": trip.table, "source_pk": str(row["id"]),
            "issue_type": "out_of_range_value",
            "issue_detail": f"passenger_count={row['passenger_count']} outside plausible range",
            "severity": "low",
        })

    null_email_rows = conn.execute(
        text(f"""
            SELECT {employee.column('id')} AS id FROM {employee.table}
            WHERE {employee.column('org_id')} = :org_id AND {employee.column('email')} IS NULL
        """),
        {"org_id": org_id},
    ).mappings().all()
    for row in null_email_rows:
        issues.append({
            "source_table": employee.table, "source_pk": str(row["id"]),
            "issue_type": "null_required_field",
            "issue_detail": "employee row missing email",
            "severity": "low",
        })

    null_license_rows = conn.execute(
        text(f"""
            SELECT {driver.column('id')} AS id FROM {driver.table}
            WHERE {driver.column('org_id')} = :org_id AND {driver.column('license_number')} IS NULL
        """),
        {"org_id": org_id},
    ).mappings().all()
    for row in null_license_rows:
        issues.append({
            "source_table": driver.table, "source_pk": str(row["id"]),
            "issue_type": "null_required_field",
            "issue_detail": "driver row missing license_number",
            "severity": "medium",
        })

    for issue in issues:
        conn.execute(
            text(f"""
                INSERT INTO {DATA_QUALITY_FLAGS_TABLE}
                    (org_id, source_table, source_pk, issue_type, issue_detail, severity)
                VALUES (:org_id, :source_table, :source_pk, :issue_type, :issue_detail, :severity)
            """),
            {"org_id": org_id, **issue},
        )
    conn.commit()

    if not issues:
        return []

    by_type: dict[str, int] = {}
    for issue in issues:
        by_type[issue["issue_type"]] = by_type.get(issue["issue_type"], 0) + 1

    max_severity = max(issues, key=lambda i: {"low": 0, "medium": 1, "high": 2}[i["severity"]])["severity"]

    return [
        Signal(
            signal_type="data_quality_issue",
            entity_type="data_quality",
            entity_id=f"{org_id}:{since_date.isoformat()}",
            severity=max_severity,
            summary=f"{len(issues)} data quality issue(s) logged since {since_date}: " + ", ".join(f"{k}={v}" for k, v in by_type.items()),
            raw_metric={"total_issues": len(issues), "by_type": by_type},
            org_id=org_id,
            detected_at=_utcnow(),
            source="flag_data_quality",
        )
    ]
