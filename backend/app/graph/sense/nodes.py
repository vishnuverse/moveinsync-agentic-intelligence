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

from sqlalchemy import bindparam, text
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
# BUGFIX (found live: a single sense pass against the real ~26K-employee
# dataset produced 418 attendance_unrelated_late signals in one run -- unlike
# detect_escort_compliance_signal below, this detector had no cap at all, so
# every one of those 418 signals fanned out into its own full reason->act LLM
# round-trip, blew through LLM_DAILY_CALL_LIMIT partway through, and every
# signal after that failed closed with LLMBudgetExhaustedError -- which is
# why the demo looked like "no agentic intelligence" after the first
# scheduler tick: the very first real run exhausted its own budget. Same
# per-pass cap pattern as DEFAULT_ESCORT_VIOLATION_SIGNAL_LIMIT below,
# applied to both directions this detector emits.
DEFAULT_ATTENDANCE_SIGNAL_LIMIT = 25
DEFAULT_TRANSPORT_CORRELATION_RATIO = 0.6
DEFAULT_UNRELATED_CORRELATION_RATIO = 0.15

INCIDENT_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
DEFAULT_INCIDENT_SEVERITY_THRESHOLD = "medium"

# PRD v3 F1: "late-night" window for the escort-compliance check. No per-org
# timezone/office-local-time data exists anywhere in the contract (trip
# timestamps are stored as plain UTC), so this reads the UTC hour of
# actual_departure directly rather than fabricating a local-time conversion
# the data can't actually support -- a documented approximation, same spirit
# as mis_schema.sql's own disclosed approximations.
NIGHT_WINDOW_START_HOUR = 21  # 9 PM
NIGHT_WINDOW_END_HOUR = 6  # before 6 AM
DEFAULT_ESCORT_VIOLATION_SIGNAL_LIMIT = 50
# alerts_data.event_type values that are genuinely panic/SOS-family (per
# data/Dictionary/alerts_data.md's 11 observed values) -- deliberately
# excludes DEVICE_NOT_REACHABLE/VEHICLE_STOPPAGE/EMPLOYEE_GEOFENCE_VIOLATION/
# OVER_SPEEDING/FIRST_MALE_NO_SHOW/EMPLOYEE_SIGN_OFF_TIME_VIOLATION/
# SUPPLEMENTARY_ALERT, which are real alert types but not escort/safety-panic
# events PRD F1 is about.
PANIC_EVENT_TYPES = (
    "PANIC_MOBILE",
    "PANIC_DEVICE",
    "PANIC_FIXED_DEVICE",
    "WOMAN_TRAVELLING_ALONE",
)

DEFAULT_MIN_SLAB_SAMPLE = 20
DEFAULT_MIN_SLAB_EXPECTED_COST_SAMPLE = 5
DEFAULT_MIN_FLAGGED_TRIPS_PER_VENDOR = 3
DEFAULT_MIN_DISCREPANCY_INR = 500.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _since_date(since: datetime) -> date:
    return since.date() if isinstance(since, datetime) else since


def _resolve_since(conn: Connection, org_id: str, since: datetime | None) -> datetime:
    """BUGFIX (found live: every detector returned nothing against the real
    dataset by default): this used to anchor the default lookback window on
    wall-clock `_utcnow()`, which is fine for the synthetic seed (generated
    relative to "now" at seed time) but silently breaks on the real dataset,
    which is fixed to May-Jul 2026 -- any run after that (e.g. today) found
    zero rows in a "last 7 days from wall-clock now" window, so every
    detector except flag_data_quality (which isn't time-windowed the same
    way) produced nothing. Same fix `chart_data.py` already applies to its
    aggregation queries ("anchored to the data's own most-recent date, not
    wall-clock now") -- extended here to the sense layer too, via one MAX()
    query against the trip entity (the central fact table every other
    entity's real timestamps cluster around). Falls back to wall-clock if
    trip is empty (e.g. a genuinely fresh/synthetic DB with no data yet) --
    or if `mis.trip` doesn't exist at all yet: unlike the synthetic schema,
    `mis.*` is only created by db/real_data/ingest.py, which itself only
    runs when the real dataset's CSVs are present (see docker-compose.yml's
    `seed` comment) -- so a fresh clone without that (gitignored, host-only)
    dataset never gets `mis.trip` created, and every detector would
    otherwise raise UndefinedTable on every scheduler tick instead of just
    quietly returning no signals like an empty table does."""
    if since is not None:
        return since
    contract = get_contract()
    trip = contract.entity("trip")
    if conn.execute(text("SELECT to_regclass(:t)"), {"t": trip.table}).scalar() is None:
        return _utcnow() - DEFAULT_LOOKBACK
    anchor = conn.execute(
        text(f"SELECT MAX({trip.column('actual_departure')}) FROM {trip.table} WHERE {trip.column('org_id')} = :org_id"),
        {"org_id": org_id},
    ).scalar()
    if anchor is None:
        return _utcnow() - DEFAULT_LOOKBACK
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return anchor - DEFAULT_LOOKBACK


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
    since = _resolve_since(conn, org_id, since)

    delay_expr = (
        f"EXTRACT(EPOCH FROM (t.{trip.column('actual_time')} - t.{trip.column('scheduled_time')})) / 60.0"
    )

    sql_text = f"""
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
    """
    sql = text(sql_text)

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
                    "detector_sql": sql_text,
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
    since = _resolve_since(conn, org_id, since)
    min_rank = INCIDENT_SEVERITY_RANK.get(severity_threshold, 1)

    sql_text = f"""
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
    """
    sql = text(sql_text)

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
                    "detector_sql": sql_text,
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
    since = _resolve_since(conn, org_id, since)

    sql_text = f"""
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
    """
    sql = text(sql_text)

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
                    "detector_sql": sql_text,
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
    min_ratio_over_baseline: float = 1.05,
) -> list[Signal]:
    """Flags routes whose average co2_per_passenger_km trends above the ICE
    baseline pulled from `sustainability_targets` (not hardcoded).
    `min_ratio_over_baseline` (plan SP-B §2c) makes the "a few % over
    baseline is noise" floor configurable -- was a bare `1.05` literal."""

    contract = get_contract()
    emission = contract.entity("emission")
    route = contract.entity("route")
    since = _resolve_since(conn, org_id, since)

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

    sql_text = f"""
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
    """
    sql = text(sql_text)

    rows = conn.execute(sql, {"org_id": org_id, "since_date": _since_date(since)}).mappings().all()

    signals: list[Signal] = []
    for row in rows:
        avg_co2 = float(row["avg_co2_per_passenger_km"])
        ratio = avg_co2 / baseline
        # A few % over baseline is noise inherent to the ICE emission-factor
        # spread, not a trend worth surfacing -- require a meaningful margin.
        if ratio < min_ratio_over_baseline:
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
                    "detector_sql": sql_text,
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
    signal_limit: int = DEFAULT_ATTENDANCE_SIGNAL_LIMIT,
    transport_correlation_ratio: float = DEFAULT_TRANSPORT_CORRELATION_RATIO,
    unrelated_correlation_ratio: float = DEFAULT_UNRELATED_CORRELATION_RATIO,
) -> list[Signal]:
    """Joins attendance to commute (via trip) to classify each employee's late
    marks as transport-caused vs. unrelated, and flags both directions:
    - high transport-correlation -> the employee's attendance record shouldn't
      be held against them, it's a shuttle problem.
    - late marks with ~zero transport correlation -> a genuine attendance
      pattern worth a manager's attention, not a transport issue.

    Each direction is independently capped at `signal_limit` (worst/highest
    late_count first) -- see DEFAULT_ATTENDANCE_SIGNAL_LIMIT's own comment for
    why this matters at real-dataset scale.
    """

    contract = get_contract()
    attendance = contract.entity("attendance")
    commute = contract.entity("commute")
    trip = contract.entity("trip")
    employee = contract.entity("employee")
    since = _resolve_since(conn, org_id, since)

    delay_expr = (
        f"EXTRACT(EPOCH FROM (t.{trip.column('actual_time')} - t.{trip.column('scheduled_time')})) / 60.0"
    )

    sql_text = f"""
        SELECT
            a.{attendance.column('employee_id')} AS employee_id,
            emp.{employee.column('name')} AS employee_name,
            emp.{employee.column('team_id')} AS team_id,
            COUNT(*) AS late_count,
            COUNT(*) FILTER (
                -- BUGFIX (found live: verified against the real dataset that
                -- some orgs' no-shows/lates are carried entirely on
                -- mode='cab' with ZERO 'shuttle' rows -- e.g. vanta-Aus is
                -- 100% cab -- so a bare `mode = 'shuttle'` filter silently
                -- zeroed out transport_caused_count for every employee in
                -- those orgs, regardless of actual delay data. Both real
                -- mis.commute.mode values are company-provided transport
                -- (walk_in/wfh, the only non-transport modes the original
                -- CHECK constraint allows, never appear in the real
                -- ingested data) -- either can equally cause a delay-driven
                -- late mark, so both belong in this filter.
                WHERE c.{commute.column('mode')} IN ('shuttle', 'cab')
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
    """
    sql = text(sql_text)

    rows = conn.execute(
        sql,
        {
            "org_id": org_id,
            "delay_threshold": delay_threshold_minutes,
            "since_date": _since_date(since),
            "min_late_samples": min_late_samples,
        },
    ).mappings().all()

    correlated_candidates: list[Any] = []
    unrelated_candidates: list[Any] = []
    for row in rows:
        late_count = row["late_count"]
        transport_count = row["transport_caused_count"] or 0
        ratio = transport_count / late_count if late_count else 0.0
        if ratio >= transport_correlation_ratio:
            correlated_candidates.append((row, ratio))
        elif ratio <= unrelated_correlation_ratio:
            unrelated_candidates.append((row, ratio))

    # Cap each direction independently (worst/highest late_count first) --
    # see signal_limit's docstring note and DEFAULT_ATTENDANCE_SIGNAL_LIMIT.
    correlated_candidates.sort(key=lambda pair: pair[0]["late_count"], reverse=True)
    unrelated_candidates.sort(key=lambda pair: pair[0]["late_count"], reverse=True)
    correlated_candidates = correlated_candidates[:signal_limit]
    unrelated_candidates = unrelated_candidates[:signal_limit]

    signals: list[Signal] = []
    for row, ratio in correlated_candidates:
        late_count = row["late_count"]
        transport_count = row["transport_caused_count"] or 0
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
                    "detector_sql": sql_text,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_attendance_correlation",
                context={"team_id": row["team_id"], "employee_name": row["employee_name"]},
            )
        )
    for row, ratio in unrelated_candidates:
        late_count = row["late_count"]
        transport_count = row["transport_caused_count"] or 0
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
                    "detector_sql": sql_text,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_attendance_correlation",
                context={"team_id": row["team_id"], "employee_name": row["employee_name"]},
            )
        )
    return signals


# ---------------------------------------------------------------------------
# detect_escort_compliance_signal
# ---------------------------------------------------------------------------


@safe_detect
def detect_escort_compliance_signal(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
    violation_limit: int = DEFAULT_ESCORT_VIOLATION_SIGNAL_LIMIT,
    night_window_start_hour: int = NIGHT_WINDOW_START_HOUR,
    night_window_end_hour: int = NIGHT_WINDOW_END_HOUR,
    drop_delay_critical_minutes: float = DEFAULT_DELAY_BREACH_MINUTES,
) -> list[Signal]:
    """PRD v3 Feature 1 (Escort Compliance & Real-time Safety Monitor) --
    plan SP-B §B0/§B1 "Hotspot 1: female employee traveling without an
    escort." Three independent sub-detections, all safety-critical:

    1. Unescorted late-night LOGOUT (drop) trips carrying a female employee.
       One Signal per violating trip -- verified live against the real
       dataset this is a genuinely large backlog (tens of thousands of
       historical trips across 3 months), so emission is capped at
       `violation_limit` most-recent violations to avoid flooding one sense
       pass; the org-wide compliance % (PRD's own "Late-Night Female Safety
       Escort Compliance (%)" formula) is computed over the FULL unbounded
       set regardless of the cap, and attached to every violation Signal's
       raw_metric so the true aggregate is never hidden by the cap.
       Severity is graded by delay (SP-B addition): an unescorted drop that
       is ALSO delayed past `drop_delay_critical_minutes` is `critical` (the
       employee's exposure window is both unsupervised and longer than
       planned); an on-time unescorted drop stays `high` (today's original
       behavior, unchanged).
    2. Unescorted late-night LOGIN (pickup) trips carrying a female employee
       (SP-B addition) -- structurally identical to sub-detection 1 but for
       the pickup leg, since an early/late-night pickup carries the same
       unsupervised-exposure risk sub-detection 1 already covers for drops.
       Its own org-wide compliance aggregate, independent of sub-detection
       1's, since pickup and drop are different trips.
    3. Any active (status='open') Sev-1/panic-family alert. Combined as an
       OR, not an AND, of severity='critical' and incident_type in the
       panic/SOS family -- verified live against the real dataset that every
       severity='critical' row is already status='resolved' (an AND would
       match zero currently-open rows), while a handful of genuinely still-
       open panic-type alerts carry a NULL severity because of the
       alerts_data messiness the PRD's own Technical Ingestion Specification
       (section 4.5) documents (the stray "False" literal / ~16k nulls
       cleaned to NULL during ingest). Treating either condition as
       sufficient is what actually surfaces the real open safety events
       instead of silently matching nothing.
    """

    contract = get_contract()
    trip = contract.entity("trip")
    commute = contract.entity("commute")
    employee = contract.entity("employee")
    incident = contract.entity("incident")
    since = _resolve_since(conn, org_id, since)

    signals: list[Signal] = []

    def night_window_filter(departure_expr: str) -> str:
        return f"(EXTRACT(HOUR FROM {departure_expr}) >= {night_window_start_hour} OR EXTRACT(HOUR FROM {departure_expr}) < {night_window_end_hour})"

    def unescorted_leg_signals(*, trip_direction: str, leg_label: str) -> list[Signal]:
        """Shared body for sub-detections 1 (LOGOUT/drop) and 2 (LOGIN/pickup)
        -- same query shape, only `trip_direction`/labeling differ."""
        night_filter = night_window_filter(f"t.{trip.column('actual_departure')}")
        base_where = f"""
            t.{trip.column('org_id')} = :org_id
              AND t.{trip.column('trip_direction')} = :trip_direction
              AND e.{employee.column('gender')} = 'FEMALE'
              AND t.{trip.column('actual_departure')} IS NOT NULL
              AND t.{trip.column('actual_departure')} >= :since
              AND {night_filter}
        """
        compliance_sql = f"""
            SELECT
                COUNT(*) AS total_late_night_female,
                COUNT(*) FILTER (WHERE t.{trip.column('actual_escort')} = FALSE) AS unescorted_count
            FROM {trip.table} t
            JOIN {commute.table} c ON c.{commute.column('trip_id')} = t.{trip.column('id')}
            JOIN {employee.table} e ON e.{employee.column('id')} = c.{commute.column('employee_id')}
            WHERE {base_where}
        """
        compliance_row = conn.execute(
            text(compliance_sql), {"org_id": org_id, "since": since, "trip_direction": trip_direction}
        ).mappings().first()
        total = (compliance_row["total_late_night_female"] or 0) if compliance_row else 0
        unescorted = (compliance_row["unescorted_count"] or 0) if compliance_row else 0
        compliance_pct = round((1 - unescorted / total) * 100, 2) if total else 100.0

        leg_signals: list[Signal] = []
        if not unescorted:
            return leg_signals

        # DISTINCT ON (trip_id): a shared cab can carry more than one female
        # employee on the same trip (multiple mis.commute legs join to the
        # same mis.trip row) -- verified live this happens for real; without
        # the dedup the same trip_id would emit near-duplicate Signals (and
        # collide on the same supervisor.py thread_id, since thread_id is
        # keyed on entity_id only), when the actionable unit (dispatch a
        # warning to the vendor for THIS trip) is per-trip, not per-rider.
        violation_sql = f"""
            SELECT trip_id, route_id, actual_departure, delay_minutes, employee_id, employee_name
            FROM (
                SELECT DISTINCT ON (t.{trip.column('id')})
                    t.{trip.column('id')} AS trip_id,
                    t.{trip.column('route_id')} AS route_id,
                    t.{trip.column('actual_departure')} AS actual_departure,
                    t.{trip.column('delay_minutes')} AS delay_minutes,
                    e.{employee.column('id')} AS employee_id,
                    e.{employee.column('name')} AS employee_name
                FROM {trip.table} t
                JOIN {commute.table} c ON c.{commute.column('trip_id')} = t.{trip.column('id')}
                JOIN {employee.table} e ON e.{employee.column('id')} = c.{commute.column('employee_id')}
                WHERE {base_where}
                  AND t.{trip.column('actual_escort')} = FALSE
                ORDER BY t.{trip.column('id')}, e.{employee.column('id')}
            ) dedup
            ORDER BY actual_departure DESC
            LIMIT :violation_limit
        """
        violation_rows = conn.execute(
            text(violation_sql),
            {"org_id": org_id, "since": since, "trip_direction": trip_direction, "violation_limit": violation_limit},
        ).mappings().all()

        for row in violation_rows:
            departure = row["actual_departure"]
            delay_minutes = row["delay_minutes"]
            is_critical_delay = (
                leg_label == "drop" and delay_minutes is not None and float(delay_minutes) >= drop_delay_critical_minutes
            )
            severity = "critical" if is_critical_delay else "high"
            delay_clause = (
                f" and delayed {float(delay_minutes):.0f} min past schedule" if is_critical_delay else ""
            )
            leg_signals.append(
                Signal(
                    signal_type="escort_compliance_violation",
                    entity_type="trip",
                    entity_id=str(row["trip_id"]),
                    severity=severity,
                    summary=(
                        f"Unescorted late-night {leg_label} ({trip_direction}) trip {row['trip_id']} for a "
                        f"female employee at {departure} (actual_escort=False){delay_clause}. Org-wide "
                        f"late-night female {leg_label} escort compliance is {compliance_pct:.1f}% "
                        f"({unescorted} of {total} late-night female {leg_label} trips unescorted)."
                    ),
                    raw_metric={
                        "pickup_or_drop": leg_label,
                        "actual_departure": departure.isoformat() if departure else None,
                        "delay_minutes": float(delay_minutes) if delay_minutes is not None else None,
                        f"org_total_late_night_female_{leg_label}_trips": total,
                        f"org_unescorted_late_night_female_{leg_label}_trips": unescorted,
                        f"org_late_night_female_{leg_label}_escort_compliance_pct": compliance_pct,
                        "detector_sql": violation_sql,
                    },
                    org_id=org_id,
                    detected_at=_utcnow(),
                    source="detect_escort_compliance_signal",
                    context={
                        "route_id": row["route_id"],
                        "employee_id": row["employee_id"],
                        "employee_name": row["employee_name"],
                    },
                )
            )
        return leg_signals

    # --- sub-detection 1: unescorted late-night female LOGOUT (drop) trips
    signals.extend(unescorted_leg_signals(trip_direction="LOGOUT", leg_label="drop"))

    # --- sub-detection 2: unescorted late-night female LOGIN (pickup) trips
    # (SP-B addition -- see docstring point 2)
    signals.extend(unescorted_leg_signals(trip_direction="LOGIN", leg_label="pickup"))

    # --- sub-detection 3: active Sev-1 / panic-family alerts ---------------
    incident_sql_text = f"""
        SELECT
            i.{incident.column('id')} AS id,
            i.{incident.column('trip_id')} AS trip_id,
            i.{incident.column('route_id')} AS route_id,
            i.{incident.column('incident_type')} AS incident_type,
            i.{incident.column('severity')} AS severity,
            i.{incident.column('occurred_at')} AS occurred_at,
            i.{incident.column('acknowledge_time')} AS acknowledge_time
        FROM {incident.table} i
        WHERE i.{incident.column('org_id')} = :org_id
          AND i.{incident.column('status')} = 'open'
          AND i.{incident.column('occurred_at')} >= :since
          AND (i.{incident.column('severity')} = 'critical' OR i.{incident.column('incident_type')} IN :panic_types)
    """
    incident_sql = text(incident_sql_text).bindparams(bindparam("panic_types", expanding=True))

    incident_rows = conn.execute(
        incident_sql, {"org_id": org_id, "since": since, "panic_types": list(PANIC_EVENT_TYPES)}
    ).mappings().all()

    for row in incident_rows:
        occurred_at = row["occurred_at"]
        acknowledge_time = row["acknowledge_time"]
        response_time_seconds: float | None = None
        unacknowledged_minutes: float | None = None
        if acknowledge_time is not None and occurred_at is not None:
            response_time_seconds = (acknowledge_time - occurred_at).total_seconds()
            status_clause = f"acknowledged in {response_time_seconds:.0f}s"
        elif occurred_at is not None:
            unacknowledged_minutes = round((_utcnow() - occurred_at).total_seconds() / 60.0, 1)
            status_clause = f"still unacknowledged, {unacknowledged_minutes:.1f} min exposure"
        else:
            status_clause = "acknowledgement status unknown"

        signals.append(
            Signal(
                signal_type="escort_compliance_violation",
                entity_type="incident",
                entity_id=str(row["id"]),
                severity="critical",
                summary=(
                    f"Active {row['incident_type']} safety alert since {occurred_at} -- {status_clause}."
                ),
                raw_metric={
                    "incident_type": row["incident_type"],
                    "occurred_at": occurred_at.isoformat() if occurred_at else None,
                    "acknowledge_time": acknowledge_time.isoformat() if acknowledge_time else None,
                    "response_time_seconds": response_time_seconds,
                    "unacknowledged_minutes": unacknowledged_minutes,
                    "detector_sql": incident_sql_text,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_escort_compliance_signal",
                context={"trip_id": row["trip_id"], "route_id": row["route_id"]},
            )
        )

    return signals


# ---------------------------------------------------------------------------
# detect_billing_discrepancy_signal
# ---------------------------------------------------------------------------


@safe_detect
def detect_billing_discrepancy_signal(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
    min_slab_sample: int = DEFAULT_MIN_SLAB_SAMPLE,
    min_discrepancy_inr: float = DEFAULT_MIN_DISCREPANCY_INR,
) -> list[Signal]:
    """PRD v3 Feature 2 (Billing Slab & Distance Discrepancy Auditor).

    Slab-to-distance thresholds are NOT hardcoded: verified live against the
    real dataset that each of the 5 orgs uses a wildly different slab-naming
    convention on the same `slab_name` column -- "Short/Medium/Long",
    "Slab1".."Slab4", "Zone_A".."Zone_D", raw "0-20"/"21-30" km-range
    strings, even the literal text "null" as its own category -- so a single
    hardcoded PRD-example threshold (">25km = Long") would only ever fire
    for one org's naming scheme. Instead each org's own slab_name -> distance
    band is derived empirically per run, per org, from the median
    traveled_km of trips actually billed under each slab_name, with the
    boundary between two adjacent slabs (sorted by median) set at the
    midpoint between their medians -- the cheapest data-driven stand-in for
    the real (unavailable) contract rate card. "Calculated Slab Cost" (the
    PRD's own term in its measurement formula) for a given traveled_km is
    then the average trip_cost among trips that WERE billed under whichever
    slab actually contains that distance -- the empirical "going rate" for a
    correctly-slabbed trip of that length. A trip is flagged when the slab
    its real traveled_km falls into doesn't match the slab it was actually
    billed under; verified live this reproduces the PRD's own ~8-12%
    fleet-spend leakage ballpark (see build report) using only real data.

    Aggregated per vendor (not per trip): PRD's own Act description turns
    this into a vendor chargeback memo, and per-trip signals across a real
    billing cycle would be several thousand near-duplicate rows for the same
    handful of vendors.
    """

    contract = get_contract()
    cost = contract.entity("cost")
    trip = contract.entity("trip")
    vendor = contract.entity("vendor")
    since = _resolve_since(conn, org_id, since)
    since_date = _since_date(since)

    sql_text = f"""
        WITH billed AS (
            SELECT
                c.{cost.column('id')} AS cost_id,
                c.{cost.column('vendor_id')} AS vendor_id,
                v.{vendor.column('name')} AS vendor_name,
                c.{cost.column('slab_name')} AS slab_name,
                c.{cost.column('amount')} AS trip_cost,
                t.{trip.column('traveled_km')} AS traveled_km
            FROM {cost.table} c
            JOIN {trip.table} t ON t.{trip.column('id')} = c.{cost.column('trip_id')}
            LEFT JOIN {vendor.table} v ON v.{vendor.column('id')} = c.{cost.column('vendor_id')}
            WHERE c.{cost.column('org_id')} = :org_id
              AND c.{cost.column('cost_date')} >= :since_date
              AND c.{cost.column('slab_name')} IS NOT NULL AND trim(c.{cost.column('slab_name')}) <> ''
              AND t.{trip.column('traveled_km')} IS NOT NULL
              AND c.{cost.column('amount')} IS NOT NULL
        ),
        slab_bands AS (
            SELECT slab_name,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY traveled_km) AS median_km
            FROM billed
            GROUP BY slab_name
            HAVING COUNT(*) >= :min_slab_sample
        ),
        slab_bounds AS (
            SELECT slab_name, median_km,
                   LAG(median_km) OVER (ORDER BY median_km) AS prev_median,
                   LEAD(median_km) OVER (ORDER BY median_km) AS next_median
            FROM slab_bands
        ),
        slab_ranges AS (
            -- first slab (no prev): floor 0km; last slab (no next): open-ended ceiling
            SELECT slab_name,
                   COALESCE((prev_median + median_km) / 2.0, 0.0) AS lower_km,
                   COALESCE((median_km + next_median) / 2.0, median_km * 2 + 1000) AS upper_km
            FROM slab_bounds
        ),
        correct_slab AS (
            SELECT b.cost_id, r.slab_name AS correct_slab_name
            FROM billed b
            JOIN slab_ranges r ON b.traveled_km >= r.lower_km AND b.traveled_km < r.upper_km
        ),
        slab_expected_cost AS (
            SELECT b.slab_name, AVG(b.trip_cost) AS avg_cost_for_slab
            FROM billed b
            JOIN slab_ranges r ON r.slab_name = b.slab_name
            WHERE b.traveled_km >= r.lower_km AND b.traveled_km < r.upper_km
            GROUP BY b.slab_name
            HAVING COUNT(*) >= :min_expected_cost_sample
        ),
        flagged AS (
            SELECT b.vendor_id, b.vendor_name, b.trip_cost, sec.avg_cost_for_slab AS calculated_slab_cost
            FROM billed b
            JOIN correct_slab cs ON cs.cost_id = b.cost_id
            JOIN slab_expected_cost sec ON sec.slab_name = cs.correct_slab_name
            WHERE cs.correct_slab_name <> b.slab_name
        )
        SELECT
            vendor_id, vendor_name,
            COUNT(*) AS flagged_count,
            SUM(trip_cost - calculated_slab_cost) AS discrepancy_amount,
            AVG(trip_cost - calculated_slab_cost) AS avg_discrepancy_per_trip
        FROM flagged
        GROUP BY vendor_id, vendor_name
        HAVING COUNT(*) >= :min_flagged_trips AND SUM(trip_cost - calculated_slab_cost) >= :min_discrepancy
        ORDER BY discrepancy_amount DESC
    """
    sql = text(sql_text)

    rows = conn.execute(
        sql,
        {
            "org_id": org_id,
            "since_date": since_date,
            "min_slab_sample": min_slab_sample,
            "min_expected_cost_sample": DEFAULT_MIN_SLAB_EXPECTED_COST_SAMPLE,
            "min_flagged_trips": DEFAULT_MIN_FLAGGED_TRIPS_PER_VENDOR,
            "min_discrepancy": min_discrepancy_inr,
        },
    ).mappings().all()
    if not rows:
        return []

    total_billed_row = conn.execute(
        text(f"""
            SELECT SUM({cost.column('amount')}) AS total_billed
            FROM {cost.table}
            WHERE {cost.column('org_id')} = :org_id AND {cost.column('cost_date')} >= :since_date
        """),
        {"org_id": org_id, "since_date": since_date},
    ).mappings().first()
    total_billed = float(total_billed_row["total_billed"]) if total_billed_row and total_billed_row["total_billed"] else 0.0
    total_discrepancy = sum(float(row["discrepancy_amount"]) for row in rows)
    # PRD's own "Billing Leakage Rate (%)" formula -- computed once over the
    # whole flagged batch and attached to every vendor signal below, rather
    # than left for a later report-stage re-aggregation.
    leakage_rate_pct = round(total_discrepancy / total_billed * 100, 2) if total_billed else None

    signals: list[Signal] = []
    for row in rows:
        vendor_spend_row = conn.execute(
            text(f"""
                SELECT SUM({cost.column('amount')}) AS vendor_spend
                FROM {cost.table}
                WHERE {cost.column('org_id')} = :org_id AND {cost.column('vendor_id')} = :vendor_id
                  AND {cost.column('cost_date')} >= :since_date
            """),
            {"org_id": org_id, "vendor_id": row["vendor_id"], "since_date": since_date},
        ).mappings().first()
        vendor_spend = float(vendor_spend_row["vendor_spend"]) if vendor_spend_row and vendor_spend_row["vendor_spend"] else None
        discrepancy_amount = float(row["discrepancy_amount"])
        discrepancy_pct_of_vendor_spend = (
            round(discrepancy_amount / vendor_spend * 100, 2) if vendor_spend else None
        )

        # Severity scaled to what share of THIS vendor's own spend is
        # discrepant (not an absolute INR cutoff, which wouldn't generalize
        # across orgs of very different fleet size) -- verified live against
        # real data the observed range clusters at 12-15% per vendor.
        if discrepancy_pct_of_vendor_spend is not None and discrepancy_pct_of_vendor_spend >= 20.0:
            severity = "critical"
        elif discrepancy_pct_of_vendor_spend is not None and discrepancy_pct_of_vendor_spend >= 12.0:
            severity = "high"
        else:
            severity = "medium"

        pct_clause = (
            f", {discrepancy_pct_of_vendor_spend:.1f}% of this vendor's total billed spend"
            if discrepancy_pct_of_vendor_spend is not None
            else ""
        )
        signals.append(
            Signal(
                signal_type="billing_discrepancy",
                entity_type="vendor",
                entity_id=str(row["vendor_id"]),
                severity=severity,
                summary=(
                    f"Vendor {row['vendor_name']} has {row['flagged_count']} trip(s) billed under a "
                    f"distance slab inconsistent with actual GPS distance since {since.date()}, totaling "
                    f"INR {discrepancy_amount:,.2f} in overbilling "
                    f"(avg INR {float(row['avg_discrepancy_per_trip']):.2f}/trip){pct_clause}."
                ),
                raw_metric={
                    "flagged_trip_count": row["flagged_count"],
                    "discrepancy_amount_inr": round(discrepancy_amount, 2),
                    "avg_discrepancy_per_trip_inr": round(float(row["avg_discrepancy_per_trip"]), 2),
                    "vendor_total_billed_inr": round(vendor_spend, 2) if vendor_spend else None,
                    "discrepancy_pct_of_vendor_spend": discrepancy_pct_of_vendor_spend,
                    "org_total_billed_fleet_spend_inr": round(total_billed, 2),
                    "org_billing_leakage_rate_pct": leakage_rate_pct,
                    "detector_sql": sql_text,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_billing_discrepancy_signal",
                context={"vendor_name": row["vendor_name"]},
            )
        )
    return signals


# ---------------------------------------------------------------------------
# detect_variability_signal (plan SP-B §B2)
# ---------------------------------------------------------------------------

DEFAULT_VARIABILITY_CV_THRESHOLD_PCT = 20.0
DEFAULT_VARIABILITY_MIN_SAMPLE_SIZE = 15
DEFAULT_VARIABILITY_MINUTES_FLOOR = 10.0


@safe_detect
def detect_variability_signal(
    conn: Connection,
    org_id: str,
    since: datetime | None = None,
    cv_threshold_pct: float = DEFAULT_VARIABILITY_CV_THRESHOLD_PCT,
    min_sample_size: int = DEFAULT_VARIABILITY_MIN_SAMPLE_SIZE,
    variability_minutes_floor: float = DEFAULT_VARIABILITY_MINUTES_FLOOR,
) -> list[Signal]:
    """Flags *inconsistency*, not magnitude -- a route/vendor whose average
    looks fine but whose actual values swing wildly trip-to-trip is its own
    operational risk (unpredictable for planning, often a leading indicator
    of a magnitude breach about to happen) and is invisible to every other
    detector in this file, which all flag "average too high," never "too
    erratic." Uses the coefficient of variation (stddev / mean, as a %) --
    the same statistical-band technique `detect_billing_discrepancy_signal`
    already uses (`percentile_cont`) for a different purpose, no new library.

    Three independent sub-metrics, each requiring `min_sample_size` rows in
    its group before a CV is trusted (a tiny sample's stddev is noise, not
    signal -- same "insufficient sample" philosophy every other detector in
    this file already applies):

    1. On-time/delay consistency, grouped by route. Guarded: when a route's
       mean delay is close to zero, a ratio blows up for no real reason, so
       below `abs(mean) < 1.0` minute this compares the raw stddev against
       `variability_minutes_floor` instead of a ratio.
    2. Distance/route-adherence consistency, grouped by route -- variance of
       (traveled_km - planned_km), normalized against the route's own
       planned_km (a stable denominator, unlike normalizing against the
       deviation's own mean which can sit near zero).
    3. Cost consistency, grouped by vendor -- variance of cost_per_km.
    """

    contract = get_contract()
    trip = contract.entity("trip")
    route = contract.entity("route")
    cost = contract.entity("cost")
    vendor = contract.entity("vendor")
    since = _resolve_since(conn, org_id, since)
    since_date = _since_date(since)

    signals: list[Signal] = []

    # --- sub-metric 1: delay consistency, by route -------------------------
    delay_sql_text = f"""
        SELECT
            t.{trip.column('route_id')} AS route_id,
            r.{route.column('route_code')} AS route_code,
            r.{route.column('name')} AS route_name,
            COUNT(*) AS sample_count,
            AVG(t.{trip.column('delay_minutes')}) AS avg_delay_minutes,
            STDDEV_POP(t.{trip.column('delay_minutes')}) AS stddev_delay_minutes
        FROM {trip.table} t
        JOIN {route.table} r ON r.{route.column('id')} = t.{trip.column('route_id')}
        WHERE t.{trip.column('org_id')} = :org_id
          AND t.{trip.column('trip_date')} >= :since_date
          AND t.{trip.column('delay_minutes')} IS NOT NULL
        GROUP BY t.{trip.column('route_id')}, r.{route.column('route_code')}, r.{route.column('name')}
        HAVING COUNT(*) >= :min_sample_size
    """
    for row in conn.execute(
        text(delay_sql_text), {"org_id": org_id, "since_date": since_date, "min_sample_size": min_sample_size}
    ).mappings().all():
        avg_delay = float(row["avg_delay_minutes"] or 0.0)
        stddev_delay = float(row["stddev_delay_minutes"] or 0.0)
        if abs(avg_delay) < 1.0:
            breach = stddev_delay >= variability_minutes_floor
            cv_pct = None
        else:
            cv_pct = round(100.0 * stddev_delay / abs(avg_delay), 2)
            breach = cv_pct >= cv_threshold_pct
        if not breach:
            continue
        severity = "high" if (cv_pct or 0) >= cv_threshold_pct * 2 else "medium"
        signals.append(
            Signal(
                signal_type="performance_variability",
                entity_type="route",
                entity_id=str(row["route_id"]),
                severity=severity,
                summary=(
                    f"Route {row['route_code']} ({row['route_name']})'s on-time performance is highly "
                    f"inconsistent: mean delay {avg_delay:.1f} min, std-dev {stddev_delay:.1f} min "
                    + (f"(CV {cv_pct:.0f}%) " if cv_pct is not None else "")
                    + f"across {row['sample_count']} trips since {since.date()} -- not a magnitude "
                    "breach, but not predictable either."
                ),
                raw_metric={
                    "metric_name": "delay",
                    "sample_count": row["sample_count"],
                    "avg_delay_minutes": round(avg_delay, 2),
                    "stddev_delay_minutes": round(stddev_delay, 2),
                    "cv_pct": cv_pct,
                    "cv_threshold_pct": cv_threshold_pct,
                    "detector_sql": delay_sql_text,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_variability_signal",
                context={"route_code": row["route_code"], "route_name": row["route_name"]},
            )
        )

    # --- sub-metric 2: distance/route-adherence consistency, by route ------
    distance_sql_text = f"""
        SELECT
            t.{trip.column('route_id')} AS route_id,
            r.{route.column('route_code')} AS route_code,
            r.{route.column('name')} AS route_name,
            COUNT(*) AS sample_count,
            AVG(t.{trip.column('planned_km')}) AS avg_planned_km,
            STDDEV_POP(t.{trip.column('traveled_km')} - t.{trip.column('planned_km')}) AS stddev_deviation_km
        FROM {trip.table} t
        JOIN {route.table} r ON r.{route.column('id')} = t.{trip.column('route_id')}
        WHERE t.{trip.column('org_id')} = :org_id
          AND t.{trip.column('trip_date')} >= :since_date
          AND t.{trip.column('traveled_km')} IS NOT NULL
          AND t.{trip.column('planned_km')} IS NOT NULL
          AND t.{trip.column('planned_km')} > 0
        GROUP BY t.{trip.column('route_id')}, r.{route.column('route_code')}, r.{route.column('name')}
        HAVING COUNT(*) >= :min_sample_size
    """
    for row in conn.execute(
        text(distance_sql_text), {"org_id": org_id, "since_date": since_date, "min_sample_size": min_sample_size}
    ).mappings().all():
        avg_planned = float(row["avg_planned_km"] or 0.0)
        stddev_deviation = float(row["stddev_deviation_km"] or 0.0)
        if avg_planned <= 0:
            continue
        cv_pct = round(100.0 * stddev_deviation / avg_planned, 2)
        if cv_pct < cv_threshold_pct:
            continue
        severity = "high" if cv_pct >= cv_threshold_pct * 2 else "medium"
        signals.append(
            Signal(
                signal_type="performance_variability",
                entity_type="route",
                entity_id=str(row["route_id"]),
                severity=severity,
                summary=(
                    f"Route {row['route_code']} ({row['route_name']})'s actual distance driven is "
                    f"inconsistent relative to its planned distance: std-dev of (traveled - planned) "
                    f"is {stddev_deviation:.1f} km against a {avg_planned:.1f} km planned average "
                    f"(CV {cv_pct:.0f}%) across {row['sample_count']} trips since {since.date()} -- "
                    "possible detours, routing instability, or a data integrity issue."
                ),
                raw_metric={
                    "metric_name": "distance",
                    "sample_count": row["sample_count"],
                    "avg_planned_km": round(avg_planned, 2),
                    "stddev_deviation_km": round(stddev_deviation, 2),
                    "cv_pct": cv_pct,
                    "cv_threshold_pct": cv_threshold_pct,
                    "detector_sql": distance_sql_text,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_variability_signal",
                context={"route_code": row["route_code"], "route_name": row["route_name"]},
            )
        )

    # --- sub-metric 3: cost consistency, by vendor --------------------------
    cost_sql_text = f"""
        SELECT
            c.{cost.column('vendor_id')} AS vendor_id,
            v.{vendor.column('name')} AS vendor_name,
            COUNT(*) AS sample_count,
            AVG(c.{cost.column('cost_per_km')}) AS avg_cost_per_km,
            STDDEV_POP(c.{cost.column('cost_per_km')}) AS stddev_cost_per_km
        FROM {cost.table} c
        JOIN {vendor.table} v ON v.{vendor.column('id')} = c.{cost.column('vendor_id')}
        WHERE c.{cost.column('org_id')} = :org_id
          AND c.{cost.column('cost_date')} >= :since_date
          AND c.{cost.column('cost_per_km')} IS NOT NULL
          AND c.{cost.column('cost_per_km')} > 0
        GROUP BY c.{cost.column('vendor_id')}, v.{vendor.column('name')}
        HAVING COUNT(*) >= :min_sample_size
    """
    for row in conn.execute(
        text(cost_sql_text), {"org_id": org_id, "since_date": since_date, "min_sample_size": min_sample_size}
    ).mappings().all():
        avg_cost = float(row["avg_cost_per_km"] or 0.0)
        stddev_cost = float(row["stddev_cost_per_km"] or 0.0)
        if avg_cost <= 0:
            continue
        cv_pct = round(100.0 * stddev_cost / avg_cost, 2)
        if cv_pct < cv_threshold_pct:
            continue
        severity = "high" if cv_pct >= cv_threshold_pct * 2 else "medium"
        signals.append(
            Signal(
                signal_type="performance_variability",
                entity_type="vendor",
                entity_id=str(row["vendor_id"]),
                severity=severity,
                summary=(
                    f"Vendor {row['vendor_name']}'s per-km billing is inconsistent trip-to-trip: "
                    f"mean INR {avg_cost:.2f}/km, std-dev INR {stddev_cost:.2f}/km (CV {cv_pct:.0f}%) "
                    f"across {row['sample_count']} trips since {since.date()} -- worth an invoice "
                    "audit even though no single trip crosses the absolute billing-discrepancy threshold."
                ),
                raw_metric={
                    "metric_name": "cost",
                    "sample_count": row["sample_count"],
                    "avg_cost_per_km": round(avg_cost, 2),
                    "stddev_cost_per_km": round(stddev_cost, 2),
                    "cv_pct": cv_pct,
                    "cv_threshold_pct": cv_threshold_pct,
                    "detector_sql": cost_sql_text,
                },
                org_id=org_id,
                detected_at=_utcnow(),
                source="detect_variability_signal",
                context={"vendor_name": row["vendor_name"]},
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
    since = _resolve_since(conn, org_id, since)
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
