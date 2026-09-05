"""Golden Q&A regression set for the chat pipeline (structural, not value-based).

Run from the backend/ directory:

    python -m tests.golden_qa

What it guards (all assertions are STRUCTURAL -- the underlying data changes
over time, so it never asserts an exact figure):

  * SCOPE (T2): every in-scope business domain -- derived DYNAMICALLY from the
    active data contract's entity list, never hardcoded -- returns a non-empty
    answer. When the transport pushes generated SQL back (direct mode), that
    SQL must parse as a single read-only SELECT (via the same
    app.graph.reason.sql_agent.security.enforce_select_only guard the pipeline
    uses) and the answer must contain at least one numeric token.

  * OUT-OF-SCOPE (T2): deliberately unanswerable questions ("what's the weather
    today", "who is the CEO of Google") get a graceful decline with no
    fabricated figure -- ideally via the OUT_OF_SCOPE short-circuit path.

  * SIDE-EFFECT-FREE CHAT (T1): asking an in-scope chat question writes NO new
    `agent_notifications` row with scope='chat' (verified straight against
    Postgres). Chat is read-only; it must not create inbox rows or trip HITL.

Modes:
  * Default: hit a running backend over HTTP (BACKEND_URL, default
    http://localhost:8000) at POST /api/chat with {message, persona}.
  * Fallback: if that backend isn't reachable, import and call run_chat_turn
    directly (needs the LLM provider + Postgres reachable from this process).
  Force one with GOLDEN_QA_MODE=http | direct (default: auto).

Exit code is non-zero if any case fails.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

# --- configuration ---------------------------------------------------------

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
DB_DSN = os.environ.get(
    "GOLDEN_QA_DSN", "postgresql://moveinsync:moveinsync@localhost:5432/moveinsync"
)
MODE = os.environ.get("GOLDEN_QA_MODE", "auto").lower()  # auto | http | direct
HTTP_TIMEOUT_S = float(os.environ.get("GOLDEN_QA_HTTP_TIMEOUT", "120"))

# Infra/output entities -- never a chat Q&A subject. Kept in lockstep with the
# SQL agent's own scope exclusion (app.graph.reason.sql_agent.prompts).
INFRA_ENTITIES = {"notification", "report", "pipeline_run", "chat_thread", "benchmark"}

# Representative real questions per business domain. Keyed by the contract's
# logical entity name so the in-scope set is DERIVED from the contract (see
# build_in_scope_cases) rather than being a second hardcoded domain list. A
# business entity with no curated question here still gets covered by a generic
# count question, so the suite can never silently skip a domain the contract
# grows.
QUESTIONS_BY_ENTITY: dict[str, list[str]] = {
    "trip": ["What is the average trip delay in minutes across all trips?"],
    "cost": ["What is the total transportation cost across all routes?"],
    "incident": ["How many safety incidents have been reported?"],
    "emission": ["What are the total CO2 emissions recorded?"],
    "vendor": ["How many transport vendors are there?"],
    "commute": ["How many commute records are marked as no-shows?"],
    "attendance": ["How many attendance records show a late arrival?"],
    "route": ["How many routes are configured?"],
    "employee": ["How many employees use the transport service?"],
    "driver": ["How many drivers are registered?"],
    "team": ["How many teams are there?"],
    "feedback": ["What is the average driver rating from trip feedback?"],
}

# Persona to ask each domain as (structurally irrelevant, but realistic).
PERSONA_BY_ENTITY: dict[str, str] = {
    "commute": "line_manager",
    "attendance": "line_manager",
    "employee": "line_manager",
    "team": "line_manager",
    "emission": "transport_head",
    "vendor": "transport_head",
    "cost": "transport_head",
}
DEFAULT_PERSONA = "transport_manager"

OUT_OF_SCOPE_QUESTIONS = [
    "What's the weather today?",
    "Who is the CEO of Google?",
]

_DIGIT_RE = re.compile(r"\d")
# Words that signal a graceful "I can't answer that" rather than a fabricated
# answer. Used only as a soft signal on out-of-scope cases.
_DECLINE_HINTS = (
    "outside",
    "only",
    "can't",
    "cannot",
    "don't have",
    "do not have",
    "not able",
    "scope",
    "transportation",
    "no access",
)


# --- case model ------------------------------------------------------------


@dataclass
class Case:
    domain: str
    question: str
    persona: str
    in_scope: bool


@dataclass
class Outcome:
    answer: str
    generated_sql: Optional[str]
    raw: dict[str, Any] = field(default_factory=dict)


# --- case construction (contract-derived) ----------------------------------


def build_in_scope_cases() -> list[Case]:
    from app.contracts import get_contract

    contract = get_contract()
    cases: list[Case] = []
    for name in contract.entity_names:
        if name in INFRA_ENTITIES:
            continue
        persona = PERSONA_BY_ENTITY.get(name, DEFAULT_PERSONA)
        questions = QUESTIONS_BY_ENTITY.get(name) or [f"How many {name} records are there?"]
        for q in questions:
            cases.append(Case(domain=name, question=q, persona=persona, in_scope=True))
    return cases


def build_out_of_scope_cases() -> list[Case]:
    return [
        Case(domain="out_of_scope", question=q, persona=DEFAULT_PERSONA, in_scope=False)
        for q in OUT_OF_SCOPE_QUESTIONS
    ]


# --- transports ------------------------------------------------------------


def _http_reachable() -> bool:
    parts = urlsplit(BACKEND_URL)
    host = parts.hostname or "localhost"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def ask_http(case: Case) -> Outcome:
    payload = json.dumps({"message": case.question, "persona": case.persona}).encode("utf-8")
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    # ChatResponse -> {"message": {"text": ...}}. The HTTP surface intentionally
    # does NOT expose generated SQL, so generated_sql stays None in this mode.
    answer = (body.get("message") or {}).get("text", "")
    return Outcome(answer=answer, generated_sql=None, raw=body)


def ask_direct(case: Case) -> Outcome:
    from app.contracts import get_contract
    from app.graph.supervisor import run_chat_turn

    org_id = get_contract().default_org_id
    result = run_chat_turn(question=case.question, persona=case.persona, org_id=org_id)
    return Outcome(
        answer=result.get("answer") or "",
        generated_sql=result.get("generated_sql"),
        raw=result,
    )


# --- assertions ------------------------------------------------------------


def _check_select_only(sql: str) -> tuple[bool, str]:
    from app.graph.reason.sql_agent.security import (
        SQLSecurityError,
        SQLSyntaxError,
        enforce_select_only,
    )

    try:
        enforce_select_only(sql, row_limit=200)
    except (SQLSyntaxError, SQLSecurityError) as exc:
        return False, f"generated SQL is not a safe SELECT: {type(exc).__name__}: {exc}"
    return True, ""


def assert_in_scope(outcome: Outcome) -> list[str]:
    """Return a list of failure messages (empty == pass)."""
    failures: list[str] = []
    answer = (outcome.answer or "").strip()
    if not answer:
        failures.append("in-scope question returned an empty answer")
        return failures

    # Only enforce the SQL/number contract when the transport actually exposed
    # generated SQL (direct mode). HTTP mode hides SQL by design, so a non-empty
    # answer is all that can be structurally checked there.
    if outcome.generated_sql:
        ok, msg = _check_select_only(outcome.generated_sql)
        if not ok:
            failures.append(msg)
        if not _DIGIT_RE.search(answer):
            failures.append("answer exposed SQL but contains no numeric token")
    return failures


def assert_out_of_scope(outcome: Outcome) -> list[str]:
    failures: list[str] = []
    answer = (outcome.answer or "").strip()
    if not answer:
        failures.append("out-of-scope question returned an empty answer")
        return failures
    # No fabricated figure: a graceful decline states it can't answer, it does
    # not invent a number.
    if _DIGIT_RE.search(answer):
        failures.append(f"out-of-scope answer contains a numeric token (possible fabricated figure): {answer!r}")
    # It must NOT have run a SELECT that produced data.
    if outcome.generated_sql:
        failures.append(f"out-of-scope question produced generated SQL: {outcome.generated_sql!r}")
    # Soft signal: ideally it reads like a decline. Not fatal on its own, but
    # combined with 'no digits' it confirms the OUT_OF_SCOPE path.
    lowered = answer.lower()
    if not any(h in lowered for h in _DECLINE_HINTS):
        failures.append(f"out-of-scope answer does not read as a graceful decline: {answer!r}")
    return failures


# --- T1 DB regression ------------------------------------------------------


def _chat_notification_count() -> Optional[int]:
    """Count agent_notifications rows with scope='chat'. Returns None if the
    DB is unreachable (the check is then SKIPPED rather than failed)."""
    try:
        import psycopg
    except ImportError:
        return None
    try:
        with psycopg.connect(DB_DSN, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM public.agent_notifications WHERE scope = %s",
                    ("chat",),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001 - infra unavailable -> skip, don't false-fail
        print(f"  [warn] could not read agent_notifications for T1 check: {exc}")
        return None


# --- runner ----------------------------------------------------------------


def _resolve_mode() -> str:
    if MODE in ("http", "direct"):
        return MODE
    return "http" if _http_reachable() else "direct"


def main() -> int:
    mode = _resolve_mode()
    ask = ask_http if mode == "http" else ask_direct
    print(f"golden_qa: mode={mode} backend={BACKEND_URL} dsn={DB_DSN.split('@')[-1]}")
    print("=" * 72)

    in_scope_cases = build_in_scope_cases()
    out_cases = build_out_of_scope_cases()

    print(f"in-scope domains ({len(in_scope_cases)}): "
          f"{', '.join(sorted({c.domain for c in in_scope_cases}))}")
    print(f"out-of-scope probes: {len(out_cases)}")
    print("-" * 72)

    # T1 baseline: snapshot chat-scope notifications BEFORE any chat turns.
    baseline_chat_rows = _chat_notification_count()

    passed = 0
    failed = 0

    def run_case(case: Case, asserter) -> None:
        nonlocal passed, failed
        label = f"[{'IN ' if case.in_scope else 'OUT'}:{case.domain}] {case.question!r} ({case.persona})"
        try:
            outcome = ask(case)
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            print(f"FAIL {label}\n     transport error: {exc}")
            failed += 1
            return
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {label}\n     unexpected error: {type(exc).__name__}: {exc}")
            failed += 1
            return
        problems = asserter(outcome)
        if problems:
            failed += 1
            print(f"FAIL {label}")
            for p in problems:
                print(f"     - {p}")
            snippet = (outcome.answer or "").strip().replace("\n", " ")[:140]
            print(f"     answer: {snippet!r}")
        else:
            passed += 1
            snippet = (outcome.answer or "").strip().replace("\n", " ")[:100]
            sql_note = " [sql:SELECT-only]" if outcome.generated_sql else ""
            print(f"PASS {label}{sql_note}\n     answer: {snippet!r}")

    for case in in_scope_cases:
        run_case(case, assert_in_scope)
    for case in out_cases:
        run_case(case, assert_out_of_scope)

    # T1 assertion: no new chat-scope notification rows created.
    print("-" * 72)
    after_chat_rows = _chat_notification_count()
    t1_failed = False
    if baseline_chat_rows is None or after_chat_rows is None:
        print("SKIP T1 side-effect-free check: Postgres not reachable "
              "(the integrated run should verify this against the live DB).")
    elif after_chat_rows > baseline_chat_rows:
        t1_failed = True
        print(f"FAIL T1 side-effect-free chat: agent_notifications scope='chat' rows grew "
              f"{baseline_chat_rows} -> {after_chat_rows} (chat must write no notification row)")
    else:
        print(f"PASS T1 side-effect-free chat: agent_notifications scope='chat' rows unchanged "
              f"({baseline_chat_rows} -> {after_chat_rows})")

    print("=" * 72)
    total = passed + failed + (1 if t1_failed else 0)
    print(f"SUMMARY: {passed} passed, {failed} failed"
          + (", T1 regression FAILED" if t1_failed else "")
          + f"  (case total {passed + failed})")

    return 0 if (failed == 0 and not t1_failed) else 1


if __name__ == "__main__":
    sys.exit(main())
