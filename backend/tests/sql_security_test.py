"""Standalone harness for app.graph.reason.sql_agent.security.enforce_select_only
-- the "hard security boundary" (per that module's own docstring) standing
between LLM-generated SQL and the real database.

This file DOCUMENTS the guard's current behavior; it does not fix anything.
No change is made anywhere in app/graph/reason/sql_agent/ by this file.

- Group 1 (happy path) and Group 2 (already-blocked) are ordinary regression
  checks: they assert today's correct behavior stays correct.
- Group 3 is a KNOWN-GAPS INVENTORY, not a todo list this suite is trying to
  close. Each check there asserts that a query enforce_select_only currently
  ACCEPTS keeps being accepted -- i.e. these checks pass today specifically
  because the gap is still open. Two things can make this suite fail later,
  both intentional:
    1. security.py starts blocking one of these (Group 3 assertion flips) --
       forces whoever made that change to notice and update this file,
       turning an incidental behavior change into a deliberate, visible one.
    2. security.py stops blocking one of the Group 2 cases -- the intended
       regression alarm for an actual security bug.

Every Group 3 case here was verified live against this repo's installed
sqlglot before being written down: each one parses as a clean, single
`exp.Select` with zero hits against enforce_select_only's forbidden-node
walk, because the walk only looks for DML/DDL statement nodes
(Insert/Update/Delete/Drop/Alter/Create/TruncateTable/Grant/Command) -- a
side-effecting function call (lo_export, pg_terminate_backend, set_config,
...) or a reference to a table outside the data contract (checkpoints,
agent_notifications, ...) is neither, so none of them are examined at all.

Run from the backend/ directory:

    python -m tests.sql_security_test

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import sys

from app.graph.reason.sql_agent.security import (
    SQLSecurityError,
    SQLSyntaxError,
    enforce_select_only,
)

_FAILURES: list[str] = []


def check(condition: bool, description: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _FAILURES.append(description)


def _accepted(sql: str, *, row_limit: int = 200):
    """Returns the CheckedQuery on success, None if enforce_select_only raised."""
    try:
        return enforce_select_only(sql, row_limit=row_limit)
    except (SQLSecurityError, SQLSyntaxError):
        return None


def _rejected(sql: str, *, row_limit: int = 200) -> bool:
    return _accepted(sql, row_limit=row_limit) is None


def run() -> None:
    print("=== Group 1: happy path (regression -- must stay accepted) ===")
    result = _accepted("SELECT * FROM mis.trip WHERE org_id = 'x'")
    check(result is not None, "plain contract-table SELECT is accepted")
    check(result is not None and "LIMIT 200" in result.safe_sql, "missing LIMIT is injected as row_limit")

    result = _accepted("SELECT * FROM mis.trip LIMIT 5000", row_limit=200)
    check(result is not None and "LIMIT 200" in result.safe_sql, "an over-large LIMIT is clamped down to row_limit")

    result = _accepted("SELECT t.id, c.total_cost_inr FROM mis.trip t JOIN mis.cost c ON c.trip_id = t.id")
    check(result is not None, "a join across two real tables is accepted")

    result = _accepted("WITH t AS (SELECT id FROM mis.trip) SELECT * FROM t")
    check(result is not None, "a plain read-only CTE reference is accepted")

    print()
    print("=== Group 2: already-blocked today (regression -- must stay blocked) ===")
    check(_rejected("SELECT 1; DROP TABLE mis.trip;"), "stacked statements via ';' are rejected")
    check(_rejected("INSERT INTO mis.trip (id) VALUES (1)"), "direct INSERT is rejected")
    check(_rejected("UPDATE mis.trip SET traveled_km = 0"), "direct UPDATE is rejected")
    check(_rejected("DELETE FROM mis.trip"), "direct DELETE is rejected")
    check(_rejected("DROP TABLE mis.trip"), "direct DROP is rejected")
    check(_rejected("ALTER TABLE mis.trip ADD COLUMN x int"), "direct ALTER is rejected")
    check(_rejected("CREATE TABLE evil (id int)"), "direct CREATE is rejected")
    check(_rejected("TRUNCATE mis.trip"), "direct TRUNCATE is rejected")
    check(_rejected("GRANT ALL ON mis.trip TO PUBLIC"), "direct GRANT is rejected")
    check(
        _rejected("WITH t AS (INSERT INTO mis.trip (id) VALUES (1) RETURNING id) SELECT * FROM t"),
        "a writable CTE (INSERT nested inside a WITH clause) is rejected -- "
        "verified sqlglot's .walk() visits the nested Insert node",
    )

    print()
    print("=== Group 3: KNOWN OPEN GAPS (documented, not enforced -- see module docstring) ===")
    check(
        not _rejected("SELECT lo_export(loid, '/tmp/pwned') FROM mis.trip LIMIT 1"),
        "KNOWN GAP: lo_export(...) (arbitrary filesystem write) passes enforce_select_only unblocked",
    )
    check(
        not _rejected("SELECT lo_import('/etc/passwd')"),
        "KNOWN GAP: lo_import(...) (arbitrary filesystem read) passes enforce_select_only unblocked",
    )
    check(
        not _rejected("SELECT dblink_exec('dbname=moveinsync', 'DELETE FROM mis.trip')"),
        "KNOWN GAP: dblink_exec(...) (arbitrary SQL execution over a second connection) passes unblocked",
    )
    check(
        not _rejected("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"),
        "KNOWN GAP: pg_terminate_backend(...) (kill an arbitrary backend -- DoS) passes unblocked",
    )
    check(
        not _rejected("SELECT pg_cancel_backend(pid) FROM pg_stat_activity"),
        "KNOWN GAP: pg_cancel_backend(...) (cancel an arbitrary in-flight query -- DoS) passes unblocked",
    )
    check(
        not _rejected("SELECT set_config('statement_timeout', '0', false)"),
        "KNOWN GAP: set_config(...) (can defeat the statement-timeout mitigation on a pooled connection) passes unblocked",
    )
    check(
        not _rejected("SELECT pg_sleep(5)"),
        "KNOWN GAP: pg_sleep(...) (DoS, only partially mitigated by statement_timeout) passes unblocked",
    )
    check(
        not _rejected("SELECT PG_SLEEP(5)"),
        "KNOWN GAP: case-varied PG_SLEEP(...) still resolves to the same bare function name -- not a narrow edge case",
    )
    check(
        not _rejected("SELECT pg_catalog.pg_terminate_backend(pid) FROM pg_stat_activity"),
        "KNOWN GAP: schema-qualified pg_catalog.pg_terminate_backend(...) still resolves to the same bare function name",
    )
    check(
        not _rejected("SELECT * FROM checkpoints LIMIT 5"),
        "KNOWN GAP: querying 'checkpoints' (LangGraph's own internal state) passes unblocked -- "
        "include_tables= only shapes the LLM's prompt context, it is not an execution-time allowlist",
    )
    check(
        not _rejected("SELECT * FROM public.agent_notifications LIMIT 5"),
        "KNOWN GAP: querying 'agent_notifications' (draft communications, approver identities) passes unblocked",
    )

    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} check(s) FAILED:")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All SQL-security harness checks passed (Group 3 passing means those gaps are still open, by design of this harness).")


if __name__ == "__main__":
    run()
