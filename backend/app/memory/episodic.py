"""Episodic memory (plan §6): cross-session "what was discussed" context.

Each remember_episode() call appends a new item (key = thread_id + a short
random suffix) rather than overwriting one -- a conversation accrues many
episodes over time, unlike a semantic preference which has one current value.
recall_episodes() lists the persona+user's episodic namespace and filters/
sorts in Python; no vector index is configured on the store (see
backend/db/memory_schema.sql), so this is namespace-prefix + in-memory
filtering, not natural-language semantic search over content.

Writes default to backgrounded: producing a good summary is real work (at
minimum the caller composed prose; optionally summarize_episode_with_llm()
below ran a whole LLM call), and per plan §6 that shouldn't add latency to a
live chat response, so the store.put() itself is deferred off the calling
thread via background.defer() -- see background.py for exactly what
"background" does and doesn't mean here.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import Future
from typing import Any

from app.memory import background
from app.memory.namespaces import memory_namespace
from app.memory.store import get_memory_store


def remember_episode(
    persona_id: str,
    user_id: str,
    thread_id: str,
    summary: str,
    *,
    metadata: dict[str, Any] | None = None,
    background_write: bool = True,
) -> Future | None:
    namespace = memory_namespace(persona_id, user_id, "episodic")
    store = get_memory_store()
    key = f"{thread_id}:{uuid.uuid4().hex[:12]}"
    value = {
        "thread_id": thread_id,
        "summary": summary,
        "metadata": metadata or {},
        "recorded_at": time.time(),
    }

    def _write() -> None:
        store.put(namespace, key, value)

    if background_write:
        return background.defer(_write)
    _write()
    return None


def recall_episodes(
    persona_id: str,
    user_id: str,
    *,
    thread_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    namespace = memory_namespace(persona_id, user_id, "episodic")
    # Over-fetch when filtering by thread_id since search() has no thread_id
    # filter of its own -- the namespace only scopes persona+user, not thread.
    fetch_limit = limit if thread_id is None else max(limit * 5, 100)
    items = get_memory_store().search(namespace, limit=fetch_limit)
    episodes = [{**item.value, "key": item.key} for item in items]
    if thread_id is not None:
        episodes = [episode for episode in episodes if episode.get("thread_id") == thread_id]
    episodes.sort(key=lambda episode: episode.get("recorded_at", 0), reverse=True)
    return episodes[:limit]


def forget_thread(persona_id: str, thread_id: str) -> int:
    """Deletes every episodic memory item for one chat thread (DELETE
    /api/chat/threads/{id}) so a deleted thread's history can never leak into
    a later thread's context. BaseStore has no bulk-namespace delete
    primitive, only delete(namespace, key) for one item at a time (confirmed
    against the installed langgraph-checkpoint-postgres, same way store.py's
    own docstring confirms its API directly rather than assuming) -- so this
    lists the namespace's keys via search() first, then deletes each.

    `thread_id` is passed as the namespace's `user_id` slot here, not the
    persona -- see app/api/chat.py's module docstring for why the chat
    feature repurposes that slot to mean "this one conversation" instead of
    "this one persona", which is exactly what makes per-thread isolation (and
    therefore per-thread deletion) possible in the first place."""
    namespace = memory_namespace(persona_id, thread_id, "episodic")
    store = get_memory_store()
    items = store.search(namespace, limit=1000)
    for item in items:
        store.delete(namespace, item.key)
    return len(items)


def summarize_episode_with_llm(messages: list[dict[str, Any]], *, provider: str | None = None) -> str:
    """Optional: turn a message history into remember_episode()'s summary text
    via LangMem's create_thread_extractor, run against this repo's own
    get_chat_model() (so it inherits the Redis daily-budget circuit breaker
    automatically, same as every other LLM call in this codebase).

    Not exercised by this build's verification -- no live Sarvam/OpenRouter
    credential is available in this environment, and create_thread_extractor()
    always performs a real LLM call, which would either fail without a key or
    silently spend budget in a test run. Documented here as the intended
    plug-in point for the future chat endpoint: call this to produce `summary`,
    then pass it to remember_episode() same as any caller-supplied string.
    """
    from langmem import create_thread_extractor

    from app.llm import get_chat_model

    extractor = create_thread_extractor(get_chat_model(provider))
    result = extractor.invoke({"messages": messages})
    return getattr(result, "summary", None) or str(result)
