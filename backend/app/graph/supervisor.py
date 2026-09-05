"""Supervisor: the top-level entry points other code imports (plan §12 step 9,
§4's "light supervisor node").

Two public callables:

    run_pipeline(org_id, since=None, event=None) -> list[dict]
        Runs sense once, routes each resulting signal to the persona(s) it's
        relevant to (plan §1's feature mapping), and runs reason->act
        (app.graph.graph.build_top_graph) once per (signal, persona) pair.
        This is what a scheduler tick or a LISTEN/NOTIFY event calls -- the
        "no human prompt required" autonomy path (plan §4).

    run_chat_turn(question, persona, org_id, thread_id=None) -> dict
        Runs reason->act for one raw NL question, bypassing sense -- the seam
        a future chat endpoint (TM2/LM2/TH4) calls.

    run_report(org_id, persona, report_type="digest", period_label=None) -> dict
        Generates a period-aggregated HTML report (TM4's daily digest, TH3's
        leadership report) by invoking the act subgraph directly with
        action_type="report" -- deliberately NOT routed through run_pipeline's
        per-signal fan-out, since a digest summarizes a *period* of recent
        decisions, it isn't a reaction to one specific signal. Called by the
        scheduler on its own (coarser) cadence (app.schedulers.reports),
        independent of the interval/event signal-detection paths -- this is
        what makes TM4/TH3 actually reachable autonomously (flagged as a gap
        by the FastAPI-integration agent's build report, fixed here rather
        than left as a follow-up).

Design note on why sense->reason->act is an *orchestrating function* here
rather than sense also being a node in one single StateGraph (the plan gives
explicit latitude for either shape): `run_sense()` is a fan-out producer --
one call yields N signals, each potentially relevant to more than one
persona, each needing its OWN thread_id/checkpoint history so a judge (or a
future /threads/{id}/trace endpoint, plan §10) can inspect one signal's
reasoning chain independently. That 1-call-to-N-graph-runs shape doesn't
map onto a single linear StateGraph invocation without inventing an awkward
inner Send/fan-out construct for no real benefit -- a plain Python loop
calling the compiled reason->act graph (app.graph.graph.build_top_graph) once
per thread is simpler, exactly as inspectable per-thread, and is what's
actually invoked/verified. graph.py itself remains a real, independently
compiled StateGraph (reason -> bridge_to_act -> act) -- the "one compiled
graph" the plan describes for the reason->act portion -- this module is what
drives it per signal.

Thread ID scheme (plan §4): f"{persona}:{scope}:{signal_or_question_ref}".
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.checkpointer import get_checkpointer
from app.contracts import get_contract
from app.graph.act import build_act_subgraph
from app.graph.act.db import get_engine as get_act_engine
from app.graph.act.db import log_gate_decision, notification_exists_for_thread
from app.graph.reason.gate import ALWAYS_ESCALATE_SEVERITIES, ALWAYS_ESCALATE_SIGNAL_TYPES, evaluate_gate
from app.graph.sense import Signal, run_sense
from app.rules import get_gate_settings, get_rules
from app.services.notifications_query import list_notifications

from .graph import build_top_graph
from .state import TopState

# report_type -> which persona's recent notifications feed the digest (plan
# §1 TM4/TH3). Kept as a small lookup here rather than making the caller pass
# a persona explicitly for report_type too -- there is exactly one sensible
# persona per report_type in this project's feature set. Values must match
# agent_reports.report_type's CHECK constraint (backend/db/schema.sql) --
# BUGFIX (found live: the scheduler's first real report_tick threw
# IntegrityError, "leadership_report" isn't in that constraint's allowed
# list) -- "monthly_leadership" is.
REPORT_TYPE_PERSONA: dict[str, str] = {
    "daily_digest": "transport_manager",
    # GAP (found during a plan-vs-implementation audit): plan §1's LM2 is
    # explicitly "Team-scoped Q&A + weekly digest" -- the weekly_digest value
    # already existed in agent_reports' CHECK constraint (schema.sql) but was
    # never added here, so Line Manager was the one persona with zero
    # scheduled reports.
    "weekly_digest": "line_manager",
    "monthly_leadership": "transport_head",
}

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Persona routing (plan §1's feature list drives this mapping):
#   TM1/TM5 (live delay alerts, incident escalation)      -> transport_manager
#   TM3 (route/vendor reallocation) + TH1 (vendor scorecard) both read a
#       cost/vendor-performance divergence, so cost_divergence fans out to
#       both personas rather than picking one.
#   LM1 (team commute-attendance correlation)               -> line_manager
#   TH2 (carbon footprint vs. sustainability goal)           -> transport_head
#   PRD v3 F1 (escort compliance / active panic alert)       -> transport_manager
#   PRD v3 F2 (billing slab/distance discrepancy)            -> transport_head
#   performance_variability (plan SP-B §B2, new): consistency/std-dev anomaly
#       across delay, distance, and cost sub-metrics -- all operational-
#       reliability concerns a transport manager would take to the vendor.
#       Deliberately not also routed to transport_head the way
#       cost_divergence is, to avoid duplicate notification/LLM volume for a
#       single-persona rollout; revisit if Transport Head asks for it.
#   data_quality_issue is logged (backend/db's data_quality_flags, written by
#       the sense layer itself) but is not persona-actionable on its own --
#       no reason/act dispatch for it here.
# ---------------------------------------------------------------------------
# SP-B escalation hierarchy (plan §A1): a real reporting hierarchy --
# Transport & Facilities Head (strategic, most senior) -> Transport Manager
# (operational, reports to the Head) -> Team/Line Manager (shift-level, most
# localized) -- confirmed against the persona roles' own stated LEVEL 1/2/3
# framing (Team/Floor Manager -> Transport Manager -> Head of Transport &
# Facilities). An `open` notification nobody acknowledges within its
# severity's timeout (gate_settings.escalation_after_hours_*) is promoted to
# the next persona up this chain -- see check_escalations below. The Head has
# no `None` -- nothing above them to escalate to.
ESCALATION_CHAIN: dict[str, str | None] = {
    "line_manager": "transport_manager",
    "transport_manager": "transport_head",
    "transport_head": None,
}

_PERSONA_ROUTES: dict[str, tuple[str, ...]] = {
    "delay_breach": ("transport_manager",),
    "incident": ("transport_manager",),
    "cost_divergence": ("transport_manager", "transport_head"),
    "emissions_over_target": ("transport_head",),
    "attendance_correlated_with_transport": ("line_manager",),
    "attendance_unrelated_late": ("line_manager",),
    "escort_compliance_violation": ("transport_manager",),
    "billing_discrepancy": ("transport_head",),
    "performance_variability": ("transport_manager",),
    "data_quality_issue": (),
}


def personas_for_signal(signal: Signal) -> tuple[str, ...]:
    return _PERSONA_ROUTES.get(signal.signal_type, ("transport_manager",))


def derive_scope(signal: Signal) -> str:
    ctx = signal.context or {}
    if ctx.get("route_code"):
        return f"route:{ctx['route_code']}"
    if ctx.get("vendor_name"):
        return f"vendor:{ctx['vendor_name']}"
    if ctx.get("team_id") is not None:
        return f"team:{ctx['team_id']}"
    return f"{signal.entity_type}:{signal.entity_id}"


def build_thread_id(persona: str, scope: str, ref: str) -> str:
    """`{persona}:{scope}:{ref}` (plan §4) -- the same string is used both as
    LangGraph's `configurable.thread_id` (so PostgresSaver keys checkpoint
    history by it) and, via act/db.py's upsert_notification, as the
    idempotency key on the `agent_notifications`/`agent_reports` row that
    thread's run produces. A future `GET /threads/{thread_id}/trace` (plan
    §10) reads the same value back out of the checkpointer."""
    return f"{persona}:{scope}:{ref}"


def _question_ref(question: str) -> str:
    return hashlib.sha1(question.strip().lower().encode("utf-8")).hexdigest()[:10]


# agent_notifications.severity is info/warning/critical (not Signal's own
# low/medium/high/critical) -- 'info' is deliberately excluded from
# escalation entirely (nothing to promote urgently), 'warning' uses the
# "high" timeout bucket as the closest match.
_ESCALATION_TIMEOUT_ATTR_BY_SEVERITY: dict[str, str] = {
    "critical": "escalation_after_hours_critical",
    "warning": "escalation_after_hours_high",
}


def check_escalations(engine: Any, org_id: str, gate_settings: Any) -> list[dict[str, Any]]:
    """SP-B escalation hierarchy (plan §A1): folded into the same scheduler
    tick as the main signal loop (called at the end of run_pipeline) rather
    than a separate scheduler process -- this is a small, additive check
    (one SELECT + up to a few INSERT/UPDATE pairs per tick), not a new
    subsystem. Promotes an `open`, not-yet-escalated notification to the
    next persona in ESCALATION_CHAIN once it's sat unacknowledged past its
    severity's configured timeout -- a false-negative safeguard (plan
    Context): the system correctly detected a problem, but nothing forced it
    in front of a more senior owner if the first-line persona missed it.

    Escalated notifications are always `notification_cadence="immediate"`
    (escalation exists specifically to interrupt a senior owner right now --
    a batched escalation defeats the purpose) and inherit the original's
    severity. Returns a summary list, one entry per escalation performed,
    for the caller's own tick-summary/activity-log purposes.
    """
    from app.rules import compute_scheduled_for

    contract = get_contract().entity("notification")
    table, c = contract.table, contract.column
    escalations: list[dict[str, Any]] = []

    with engine.begin() as conn:
        for severity, timeout_attr in _ESCALATION_TIMEOUT_ATTR_BY_SEVERITY.items():
            timeout_hours = getattr(gate_settings, timeout_attr)
            rows = conn.execute(
                text(
                    f"SELECT {c('id')} AS id, {c('persona')} AS persona, {c('scope')} AS scope, "
                    f"{c('title')} AS title, {c('message')} AS message, {c('thread_id')} AS thread_id "
                    f"FROM {table} "
                    f"WHERE {c('org_id')} = :org_id AND {c('status')} = 'open' "
                    f"AND {c('severity')} = :severity AND {c('escalated_at')} IS NULL "
                    f"AND {c('created_at')} < now() - (:timeout_hours || ' hours')::interval"
                ),
                {"org_id": org_id, "severity": severity, "timeout_hours": timeout_hours},
            ).mappings().all()

            for row in rows:
                next_persona = ESCALATION_CHAIN.get(row["persona"])
                if not next_persona:
                    continue  # already at the top of the chain (transport_head)

                # Idempotency: guarded by the WHERE clause above
                # (`escalated_at IS NULL`), not a DB constraint -- the
                # original row is stamped with escalated_at immediately
                # after this INSERT, in the same transaction, so a re-run of
                # this function can never select the same original row twice.
                escalated_thread_id = f"{next_persona}:{row['scope']}:escalated-{row['id']}"
                conn.execute(
                    text(
                        f"INSERT INTO {table} ({c('org_id')}, {c('persona')}, {c('scope')}, "
                        f"{c('severity')}, {c('title')}, {c('message')}, {c('status')}, "
                        f"{c('thread_id')}, {c('scheduled_for')}, {c('related_entity_type')}) "
                        f"VALUES (:org_id, :persona, :scope, :severity, :title, :message, 'open', "
                        f":thread_id, NULL, 'escalation')"
                    ),
                    {
                        "org_id": org_id,
                        "persona": next_persona,
                        "scope": row["scope"],
                        "severity": severity,
                        "title": f"[Escalated] {row['title']}",
                        "message": (
                            f"{row['message']} -- escalated to {next_persona} after "
                            f"{timeout_hours:.0f}h unacknowledged by {row['persona']}."
                        ),
                        "thread_id": escalated_thread_id,
                    },
                )
                conn.execute(
                    text(
                        f"UPDATE {table} SET {c('escalated_at')} = now(), "
                        f"{c('escalated_to_persona')} = :next_persona WHERE {c('id')} = :id"
                    ),
                    {"next_persona": next_persona, "id": row["id"]},
                )
                escalations.append(
                    {
                        "original_notification_id": row["id"],
                        "from_persona": row["persona"],
                        "to_persona": next_persona,
                        "severity": severity,
                        "escalation_reason": "timeout",
                        "thread_id": escalated_thread_id,
                    }
                )

    if escalations:
        logger.info("check_escalations: org=%s escalated %d notification(s)", org_id, len(escalations))
    return escalations


def run_pipeline(org_id: str, since: datetime | None = None, event: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The top-level autonomy entry point (plan §4/§12 step 9): one sense pass,
    then reason->act once per (signal, persona). Called by both scheduler
    paths in app.schedulers/ (interval tick and LISTEN/NOTIFY event) and
    directly for manual verification. Returns a summary list -- one entry per
    (signal, persona) dispatch -- describing what was detected, decided, and
    acted on, including whether the run paused for human sign-off."""
    checkpointer = get_checkpointer()
    top_graph = build_top_graph(checkpointer)
    # get_act_engine() (app.graph.act.db.get_engine) creates a brand-new
    # SQLAlchemy Engine/connection-pool per call -- built once here, not
    # inside the per-signal loop below (up to hundreds of iterations against
    # real data), to avoid spinning up hundreds of throwaway pools per tick.
    act_engine = get_act_engine()
    # SP-B (plan §1/§2): resolved once per tick, not per signal -- both are
    # cached with a short TTL (app.rules.loader) so a Settings-page change
    # still takes effect within one tick without re-querying per signal.
    rules_by_signal_type = get_rules(act_engine, org_id)
    gate_settings = get_gate_settings(act_engine, org_id)

    sense_result = run_sense(org_id=org_id, since=since, event=event)
    signals: list[Signal] = sense_result.get("signals", [])

    summary: list[dict[str, Any]] = []
    for signal in signals:
        if signal.signal_type == "data_quality_issue":
            summary.append(
                {
                    "signal_type": signal.signal_type,
                    "entity_type": signal.entity_type,
                    "entity_id": signal.entity_id,
                    "action": "logged_only",
                }
            )
            continue

        scope = derive_scope(signal)
        ref = f"{signal.signal_type}-{signal.entity_id}"

        for persona in personas_for_signal(signal):
            thread_id = build_thread_id(persona, scope, ref)

            # See app.graph.act.db.notification_exists_for_thread's own
            # docstring for the incident this guards against: without it,
            # every tick re-reasons (real LLM call) about every signal a
            # detector's rolling window still returns, including ones
            # already notified on a prior tick -- verified live this is the
            # common case (a signal's underlying row doesn't disappear from
            # the window just because it was already handled).
            if notification_exists_for_thread(act_engine, org_id=org_id, thread_id=thread_id):
                summary.append(
                    {
                        "signal_type": signal.signal_type,
                        "entity_type": signal.entity_type,
                        "entity_id": signal.entity_id,
                        "persona": persona,
                        "thread_id": thread_id,
                        "action": "skipped_already_processed",
                    }
                )
                continue

            # SP-B gate (plan §1): decides suppress/rule_only/escalate BEFORE
            # the graph (and its guaranteed-until-now LLM call) ever runs.
            # Logged for every evaluation regardless of the action taken --
            # see log_gate_decision's own docstring for why a `suppress`
            # still needs a row (it never produces an agent_notifications
            # one, so this is the only audit trail for recurrence tracking).
            gate_decision = evaluate_gate(
                signal,
                persona=persona,
                scope=scope,
                engine=act_engine,
                org_id=org_id,
                rules=rules_by_signal_type.get(signal.signal_type),
                gate_settings=gate_settings,
            )
            log_gate_decision(
                act_engine,
                org_id=org_id,
                persona=persona,
                signal_type=signal.signal_type,
                scope=scope,
                entity_id=signal.entity_id,
                severity=signal.severity,
                thread_id=thread_id,
                action=gate_decision.action,
                reason=gate_decision.reason,
                matched_rule=gate_decision.matched_rule,
                confidence=gate_decision.confidence,
            )

            if gate_decision.action == "suppress":
                summary.append(
                    {
                        "signal_type": signal.signal_type,
                        "entity_type": signal.entity_type,
                        "entity_id": signal.entity_id,
                        "persona": persona,
                        "thread_id": thread_id,
                        "action": "suppressed_by_gate",
                        "gate_reason": gate_decision.reason,
                    }
                )
                continue

            initial_state: TopState = {
                "org_id": org_id,
                "persona": persona,
                "signal": signal.to_dict(),
                "scope": scope,
                "thread_id": thread_id,
            }
            # SP-B cadence (plan §3/§A1): the safety floor (mirrored from
            # gate.py's own -- incident/escort_compliance_violation/critical
            # always escalate) also pins cadence to "immediate" regardless of
            # any alert_rules override, for the same reason: a safety-
            # critical alert must never be silently held for a batch.
            if signal.signal_type in ALWAYS_ESCALATE_SIGNAL_TYPES or signal.severity in ALWAYS_ESCALATE_SEVERITIES:
                initial_state["notification_cadence"] = "immediate"
            else:
                signal_rules = rules_by_signal_type.get(signal.signal_type)
                initial_state["notification_cadence"] = signal_rules.notification_cadence if signal_rules else "immediate"

            if gate_decision.action == "rule_only":
                initial_state["gate_mode"] = "rule_only"
                initial_state["gate_reason"] = gate_decision.reason
                initial_state["gate_confidence"] = gate_decision.confidence
            # gate_decision.action == "escalate" leaves gate_mode unset
            # entirely -- zero behavior change, byte-for-byte the same as
            # the pipeline's original (pre-SP-B) path.

            config = {"configurable": {"thread_id": thread_id}}
            try:
                result = top_graph.invoke(initial_state, config=config)
            except Exception:  # noqa: BLE001 - one bad thread must not stop the whole pipeline pass
                logger.exception("run_pipeline: reason->act failed for thread %s", thread_id)
                summary.append(
                    {
                        "signal_type": signal.signal_type,
                        "entity_id": signal.entity_id,
                        "persona": persona,
                        "thread_id": thread_id,
                        "action": "error",
                    }
                )
                continue

            decision = result.get("decision", {}) or {}
            summary.append(
                {
                    "signal_type": signal.signal_type,
                    "entity_type": signal.entity_type,
                    "entity_id": signal.entity_id,
                    "severity": signal.severity,
                    "persona": persona,
                    "scope": scope,
                    "thread_id": thread_id,
                    "action_type": result.get("action_type"),
                    "decision_summary": decision.get("summary"),
                    "needs_human_signoff": decision.get("needs_human_signoff", False),
                    "paused": "__interrupt__" in result,
                    "notification_id": result.get("notification_id"),
                    "dispatch_status": result.get("dispatch_status"),
                }
            )

    # SP-B escalation hierarchy (plan §A1): folded into the same tick, after
    # the main signal loop -- checks for anything unacknowledged past its
    # severity's timeout, independent of whatever signals this particular
    # tick did or didn't detect.
    try:
        escalations = check_escalations(act_engine, org_id, gate_settings)
        summary.extend({**e, "action": "escalated"} for e in escalations)
    except Exception:  # noqa: BLE001 - one bad escalation check must not fail the whole tick
        logger.exception("run_pipeline: check_escalations failed for org %s", org_id)

    return summary


def run_chat_turn(question: str, persona: str, org_id: str, thread_id: str | None = None) -> dict[str, Any]:
    """Read-only reason-only pass for one raw NL question, bypassing sense
    (plan §12 step 9, chat endpoint backing TM2/LM2/TH4).

    Chat is strictly a Q&A surface: it must never write a permanent
    `agent_notifications` row or trip the HITL interrupt_gate. So it sets
    `skip_act=True`, which makes the top graph stop right after `reason`
    (see app.graph.graph._route_after_reason) -- act never runs. reason still
    executes under this thread_id, so the "How was this computed?" trace
    (app/services/trace_builder.py reading get_state_history) is unchanged.

    Because act didn't run, there is no notification_id and the turn can never
    pause -- those degrade to None/False below rather than being read off a
    result that never went through act."""
    checkpointer = get_checkpointer()
    top_graph = build_top_graph(checkpointer)

    scope = "chat"
    resolved_thread_id = thread_id or build_thread_id(persona, scope, _question_ref(question))
    initial_state: TopState = {
        "org_id": org_id,
        "persona": persona,
        "question": question,
        "scope": scope,
        "thread_id": resolved_thread_id,
        "skip_act": True,
    }
    config = {"configurable": {"thread_id": resolved_thread_id}}
    result = top_graph.invoke(initial_state, config=config)

    decision = result.get("decision", {}) or {}
    sql_result = result.get("sql_result", {}) or {}
    return {
        "thread_id": resolved_thread_id,
        "answer": decision.get("summary") or sql_result.get("answer", ""),
        "decision": decision,
        "generated_sql": sql_result.get("generated_sql"),
        # act never runs for a chat turn (skip_act=True), so there is no
        # notification row and no possible interrupt -- degrade explicitly
        # instead of reading keys act would have produced.
        "notification_id": None,
        "needs_human_signoff": decision.get("needs_human_signoff", False),
        "paused": False,
    }


def _decision_like(item: dict[str, Any]) -> dict[str, Any]:
    """Adapts a list_notifications() row (frontend-shaped: severity/title/
    message/status/...) into act's ReasonDecision shape (act/state.py) for
    html_report_generator's report_items input -- the notification IS the
    already-contextualized output of a prior reason pass, so re-deriving a
    fresh ReasonDecision per item would just re-run reasoning that already
    happened; this reuses it instead."""
    return {
        "summary": item.get("title") or item.get("message", ""),
        "root_cause": item.get("message", ""),
        "recommendation": "",
        "confidence": 1.0,
        "needs_human_signoff": item.get("status") == "needs_intervention",
        "target_persona": item.get("persona", ""),
        "supporting_evidence": {
            "notification_id": item.get("id"),
            "severity": item.get("severity"),
            "scope": item.get("scope"),
        },
    }


def run_report(
    org_id: str,
    persona: str,
    report_type: str = "digest",
    period_label: str | None = None,
) -> dict[str, Any]:
    """Period-aggregated report generation (plan TM4/TH3) -- invokes the act
    subgraph directly with action_type="report", bypassing reason (there is
    no single signal to reason about; the report items are already-reasoned
    recent notifications for this persona). See module docstring."""
    checkpointer = get_checkpointer()
    act_graph = build_act_subgraph(checkpointer=checkpointer)

    recent = list_notifications(org_id, persona, limit=25)
    report_items = [_decision_like(item) for item in recent]

    label = period_label or datetime.utcnow().strftime("%Y-%m-%d")
    scope = f"report:{report_type}"
    thread_id = build_thread_id(persona, scope, label)
    initial_state = {
        "org_id": org_id,
        "persona": persona,
        "action_type": "report",
        "scope": scope,
        "thread_id": thread_id,
        "report_items": report_items,
        "period_label": label,
        "report_type": report_type,
        "use_llm_narrative": True,
    }
    config = {"configurable": {"thread_id": thread_id}}
    result = act_graph.invoke(initial_state, config=config)

    return {
        "thread_id": thread_id,
        "report_id": result.get("report_id"),
        "report_storage_ref": result.get("report_storage_ref"),
        "dispatch_status": result.get("dispatch_status"),
        "item_count": len(report_items),
    }
