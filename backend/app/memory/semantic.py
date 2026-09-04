"""Semantic memory (plan §6): persistent per-user preferences.

One document per persona+user namespace (key "preferences") holding all
preference fields together, so recall_preferences() is always a single
store.get() -- not a search/scan across many small items -- and
remember_preference() merges into it rather than overwriting, so setting
`region` doesn't clobber a previously-set `currency`.

Writes are synchronous by default: a preference overwrite is a single-key
jsonb upsert with no extraction/dedup step behind it (unlike episodic/
procedural below), so there's nothing here worth moving off the hot path --
see background.py for why "background consolidation" is reserved for the
memory kinds that actually do consolidation work.
"""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any

from app.memory import background
from app.memory.namespaces import memory_namespace
from app.memory.store import get_memory_store

_PREFERENCES_KEY = "preferences"


def remember_preference(
    persona_id: str,
    user_id: str,
    key: str,
    value: Any,
    *,
    background_write: bool = False,
) -> Future | None:
    namespace = memory_namespace(persona_id, user_id, "semantic")
    store = get_memory_store()

    def _write() -> None:
        existing = store.get(namespace, _PREFERENCES_KEY)
        merged = dict(existing.value) if existing else {}
        merged[key] = value
        store.put(namespace, _PREFERENCES_KEY, merged)

    if background_write:
        return background.defer(_write)
    _write()
    return None


def recall_preferences(persona_id: str, user_id: str) -> dict[str, Any]:
    namespace = memory_namespace(persona_id, user_id, "semantic")
    item = get_memory_store().get(namespace, _PREFERENCES_KEY)
    return dict(item.value) if item else {}


def recall_preference(persona_id: str, user_id: str, key: str, default: Any = None) -> Any:
    return recall_preferences(persona_id, user_id).get(key, default)
