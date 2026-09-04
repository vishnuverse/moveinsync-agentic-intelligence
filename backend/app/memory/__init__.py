"""Conversational memory (plan §6) -- public interface for other code
(the future FastAPI chat endpoint, the SQL agent cluster) to call.

    from app.memory import (
        get_memory_store, close_memory_store,
        memory_namespace,
        remember_preference, recall_preferences, recall_preference,
        remember_episode, recall_episodes,
        remember_query_errors, recall_known_mistakes,
        wait_for_pending,
    )

See backend/app/memory/store.py, semantic.py, episodic.py, procedural.py and
background.py for what LangMem's real API surface turned out to be and why
each piece is built the way it is.
"""

from __future__ import annotations

from app.memory.background import wait_for_pending
from app.memory.episodic import recall_episodes, remember_episode, summarize_episode_with_llm
from app.memory.namespaces import MemoryKind, memory_namespace
from app.memory.procedural import recall_known_mistakes, remember_query_errors
from app.memory.semantic import recall_preference, recall_preferences, remember_preference
from app.memory.store import close_memory_store, get_memory_store

__all__ = [
    "get_memory_store",
    "close_memory_store",
    "memory_namespace",
    "MemoryKind",
    "remember_preference",
    "recall_preferences",
    "recall_preference",
    "remember_episode",
    "recall_episodes",
    "summarize_episode_with_llm",
    "remember_query_errors",
    "recall_known_mistakes",
    "wait_for_pending",
]
