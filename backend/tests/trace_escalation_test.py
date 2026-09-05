"""Standalone test for the escalated-notification trace path
(app.services.trace_builder.build_trace).

Regression cover for a live 404: `check_escalations` promotes an
unacknowledged notification to the next persona by INSERTing a row with a
synthetic `{persona}:{scope}:escalated-{original_id}` thread_id, and never
runs the graph under that thread_id -- so there is no checkpoint history to
read. build_trace handles that by recursing into the ORIGINAL notification's
thread, but when the original row is gone (or its own checkpoint history has
been trimmed) it returned [], which GET /threads/{id}/trace turns into a 404.
The escalated row is still sitting in somebody's inbox, so "no trace found"
is the one answer that must never come back for it.

Run from the backend/ directory:

    python -m tests.trace_escalation_test

Uses a dedicated throwaway org_id (never a real business unit) and deletes
every row it writes on exit, even on failure.

Exit code is non-zero if any assertion fails.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from app.contracts import get_contract
from app.graph.act.db import get_engine
from app.services.trace_builder import build_trace

TEST_ORG_ID = "trace-escalation-test"
_FAILURES: list[str] = []


def check(condition: bool, description: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _FAILURES.append(description)


def _insert_notification(conn, **kwargs) -> int:
    contract = get_contract().entity("notification")
    table, c = contract.table, contract.column
    cols = ", ".join(c(k) for k in kwargs)
    binds = ", ".join(f":{k}" for k in kwargs)
    return conn.execute(
        text(f"INSERT INTO {table} ({cols}) VALUES ({binds}) RETURNING {c('id')}"),
        kwargs,
    ).scalar_one()


def _cleanup(engine) -> None:
    contract = get_contract().entity("notification")
    table, c = contract.table, contract.column
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table} WHERE {c('org_id')} = :org"), {"org": TEST_ORG_ID})


def main() -> int:
    engine = get_engine()
    _cleanup(engine)

    try:
        with engine.begin() as conn:
            # An escalated row whose original has been deleted -- exactly the
            # live shape: the promoted row exists and is visible, the id its
            # thread_id points at does not resolve to anything.
            orphan_id = _insert_notification(
                conn,
                org_id=TEST_ORG_ID,
                persona="transport_manager",
                scope="team:999",
                severity="warning",
                title="[Escalated] Employee 999 has been late 9 times",
                message=(
                    "Employee 999 has been late 9 times since 2026-07-24, but the data shows zero "
                    "transport-caused incidents. -- escalated to transport_manager after 4h "
                    "unacknowledged by line_manager."
                ),
                status="open",
                thread_id="transport_manager:team:999:escalated-999999999",
                related_entity_type="escalation",
            )
            print(f"  seeded orphaned escalation notification id={orphan_id}")

        steps = build_trace("transport_manager:team:999:escalated-999999999")

        check(bool(steps), "an escalated thread with no resolvable original still returns a trace")
        if steps:
            kinds = [s.get("step") for s in steps]
            check(
                "escalation" in kinds,
                f"the trace carries an 'escalation' step (got {kinds})",
            )
            escalation = next((s for s in steps if s.get("step") == "escalation"), None)
            if escalation:
                check(
                    bool(escalation.get("detail")),
                    "the escalation step explains itself in its detail text",
                )
                check(
                    bool(escalation.get("timestamp")),
                    "the escalation step carries a timestamp",
                )
                check(
                    "line_manager" in escalation.get("detail", ""),
                    "the detail names who failed to acknowledge it",
                )

        # A thread_id that is genuinely unknown must still produce nothing, so
        # the 404 remains meaningful for real typos/bad ids.
        unknown = build_trace("transport_manager:team:999:does-not-exist-at-all")
        check(unknown == [], "an unknown, non-escalated thread still returns no trace")
    finally:
        _cleanup(engine)
        print("  cleaned up test rows")

    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} check(s) FAILED:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
