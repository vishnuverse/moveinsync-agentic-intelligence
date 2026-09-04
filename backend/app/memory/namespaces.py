"""Persona-scoped namespace isolation (plan §6) for LangGraph's BaseStore.

Every read/write in this package goes through memory_namespace() so a
namespace tuple is never hand-built inline -- the one thing that must never
happen is a Line Manager's memory leaking into a Transport Head's context,
and that's only guaranteed if there is exactly one place that constructs the
tuple.

No login/auth exists yet (per the plan), so `user_id` is whatever
client-provided identifier the caller has -- a session/browser id today,
swappable for a real authenticated user id later without touching callers,
since they only ever pass persona_id/user_id through, never a raw tuple.
"""

from __future__ import annotations

from typing import Literal

MemoryKind = Literal["semantic", "episodic", "procedural"]

_ROOT = "memories"
_KINDS: frozenset[str] = frozenset(("semantic", "episodic", "procedural"))


def memory_namespace(
    persona_id: str,
    user_id: str,
    kind: MemoryKind | None = None,
) -> tuple[str, ...]:
    """Build the namespace tuple for a given persona+user(+kind).

    Matches the plan's `("memories", "{persona_id}", "{user_id}")` shape for
    the first three segments -- kind is an added fourth segment so semantic/
    episodic/procedural memories don't collide within one persona+user, while
    `memory_namespace(persona_id, user_id)` (kind=None) still returns the
    plan's exact 3-tuple, usable as a namespace *prefix* to search/list
    everything for that persona+user across all three kinds at once.
    """
    if not persona_id or not persona_id.strip():
        raise ValueError("persona_id must be a non-empty string")
    if not user_id or not user_id.strip():
        raise ValueError("user_id must be a non-empty string")
    if kind is not None and kind not in _KINDS:
        raise ValueError(f"kind must be one of {sorted(_KINDS)}, got {kind!r}")

    if kind is None:
        return (_ROOT, persona_id, user_id)
    return (_ROOT, persona_id, user_id, kind)
