"""Shared queries against `gate_decisions` (plan SP-B §1/§4/§9b) -- used by
both `app.graph.reason.gate`'s own false-positive-rate lookup (step 5 of
`evaluate_gate`) and `GET /api/settings/usage`, so the SQL for "how
trustworthy has rule_only been for this signal_type" is never duplicated.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, text

from app.contracts import get_contract


def false_positive_rate_by_signal_type(
    engine: Engine, org_id: str, *, days: int = 30
) -> list[dict[str, Any]]:
    """One row per signal_type that has at least one dispatched
    (rule_only/escalate) gate decision in the window, with its false-positive
    rate against `agent_notifications.is_false_positive`. A signal_type with
    zero dispatched decisions in the window is simply absent -- callers treat
    absence as "unknown," never as "0% false positives" (see gate.py's
    explicit "unknown risk defaults to escalate" rule)."""
    gd = get_contract().entity("gate_decision")
    notif = get_contract().entity("notification")
    gd_t, gd_c = gd.table, gd.column
    n_t, n_c = notif.table, notif.column

    sql = f"""
        SELECT gd.{gd_c('signal_type')} AS signal_type,
               COUNT(*) AS dispatched_count,
               COUNT(*) FILTER (WHERE n.{n_c('is_false_positive')}) AS false_positive_count
        FROM {gd_t} gd
        JOIN {n_t} n
          ON n.{n_c('thread_id')} = gd.{gd_c('thread_id')}
         AND n.{n_c('org_id')} = gd.{gd_c('org_id')}
        WHERE gd.{gd_c('org_id')} = :org_id
          AND gd.{gd_c('action')} IN ('rule_only', 'escalate')
          AND gd.{gd_c('created_at')} > now() - (:days || ' days')::interval
        GROUP BY gd.{gd_c('signal_type')}
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"org_id": org_id, "days": days}).mappings().all()

    result = []
    for row in rows:
        dispatched = int(row["dispatched_count"])
        fp = int(row["false_positive_count"])
        result.append(
            {
                "signal_type": row["signal_type"],
                "dispatched_count": dispatched,
                "false_positive_count": fp,
                "false_positive_rate_pct": round(100.0 * fp / dispatched, 1) if dispatched else 0.0,
            }
        )
    return result


def false_positive_rate_for(
    engine: Engine, org_id: str, signal_type: str, *, days: int = 30
) -> float | None:
    """Convenience single-signal_type wrapper used by gate.py step 5. Returns
    None (unknown) when nothing has been dispatched for this signal_type in
    the window -- gate.py treats None as "not yet proven safe," never as
    "assume 0% false positives."""
    for row in false_positive_rate_by_signal_type(engine, org_id, days=days):
        if row["signal_type"] == signal_type and row["dispatched_count"] > 0:
            return row["false_positive_rate_pct"] / 100.0
    return None


def suppression_rate_by_signal_type(
    engine: Engine, org_id: str, *, days: int = 7
) -> list[dict[str, Any]]:
    """One row per signal_type evaluated at all in the window, with what
    fraction of evaluations resolved to `suppress` -- backs the Settings
    page's suppression-rate sanity-check banner (plan §1's false-negative
    safeguard: a human-visible warning, never an automatic threshold change)."""
    gd = get_contract().entity("gate_decision")
    t, c = gd.table, gd.column

    sql = f"""
        SELECT {c('signal_type')} AS signal_type,
               COUNT(*) AS total_count,
               COUNT(*) FILTER (WHERE {c('action')} = 'suppress') AS suppressed_count
        FROM {t}
        WHERE {c('org_id')} = :org_id
          AND {c('created_at')} > now() - (:days || ' days')::interval
        GROUP BY {c('signal_type')}
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"org_id": org_id, "days": days}).mappings().all()

    result = []
    for row in rows:
        total = int(row["total_count"])
        suppressed = int(row["suppressed_count"])
        result.append(
            {
                "signal_type": row["signal_type"],
                "total_count": total,
                "suppressed_count": suppressed,
                "suppression_rate_pct": round(100.0 * suppressed / total, 1) if total else 0.0,
            }
        )
    return result


def gate_counts_today(engine: Engine, org_id: str) -> dict[str, int]:
    """`{"suppress": n, "rule_only": n, "escalate": n}` for today (UTC) --
    backs GET /api/settings/usage's at-a-glance funnel summary."""
    gd = get_contract().entity("gate_decision")
    t, c = gd.table, gd.column
    sql = f"""
        SELECT {c('action')} AS action, COUNT(*) AS n
        FROM {t}
        WHERE {c('org_id')} = :org_id AND {c('created_at')} >= date_trunc('day', now())
        GROUP BY {c('action')}
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"org_id": org_id}).mappings().all()
    counts = {"suppress": 0, "rule_only": 0, "escalate": 0}
    for row in rows:
        counts[row["action"]] = int(row["n"])
    return counts


def consecutive_suppression_count(
    engine: Engine, org_id: str, persona: str, signal_type: str, scope: str
) -> int:
    """How many `suppress` decisions in a row this (persona, signal_type,
    scope) key has accumulated since its last non-suppress decision (or since
    the beginning of history, if it has never had one). Backs gate.py's
    suppression-heartbeat rule: once this reaches
    `gate_settings.max_consecutive_suppressions`, the next evaluation is
    forced to `escalate` regardless of steps 1-5, so a recurring real problem
    can never go dark indefinitely."""
    gd = get_contract().entity("gate_decision")
    t, c = gd.table, gd.column

    sql = f"""
        SELECT {c('action')} AS action
        FROM {t}
        WHERE {c('org_id')} = :org_id AND {c('persona')} = :persona
          AND {c('signal_type')} = :signal_type AND {c('scope')} = :scope
        ORDER BY {c('created_at')} DESC
        LIMIT 1000
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(sql), {"org_id": org_id, "persona": persona, "signal_type": signal_type, "scope": scope}
        ).scalars().all()

    count = 0
    for action in rows:
        if action != "suppress":
            break
        count += 1
    return count
