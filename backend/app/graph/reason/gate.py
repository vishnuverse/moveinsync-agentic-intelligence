"""The SP-B filtering gate (plan §1): classifies each (signal, persona) pair
into suppress / rule_only / escalate BEFORE reason's LLM call would run,
implementing docs/BACKLOG.md's unbuilt "confidence x severity -> action
matrix + noise reduction" item.

No LLM import here, deliberately -- this module only does cheap arithmetic
over fields detectors already compute plus two read-only SQL lookups (via
app.services.gate_stats). It is a pure decision function: it does not write
anything itself (the caller, app.graph.supervisor.run_pipeline, calls
app.graph.act.db.log_gate_decision separately so every evaluation is logged
regardless of the action taken).

False-negative safeguards (plan §1, stated as an explicit design
requirement, not an afterthought): every suppression path below has a hard
ceiling that forces a real problem back into view --
  * a scope can never be suppressed indefinitely (suppression-heartbeat,
    step 3 below, reached only when gate_mode == "auto" -- it does not
    override an operator's *explicit* force_suppress, since that is
    informed intent, not the gate's own automatic judgement);
  * `rule_only` requires a *proven*, not merely *unknown*, low
    false-positive rate -- a signal_type/scope with no dispatch history yet
    defaults to `escalate`, never to "assume it's safe."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Engine

from app.graph.sense.state import Signal
from app.rules import GateSettings, SignalRules
from app.services import gate_stats

GateAction = Literal["suppress", "rule_only", "escalate"]

# Cross-referenced with root_cause.py's _FORCE_SIGNOFF_SIGNAL_TYPES /
# _FORCE_SIGNOFF_SEVERITIES (backend/app/graph/reason/root_cause.py) -- same
# safety floor, enforced one step earlier here so the LLM call for these is
# never skipped in the first place, and an operator's Settings-page
# gate_mode/notification_cadence override cannot suppress or batch them
# either (see app.graph.supervisor's use of this same set for the cadence
# floor).
ALWAYS_ESCALATE_SIGNAL_TYPES = {"incident", "escort_compliance_violation"}
ALWAYS_ESCALATE_SEVERITIES = {"critical"}

# Per-signal_type sample-size field on Signal.raw_metric, used by step 4
# (insufficient sample size) and available to callers that want to display
# "N trips this window" alongside a decision. Not every signal_type has a
# natural sample-size field (incident/escort_compliance_violation don't --
# they never reach this step, the safety floor claims them first).
_SAMPLE_SIZE_FIELD_BY_SIGNAL_TYPE: dict[str, str] = {
    "delay_breach": "trip_count",
    "cost_divergence": "sample_count",
    "emissions_over_target": "sample_count",
    "attendance_correlated_with_transport": "late_count",
    "attendance_unrelated_late": "late_count",
    "billing_discrepancy": "flagged_trip_count",
    "performance_variability": "sample_count",
}


@dataclass(frozen=True)
class GateDecision:
    action: GateAction
    reason: str
    confidence: float
    matched_rule: str


def _magnitude_ratio(signal: Signal, rules: SignalRules | None) -> float | None:
    """How far over its configured threshold this signal is, as a ratio
    (1.0 == exactly at the line). None when the signal_type has no defined
    ratio formula (safety-floor types never call this) or a required
    raw_metric field is missing."""
    raw = signal.raw_metric or {}
    rules = rules or SignalRules(signal_type=signal.signal_type)

    if signal.signal_type == "delay_breach":
        avg_delay = raw.get("avg_delay_minutes")
        threshold = rules.get("delay_threshold_minutes", raw.get("delay_threshold_minutes"))
        if avg_delay is None or not threshold:
            return None
        return float(avg_delay) / float(threshold)

    if signal.signal_type == "cost_divergence":
        vs_contract = raw.get("divergence_vs_contract_pct") or 0.0
        vs_fleet = raw.get("divergence_vs_fleet_pct") or 0.0
        divergence_pct = rules.get("divergence_pct", 0.20)
        configured_pct_units = float(divergence_pct) * 100.0
        if not configured_pct_units:
            return None
        return max(float(vs_contract), float(vs_fleet)) / configured_pct_units

    if signal.signal_type == "emissions_over_target":
        pct_above = raw.get("pct_above_baseline")
        if pct_above is None:
            return None
        return float(pct_above) / 5.0

    if signal.signal_type in ("attendance_correlated_with_transport", "attendance_unrelated_late"):
        ratio = raw.get("correlation_ratio")
        return float(ratio) if ratio is not None else None

    if signal.signal_type == "billing_discrepancy":
        amount = raw.get("discrepancy_amount_inr")
        min_discrepancy = rules.get("min_discrepancy_inr", 500.0)
        if amount is None or not min_discrepancy:
            return None
        return float(amount) / float(min_discrepancy)

    if signal.signal_type == "performance_variability":
        cv_pct = raw.get("cv_pct")
        cv_threshold_pct = rules.get("cv_threshold_pct", 20.0)
        if cv_pct is None or not cv_threshold_pct:
            return None
        return float(cv_pct) / float(cv_threshold_pct)

    return None


def evaluate_gate(
    signal: Signal,
    *,
    persona: str,
    scope: str,
    engine: Engine,
    org_id: str,
    rules: SignalRules | None,
    gate_settings: GateSettings,
) -> GateDecision:
    """Decision order, first match wins -- see module docstring for the
    false-negative-safeguard rationale behind steps 3 and 6."""

    # 1. Safety floor -- checked first, cannot be overridden by anything below.
    if signal.signal_type in ALWAYS_ESCALATE_SIGNAL_TYPES or signal.severity in ALWAYS_ESCALATE_SEVERITIES:
        return GateDecision(
            action="escalate",
            reason=f"'{signal.signal_type}' (severity={signal.severity}) is a non-overridable safety-critical signal type.",
            confidence=1.0,
            matched_rule="safety_floor",
        )

    gate_mode = rules.gate_mode if rules else "auto"

    # 2. Operator override -- an explicit, informed choice; not touched by
    # the automatic heartbeat/recurrence/sample-size logic below.
    if gate_mode != "auto":
        forced_action: GateAction = {
            "force_suppress": "suppress",
            "force_rule_only": "rule_only",
            "force_escalate": "escalate",
        }[gate_mode]
        return GateDecision(
            action=forced_action,
            reason=f"Operator override: gate_mode='{gate_mode}' for signal_type='{signal.signal_type}'.",
            confidence=1.0,
            matched_rule="operator_override",
        )

    # 3. Suppression heartbeat -- a scope the gate's OWN automatic logic has
    # suppressed max_consecutive_suppressions times in a row is forced back
    # to escalate, so a recurring real problem can never go dark
    # indefinitely just because each individual occurrence looks
    # "unremarkable" by the recurrence rule's own logic.
    consecutive = gate_stats.consecutive_suppression_count(engine, org_id, persona, signal.signal_type, scope)
    if consecutive >= gate_settings.max_consecutive_suppressions:
        return GateDecision(
            action="escalate",
            reason=(
                f"This scope has been auto-suppressed {consecutive} times in a row "
                f"(>= max_consecutive_suppressions={gate_settings.max_consecutive_suppressions}) -- "
                "forcing a full reasoning pass so it doesn't go dark indefinitely."
            ),
            confidence=0.9,
            matched_rule="suppression_heartbeat",
        )

    # 4. Insufficient sample size.
    min_sample_size = rules.get("min_sample_size") if rules else None
    if min_sample_size:
        sample_field = _SAMPLE_SIZE_FIELD_BY_SIGNAL_TYPE.get(signal.signal_type)
        sample_value = (signal.raw_metric or {}).get(sample_field) if sample_field else None
        if sample_value is not None and sample_value < min_sample_size:
            return GateDecision(
                action="suppress",
                reason=f"Sample size {sample_value} is below the configured floor of {min_sample_size}.",
                confidence=0.7,
                matched_rule="insufficient_sample",
            )

    # 5. Recurrence / hysteresis -- keyed on `scope` (e.g. "route:RT-001"),
    # distinct from the permanent, exact-entity_id dedup
    # (notification_exists_for_thread) applied before this gate ever runs:
    # that one blocks the SAME entity forever; this one recognizes when a
    # PATTERN against the same scope has already been established recently,
    # so repeated distinct violations (many different unescorted trips on
    # the same route, say) stop re-triggering full reasoning once the
    # pattern is known -- until the heartbeat above forces it back up.
    recent_dispatch_count = _recent_dispatch_count(
        engine, org_id, persona, signal.signal_type, scope, gate_settings.recurrence_window_hours
    )
    if recent_dispatch_count >= gate_settings.recurrence_suppress_after:
        return GateDecision(
            action="suppress",
            reason=(
                f"This scope already had {recent_dispatch_count} dispatched decision(s) in the last "
                f"{gate_settings.recurrence_window_hours}h -- pattern already established."
            ),
            confidence=0.6,
            matched_rule="recurrence",
        )

    # 6. High-margin breach with a PROVEN (not merely unknown) low
    # false-positive rate.
    ratio = _magnitude_ratio(signal, rules)
    fp_rate = gate_stats.false_positive_rate_for(engine, org_id, signal.signal_type, days=30)
    if (
        ratio is not None
        and ratio >= gate_settings.rule_only_margin_ratio
        and fp_rate is not None
        and fp_rate <= gate_settings.max_fp_rate_for_rule_only
    ):
        confidence = min(0.95, 0.5 + (ratio - gate_settings.rule_only_margin_ratio) * 0.1)
        if confidence >= gate_settings.min_confidence_for_rule_only:
            return GateDecision(
                action="rule_only",
                reason=(
                    f"Magnitude ratio {ratio:.2f}x clears rule_only_margin_ratio="
                    f"{gate_settings.rule_only_margin_ratio}, with a proven "
                    f"{fp_rate:.0%} false-positive rate over the last 30 days."
                ),
                confidence=round(confidence, 3),
                matched_rule="high_margin_breach",
            )

    # 7. Default -- today's existing behavior, unchanged.
    return GateDecision(
        action="escalate",
        reason="No suppress/rule_only condition matched -- full reasoning pass.",
        confidence=0.5,
        matched_rule="default_escalate",
    )


def _recent_dispatch_count(
    engine: Engine, org_id: str, persona: str, signal_type: str, scope: str, window_hours: int
) -> int:
    from sqlalchemy import text

    from app.contracts import get_contract

    gd = get_contract().entity("gate_decision")
    t, c = gd.table, gd.column
    sql = f"""
        SELECT COUNT(*) FROM {t}
        WHERE {c('org_id')} = :org_id AND {c('persona')} = :persona
          AND {c('signal_type')} = :signal_type AND {c('scope')} = :scope
          AND {c('action')} IN ('rule_only', 'escalate')
          AND {c('created_at')} > now() - (:window_hours || ' hours')::interval
    """
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(sql),
                {"org_id": org_id, "persona": persona, "signal_type": signal_type, "scope": scope, "window_hours": window_hours},
            ).scalar()
            or 0
        )
