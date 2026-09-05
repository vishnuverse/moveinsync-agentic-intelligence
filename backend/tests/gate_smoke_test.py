"""Standalone smoke test for the SP-B LLM-filtering gate (plan §1/§6),
following backend/app/llm/smoke_test.py's "prove the block, not just log it"
discipline: every assertion below is checked against the same Redis
`llm_calls:{provider}:{day}` counter that module inspects, not against a log
line or a print statement.

Run from the backend/ directory:

    python -m tests.gate_smoke_test

Uses a dedicated throwaway org_id (never a real business unit) so nothing
here can be confused with real organizational data, and cleans up every row
it writes on exit (best-effort, even on failure).

Exit code is non-zero if any assertion fails.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import redis
from sqlalchemy import text

TEST_ORG_ID = "sp-b-smoke-test"
_FAILURES: list[str] = []


def check(condition: bool, description: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _FAILURES.append(description)


def _llm_call_count() -> int:
    provider = os.environ.get("LLM_PROVIDER", "sarvam")
    r = redis.Redis.from_url(os.environ["REDIS_URL"])
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    value = r.get(f"llm_calls:{provider}:{day}")
    return int(value) if value is not None else 0


def _cleanup(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM gate_decisions WHERE org_id = :org"), {"org": TEST_ORG_ID})
        conn.execute(text("DELETE FROM agent_notifications WHERE org_id = :org"), {"org": TEST_ORG_ID})
        conn.execute(text("DELETE FROM alert_rules WHERE org_id = :org"), {"org": TEST_ORG_ID})
        conn.execute(text("DELETE FROM gate_settings WHERE org_id = :org"), {"org": TEST_ORG_ID})


def _make_signal(**overrides):
    from app.graph.sense.state import Signal

    defaults = dict(
        signal_type="delay_breach",
        entity_type="route",
        entity_id="RT-SMOKE",
        severity="high",
        summary="Smoke-test delay signal",
        raw_metric={"trip_count": 50, "avg_delay_minutes": 45.0, "delay_threshold_minutes": 15.0},
        org_id=TEST_ORG_ID,
        detected_at=datetime.now(timezone.utc),
        source="test",
    )
    defaults.update(overrides)
    return Signal(**defaults)


def run() -> None:
    from app.graph.act.db import get_engine, upsert_notification
    from app.graph.reason.gate import evaluate_gate
    from app.graph.supervisor import run_pipeline
    from app.rules import GateSettings, SignalRules, get_gate_settings, invalidate_cache

    engine = get_engine()
    _cleanup(engine)
    invalidate_cache(TEST_ORG_ID)

    try:
        gs = get_gate_settings(engine, TEST_ORG_ID)

        print("=== 1. Suppress never touches the LLM counter ===")
        before = _llm_call_count()
        low_sample_rules = SignalRules(signal_type="delay_breach", params={"min_sample_size": 100})
        with patch(
            "app.graph.supervisor.run_sense",
            return_value={"signals": [_make_signal(entity_id="RT-SUPPRESS", raw_metric={"trip_count": 5, "avg_delay_minutes": 45.0})]},
        ), patch("app.graph.supervisor.get_rules", return_value={"delay_breach": low_sample_rules}):
            run_pipeline(TEST_ORG_ID)
        after = _llm_call_count()
        check(after == before, f"suppress: LLM counter unchanged ({before} -> {after})")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT action FROM gate_decisions WHERE org_id=:org AND entity_id='RT-SUPPRESS'"),
                {"org": TEST_ORG_ID},
            ).mappings().first()
        check(row is not None and row["action"] == "suppress", "suppress: gate_decisions row logged with action='suppress'")
        with engine.connect() as conn:
            notif = conn.execute(
                text("SELECT 1 FROM agent_notifications WHERE org_id=:org"), {"org": TEST_ORG_ID}
            ).first()
        check(notif is None, "suppress: no agent_notifications row was created")

        print()
        print("=== 2. Rule-only dispatches a real notification but never touches the LLM counter ===")
        # Seed a proven, clean (zero-false-positive) dispatch history for
        # delay_breach so step 5 of evaluate_gate has a real fp_rate to work
        # with (plan §1's "unknown risk defaults to escalate" rule means a
        # fresh signal_type would otherwise never reach rule_only).
        seed_thread = f"transport_manager:route:RT-SEED:delay_breach-seed"
        upsert_notification(
            engine, org_id=TEST_ORG_ID, persona="transport_manager", scope="route:RT-SEED",
            severity="warning", title="seed", message="seed", status="resolved", thread_id=seed_thread,
        )
        from app.graph.act.db import log_gate_decision

        log_gate_decision(
            engine, org_id=TEST_ORG_ID, persona="transport_manager", signal_type="delay_breach",
            scope="route:RT-SEED", entity_id="RT-SEED", severity="high", thread_id=seed_thread,
            action="escalate", reason="seed", matched_rule="default_escalate", confidence=0.5,
        )
        high_margin_rules = SignalRules(signal_type="delay_breach", params={"delay_threshold_minutes": 15.0})
        loose_gate_settings = GateSettings(rule_only_margin_ratio=2.0, max_fp_rate_for_rule_only=0.5, min_confidence_for_rule_only=0.5)
        before = _llm_call_count()
        with patch(
            "app.graph.supervisor.run_sense",
            return_value={"signals": [_make_signal(entity_id="RT-RULEONLY", raw_metric={"trip_count": 50, "avg_delay_minutes": 60.0, "delay_threshold_minutes": 15.0})]},
        ), patch("app.graph.supervisor.get_rules", return_value={"delay_breach": high_margin_rules}), patch(
            "app.graph.supervisor.get_gate_settings", return_value=loose_gate_settings
        ):
            run_pipeline(TEST_ORG_ID)
        after = _llm_call_count()
        check(after == before, f"rule_only: LLM counter unchanged ({before} -> {after})")
        with engine.connect() as conn:
            notif = conn.execute(
                text("SELECT status FROM agent_notifications WHERE org_id=:org AND thread_id LIKE '%RT-RULEONLY%'"),
                {"org": TEST_ORG_ID},
            ).mappings().first()
        check(notif is not None, "rule_only: a real agent_notifications row was created despite skipping the LLM")

        print()
        print("=== 3. Escalate still calls the LLM (no regression) ===")
        # NOTE: a single escalated signal can cost more than one LLM call
        # (route_to_specialist may route to call_sql_agent, which itself
        # makes its own SQL-generation + answer-synthesis calls, before
        # root_cause_synthesizer's own call) -- verified live this varies by
        # signal, so the invariant this asserts is "at least one call
        # happened" (the LLM was NOT skipped), not an exact count.
        before = _llm_call_count()
        with patch(
            "app.graph.supervisor.run_sense",
            return_value={"signals": [_make_signal(entity_id="RT-ESCALATE", raw_metric={"trip_count": 50, "avg_delay_minutes": 20.0, "delay_threshold_minutes": 15.0})]},
        ), patch("app.graph.supervisor.get_rules", return_value={}):
            run_pipeline(TEST_ORG_ID)
        after = _llm_call_count()
        check(after > before, f"escalate: LLM counter increased ({before} -> {after}) -- the LLM was not skipped")

        print()
        print("=== 4. Safety floor resists an operator's force_suppress override ===")
        critical_incident = _make_signal(
            signal_type="incident", entity_type="incident", entity_id="INC-SMOKE", severity="critical", raw_metric={}
        )
        forced_suppress_rules = SignalRules(signal_type="incident", gate_mode="force_suppress")
        decision = evaluate_gate(
            critical_incident, persona="transport_manager", scope="incident:INC-SMOKE", engine=engine,
            org_id=TEST_ORG_ID, rules=forced_suppress_rules, gate_settings=gs,
        )
        check(decision.action == "escalate", f"safety floor: action is 'escalate' despite force_suppress (got '{decision.action}')")
        check(decision.matched_rule == "safety_floor", f"safety floor: matched_rule is 'safety_floor' (got '{decision.matched_rule}')")

        print()
        print("=== 5. Aggregate invariant: suppressed signals contribute zero LLM calls, escalated ones contribute >0 ===")
        batch_signals = [
            _make_signal(entity_id="BATCH-1", severity="low", raw_metric={"trip_count": 2, "avg_delay_minutes": 5.0}),  # insufficient sample -> suppress
            _make_signal(entity_id="BATCH-2", raw_metric={"trip_count": 50, "avg_delay_minutes": 16.0, "delay_threshold_minutes": 15.0}),  # default -> escalate
        ]
        batch_rules = {"delay_breach": SignalRules(signal_type="delay_breach", params={"min_sample_size": 10})}
        before = _llm_call_count()
        with patch("app.graph.supervisor.run_sense", return_value={"signals": batch_signals}), patch(
            "app.graph.supervisor.get_rules", return_value=batch_rules
        ):
            summary = run_pipeline(TEST_ORG_ID)
        after = _llm_call_count()
        escalate_count = sum(1 for s in summary if s.get("action") not in ("suppressed_by_gate", "logged_only", "skipped_already_processed", "error"))
        suppressed = [s for s in summary if s.get("action") == "suppressed_by_gate"]
        check(len(suppressed) == 1, f"batch: exactly 1 of 2 signals was suppressed (insufficient sample) -- got {len(suppressed)}")
        check(escalate_count == 1, f"batch: exactly 1 of 2 signals escalated -- got {escalate_count}")
        check(after > before, f"batch: the escalated signal's LLM call(s) registered ({before} -> {after})")

    finally:
        _cleanup(engine)
        invalidate_cache(TEST_ORG_ID)

    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} check(s) FAILED:")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All gate smoke-test checks passed.")


if __name__ == "__main__":
    run()
