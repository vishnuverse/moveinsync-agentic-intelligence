"""Procedural memory (plan §6): SQL agent error patterns to avoid repeating.

Reads the shape backend/app/graph/reason/sql_agent/state.py already produces
(`query_error_log: list[str]`, appended to on each failed generate_query ->
run_query attempt within SQLAgentState) -- this module only owns storing and
retrieving patterns extracted from that log; wiring recall_known_mistakes()'s
output into generate_query's actual system prompt (prompts.py) is left to
that subgraph's owner, per this task's constraints on backend/app/graph/.

Consolidation here is plain Python (regex normalization + a fingerprint
count), not an LLM call -- matches the plan's own framing of procedural
memory as "a lightweight self-improvement loop, not a separate ML pipeline".
It's still routed through background.defer() by default because it does do
real work (merge-and-recount against whatever's already stored) that a live
SQL-agent turn shouldn't wait on.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import Future
from typing import Any

from app.memory import background
from app.memory.namespaces import memory_namespace
from app.memory.store import get_memory_store

_KNOWN_MISTAKES_KEY = "known_mistakes"
_QUOTED_LITERAL = re.compile(r"'[^']*'")
_DIGITS = re.compile(r"\b\d+\b")


def _normalize(raw_error: str) -> str:
    """Collapse volatile bits (quoted values, numbers) so e.g. two failures
    against different literal dates fingerprint as the same known mistake
    instead of growing the store with near-duplicate entries forever."""
    normalized = _QUOTED_LITERAL.sub("'?'", raw_error)
    normalized = _DIGITS.sub("#", normalized)
    return normalized.strip()


def remember_query_errors(
    persona_id: str,
    scope_id: str,
    query_error_log: list[str],
    *,
    background_write: bool = True,
) -> Future | None:
    """scope_id identifies whatever slice of the SQL agent this log came from
    (e.g. a fixed pseudo-user like "sql_agent" for a persona-wide pattern
    store, or a real user_id for a per-session one) -- this module is
    agnostic to which convention the caller picks, same as everywhere else
    user_id is just an opaque client-provided identifier."""
    if not query_error_log:
        return None
    namespace = memory_namespace(persona_id, scope_id, "procedural")
    store = get_memory_store()

    def _consolidate() -> None:
        existing = store.get(namespace, _KNOWN_MISTAKES_KEY)
        patterns: dict[str, dict[str, Any]] = dict(existing.value.get("patterns", {})) if existing else {}
        for raw_error in query_error_log:
            normalized = _normalize(raw_error)
            fingerprint = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
            entry = patterns.get(fingerprint, {"pattern": normalized, "count": 0})
            entry["count"] += 1
            entry["example"] = raw_error
            patterns[fingerprint] = entry
        store.put(namespace, _KNOWN_MISTAKES_KEY, {"patterns": patterns})

    if background_write:
        return background.defer(_consolidate)
    _consolidate()
    return None


def recall_known_mistakes(persona_id: str, scope_id: str, *, limit: int = 5) -> str:
    """Formats stored patterns as prompt-ready bullet text, e.g. for injection
    into generate_query's system prompt as "known past mistakes to avoid".
    Returns "" when nothing is stored yet -- callers can unconditionally
    interpolate this into a prompt without an empty-state branch."""
    namespace = memory_namespace(persona_id, scope_id, "procedural")
    item = get_memory_store().get(namespace, _KNOWN_MISTAKES_KEY)
    if not item:
        return ""
    patterns = list(item.value.get("patterns", {}).values())
    patterns.sort(key=lambda pattern: pattern["count"], reverse=True)
    lines = [
        f"- {pattern['pattern']} (seen {pattern['count']}x, e.g. {pattern['example']!r})"
        for pattern in patterns[:limit]
    ]
    return "\n".join(lines)
