"""GET/PUT /api/settings/rules, GET /api/settings/usage (plan SP-B §4) --
the Settings page's backend: read/write per-signal-type thresholds/gate-mode
/cadence (`alert_rules`) and global gate policy (`gate_settings`), plus an
at-a-glance usage/health snapshot (today's LLM calls vs. budget, today's
gate-action funnel, false-positive rates, and suppression-rate warnings).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import default_org_id
from app.api.schemas import (
    GateSettingsModel,
    RulesResponse,
    RulesUpdateRequest,
    SignalRuleParams,
    UsageStatsResponse,
)
from app.contracts import get_contract
from app.graph.act.db import get_engine
from app.graph.sense import nodes as detectors
from app.llm.provider import get_daily_call_count
from app.rules import invalidate_cache
from app.services import gate_stats

router = APIRouter(prefix="/settings", tags=["settings"])

# Every signal_type the Settings page shows a card for, including the two
# Major Risk Hotspot ones (plan §B0) whose gate_mode/notification_cadence
# the frontend renders disabled/pinned -- listed here so GET /rules always
# returns a full, consistent set of cards even for signal_types with no
# alert_rules row yet (defaults, per app.rules.loader's own fallback
# contract).
_ALL_SIGNAL_TYPES = (
    "incident",
    "escort_compliance_violation",
    "delay_breach",
    "cost_divergence",
    "emissions_over_target",
    # SP-B fix: "attendance_correlated_with_transport" is the sole canonical
    # key -- it carries both the shared detector's threshold params AND its
    # own gate_mode/cadence (see subgraph.py's _RULES_KEY_BY_DETECTOR).
    # "attendance_unrelated_late" is a real, separately-dispatched Signal
    # type from the same detector, so it still gets its own gate_mode/
    # cadence card (a team's "unrelated lateness" notifications can be
    # suppressed/batched independently of its "transport-correlated" ones)
    # -- it just has no params of its own to tune.
    "attendance_correlated_with_transport",
    "attendance_unrelated_late",
    "billing_discrepancy",
    "performance_variability",
)

# BUGFIX: with no alert_rules row yet (a fresh org -- confirmed live: the
# table has 0 rows today), the old fallback returned params={} for every
# card. SignalRuleCard only renders an <input> for keys present in
# rule.params, so an empty dict meant NO threshold field ever appeared --
# the Settings page could toggle gate_mode/cadence but could never show or
# edit an actual number. Populate the fallback with each detector's real
# DEFAULT_* constant (imported, not re-literaled, so this can't drift from
# the value actually in effect) so first-load always shows the
# currently-active threshold, pre-filled and editable.
_DEFAULT_PARAMS_BY_SIGNAL_TYPE: dict[str, dict[str, Any]] = {
    "incident": {"severity_threshold": detectors.DEFAULT_INCIDENT_SEVERITY_THRESHOLD},
    "escort_compliance_violation": {
        "violation_limit": detectors.DEFAULT_ESCORT_VIOLATION_SIGNAL_LIMIT,
        "night_window_start_hour": detectors.NIGHT_WINDOW_START_HOUR,
        "night_window_end_hour": detectors.NIGHT_WINDOW_END_HOUR,
        "drop_delay_critical_minutes": detectors.DEFAULT_DELAY_BREACH_MINUTES,
    },
    "delay_breach": {"delay_threshold_minutes": detectors.DEFAULT_DELAY_BREACH_MINUTES},
    "cost_divergence": {"divergence_pct": detectors.DEFAULT_COST_DIVERGENCE_PCT},
    "emissions_over_target": {"min_ratio_over_baseline": 1.05},
    "attendance_correlated_with_transport": {
        "delay_threshold_minutes": detectors.DEFAULT_DELAY_BREACH_MINUTES,
        "min_late_samples": detectors.DEFAULT_MIN_LATE_SAMPLES,
        "signal_limit": detectors.DEFAULT_ATTENDANCE_SIGNAL_LIMIT,
        "transport_correlation_ratio": detectors.DEFAULT_TRANSPORT_CORRELATION_RATIO,
        "unrelated_correlation_ratio": detectors.DEFAULT_UNRELATED_CORRELATION_RATIO,
    },
    "attendance_unrelated_late": {},
    "billing_discrepancy": {
        "min_slab_sample": detectors.DEFAULT_MIN_SLAB_SAMPLE,
        "min_discrepancy_inr": detectors.DEFAULT_MIN_DISCREPANCY_INR,
    },
    "performance_variability": {
        "cv_threshold_pct": detectors.DEFAULT_VARIABILITY_CV_THRESHOLD_PCT,
        "min_sample_size": detectors.DEFAULT_VARIABILITY_MIN_SAMPLE_SIZE,
        "variability_minutes_floor": detectors.DEFAULT_VARIABILITY_MINUTES_FLOOR,
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/rules", response_model=RulesResponse)
def get_rules_endpoint() -> RulesResponse:
    org_id = default_org_id()
    engine = get_engine()

    contract = get_contract().entity("alert_rule")
    table, c = contract.table, contract.column
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT {c('signal_type')} AS signal_type, {c('params')} AS params, "
                f"{c('gate_mode')} AS gate_mode, {c('notification_cadence')} AS notification_cadence, "
                f"{c('updated_at')} AS updated_at, {c('updated_by')} AS updated_by "
                f"FROM {table} WHERE {c('org_id')} = :org_id"
            ),
            {"org_id": org_id},
        ).mappings().all()

    by_signal_type: dict[str, dict[str, Any]] = {row["signal_type"]: dict(row) for row in rows}

    signal_rules = []
    for signal_type in _ALL_SIGNAL_TYPES:
        row = by_signal_type.get(signal_type)
        if row is not None:
            signal_rules.append(
                SignalRuleParams(
                    signal_type=signal_type,
                    params=row["params"] if isinstance(row["params"], dict) else {},
                    gate_mode=row["gate_mode"],
                    notification_cadence=row["notification_cadence"],
                    updated_at=row["updated_at"].isoformat(),
                    updated_by=row["updated_by"],
                )
            )
        else:
            # No row yet -- defaults, per app.rules.loader's own fallback
            # contract. incident/escort_compliance_violation default to
            # auto/immediate here same as everything else; the *enforcement*
            # of their non-overridability lives in gate.py's safety floor,
            # not in what this default row shows.
            signal_rules.append(
                SignalRuleParams(
                    signal_type=signal_type,
                    params=dict(_DEFAULT_PARAMS_BY_SIGNAL_TYPE.get(signal_type, {})),
                    gate_mode="auto",
                    notification_cadence="immediate",
                    updated_at=_now_iso(),
                    updated_by=None,
                )
            )

    gs = _load_gate_settings_row(engine, org_id)
    return RulesResponse(signal_rules=signal_rules, gate_settings=gs)


def _load_gate_settings_row(engine, org_id: str) -> GateSettingsModel:
    contract = get_contract().entity("gate_setting")
    table, c = contract.table, contract.column
    cols = [
        "recurrence_window_hours",
        "recurrence_suppress_after",
        "max_consecutive_suppressions",
        "rule_only_margin_ratio",
        "max_fp_rate_for_rule_only",
        "min_confidence_for_rule_only",
        "max_healthy_suppression_rate",
        "escalation_after_hours_critical",
        "escalation_after_hours_high",
        "escalation_after_hours_medium",
        "updated_at",
        "updated_by",
    ]
    select_list = ", ".join(f"{c(name)} AS {name}" for name in cols)
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {select_list} FROM {table} WHERE {c('org_id')} = :org_id"), {"org_id": org_id}
        ).mappings().first()

    if row is None:
        from app.rules import GateSettings

        defaults = GateSettings()
        return GateSettingsModel(
            recurrence_window_hours=defaults.recurrence_window_hours,
            recurrence_suppress_after=defaults.recurrence_suppress_after,
            max_consecutive_suppressions=defaults.max_consecutive_suppressions,
            rule_only_margin_ratio=defaults.rule_only_margin_ratio,
            max_fp_rate_for_rule_only=defaults.max_fp_rate_for_rule_only,
            min_confidence_for_rule_only=defaults.min_confidence_for_rule_only,
            max_healthy_suppression_rate=defaults.max_healthy_suppression_rate,
            escalation_after_hours_critical=defaults.escalation_after_hours_critical,
            escalation_after_hours_high=defaults.escalation_after_hours_high,
            escalation_after_hours_medium=defaults.escalation_after_hours_medium,
            updated_at=_now_iso(),
            updated_by=None,
        )

    return GateSettingsModel(
        recurrence_window_hours=int(row["recurrence_window_hours"]),
        recurrence_suppress_after=int(row["recurrence_suppress_after"]),
        max_consecutive_suppressions=int(row["max_consecutive_suppressions"]),
        rule_only_margin_ratio=float(row["rule_only_margin_ratio"]),
        max_fp_rate_for_rule_only=float(row["max_fp_rate_for_rule_only"]),
        min_confidence_for_rule_only=float(row["min_confidence_for_rule_only"]),
        max_healthy_suppression_rate=float(row["max_healthy_suppression_rate"]),
        escalation_after_hours_critical=float(row["escalation_after_hours_critical"]),
        escalation_after_hours_high=float(row["escalation_after_hours_high"]),
        escalation_after_hours_medium=float(row["escalation_after_hours_medium"]),
        updated_at=row["updated_at"].isoformat(),
        updated_by=row["updated_by"],
    )


@router.put("/rules", response_model=RulesResponse)
def update_rules_endpoint(body: RulesUpdateRequest) -> RulesResponse:
    org_id = default_org_id()
    engine = get_engine()

    if body.signal_rules:
        contract = get_contract().entity("alert_rule")
        table, c = contract.table, contract.column
        with engine.begin() as conn:
            for rule in body.signal_rules:
                conn.execute(
                    text(
                        f"INSERT INTO {table} ({c('org_id')}, {c('signal_type')}, {c('params')}, "
                        f"{c('gate_mode')}, {c('notification_cadence')}, {c('updated_by')}) "
                        f"VALUES (:org_id, :signal_type, :params, :gate_mode, :notification_cadence, :updated_by) "
                        f"ON CONFLICT ({c('org_id')}, {c('signal_type')}) DO UPDATE SET "
                        f"{c('params')} = EXCLUDED.{c('params')}, {c('gate_mode')} = EXCLUDED.{c('gate_mode')}, "
                        f"{c('notification_cadence')} = EXCLUDED.{c('notification_cadence')}, "
                        f"{c('updated_by')} = EXCLUDED.{c('updated_by')}, {c('updated_at')} = now()"
                    ),
                    {
                        "org_id": org_id,
                        "signal_type": rule.signal_type,
                        "params": json.dumps(rule.params),
                        "gate_mode": rule.gate_mode,
                        "notification_cadence": rule.notification_cadence,
                        "updated_by": body.updated_by or rule.updated_by,
                    },
                )

    if body.gate_settings:
        gs = body.gate_settings
        contract = get_contract().entity("gate_setting")
        table, c = contract.table, contract.column
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {table} ({c('org_id')}, {c('recurrence_window_hours')}, "
                    f"{c('recurrence_suppress_after')}, {c('max_consecutive_suppressions')}, "
                    f"{c('rule_only_margin_ratio')}, {c('max_fp_rate_for_rule_only')}, "
                    f"{c('min_confidence_for_rule_only')}, {c('max_healthy_suppression_rate')}, "
                    f"{c('escalation_after_hours_critical')}, {c('escalation_after_hours_high')}, "
                    f"{c('escalation_after_hours_medium')}, {c('updated_by')}) "
                    f"VALUES (:org_id, :recurrence_window_hours, :recurrence_suppress_after, "
                    f":max_consecutive_suppressions, :rule_only_margin_ratio, :max_fp_rate_for_rule_only, "
                    f":min_confidence_for_rule_only, :max_healthy_suppression_rate, "
                    f":escalation_after_hours_critical, :escalation_after_hours_high, "
                    f":escalation_after_hours_medium, :updated_by) "
                    f"ON CONFLICT ({c('org_id')}) DO UPDATE SET "
                    f"{c('recurrence_window_hours')} = EXCLUDED.{c('recurrence_window_hours')}, "
                    f"{c('recurrence_suppress_after')} = EXCLUDED.{c('recurrence_suppress_after')}, "
                    f"{c('max_consecutive_suppressions')} = EXCLUDED.{c('max_consecutive_suppressions')}, "
                    f"{c('rule_only_margin_ratio')} = EXCLUDED.{c('rule_only_margin_ratio')}, "
                    f"{c('max_fp_rate_for_rule_only')} = EXCLUDED.{c('max_fp_rate_for_rule_only')}, "
                    f"{c('min_confidence_for_rule_only')} = EXCLUDED.{c('min_confidence_for_rule_only')}, "
                    f"{c('max_healthy_suppression_rate')} = EXCLUDED.{c('max_healthy_suppression_rate')}, "
                    f"{c('escalation_after_hours_critical')} = EXCLUDED.{c('escalation_after_hours_critical')}, "
                    f"{c('escalation_after_hours_high')} = EXCLUDED.{c('escalation_after_hours_high')}, "
                    f"{c('escalation_after_hours_medium')} = EXCLUDED.{c('escalation_after_hours_medium')}, "
                    f"{c('updated_by')} = EXCLUDED.{c('updated_by')}, {c('updated_at')} = now()"
                ),
                {
                    "org_id": org_id,
                    "recurrence_window_hours": gs.recurrence_window_hours,
                    "recurrence_suppress_after": gs.recurrence_suppress_after,
                    "max_consecutive_suppressions": gs.max_consecutive_suppressions,
                    "rule_only_margin_ratio": gs.rule_only_margin_ratio,
                    "max_fp_rate_for_rule_only": gs.max_fp_rate_for_rule_only,
                    "min_confidence_for_rule_only": gs.min_confidence_for_rule_only,
                    "max_healthy_suppression_rate": gs.max_healthy_suppression_rate,
                    "escalation_after_hours_critical": gs.escalation_after_hours_critical,
                    "escalation_after_hours_high": gs.escalation_after_hours_high,
                    "escalation_after_hours_medium": gs.escalation_after_hours_medium,
                    "updated_by": body.updated_by,
                },
            )

    invalidate_cache(org_id)
    return get_rules_endpoint()


@router.get("/usage", response_model=UsageStatsResponse)
def get_usage_stats() -> UsageStatsResponse:
    org_id = default_org_id()
    engine = get_engine()
    provider = os.environ.get("LLM_PROVIDER", "sarvam")
    daily_limit = int(os.environ.get("LLM_DAILY_CALL_LIMIT", "500"))
    redis_url = os.environ["REDIS_URL"]

    fp_rates = gate_stats.false_positive_rate_by_signal_type(engine, org_id, days=30)
    suppression_rates = gate_stats.suppression_rate_by_signal_type(engine, org_id, days=7)
    gs = _load_gate_settings_row(engine, org_id)

    warnings = [
        f"'{row['signal_type']}' has suppressed {row['suppression_rate_pct']:.0f}% of signals "
        f"in the last 7 days -- check thresholds aren't hiding real issues."
        for row in suppression_rates
        if row["total_count"] >= 5 and row["suppression_rate_pct"] / 100.0 > gs.max_healthy_suppression_rate
    ]

    return UsageStatsResponse(
        llm_calls_today=get_daily_call_count(provider, redis_url),
        llm_daily_limit=daily_limit,
        gate_counts_today=gate_stats.gate_counts_today(engine, org_id),
        false_positive_rate_by_signal_type=[
            {
                "signal_type": row["signal_type"],
                "dispatched_count": row["dispatched_count"],
                "false_positive_count": row["false_positive_count"],
                "false_positive_rate_pct": row["false_positive_rate_pct"],
            }
            for row in fp_rates
        ],
        suppression_warnings=warnings,
    )
