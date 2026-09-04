"""Shared, process-wide handles for the API routes: org_id resolution and
cached compiled-graph/engine accessors, so every route reuses the same
checkpointer/engine singletons the scheduler and supervisor already use
(app.checkpointer.get_checkpointer, app.graph.act.db.get_engine) instead of
opening new pools per request.
"""

from __future__ import annotations

import functools
import os

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.checkpointer import get_checkpointer
from app.contracts import get_contract
from app.graph.act.db import get_engine
from app.graph.graph import build_top_graph


def default_org_id() -> str:
    return os.environ.get("ORG_ID") or get_contract().default_org_id


def public_api_base_url() -> str:
    """Absolute base URL this API is reachable at, used only to build
    absolute links (report preview_url) meant for direct browser navigation
    (a plain `<a href>`, not routed through apiClient's request() wrapper) --
    so a relative path would resolve against the frontend's own origin
    instead of the backend's. Defaults to match frontend/.env.example's
    VITE_API_BASE_URL for local dev."""
    return os.environ.get("PUBLIC_API_BASE_URL", "http://localhost:8000/api")


@functools.lru_cache(maxsize=1)
def get_top_graph():
    """The reason->act graph, compiled once against the shared PostgresSaver
    (same checkpointer instance the scheduler/supervisor use) -- required so
    a thread paused by a scheduler-triggered run can be resumed by this API
    process, and so GET /threads/{id}/trace reads real checkpoint history.

    Every real run in this system (app.graph.supervisor.run_pipeline AND
    run_chat_turn) invokes this exact top-level graph -- act is embedded into
    it as a subgraph node compiled with checkpointer=None (see graph.py's
    module docstring), so an interrupt() raised inside act is only resumable
    by invoking THIS graph object with Command(resume=...), never a
    separately-built, standalone act subgraph (that would look for the pause
    under its own top-level checkpoint namespace, not the nested one the
    embedded copy actually wrote to). POST /notifications/{id}/resume relies
    on this.
    """
    return build_top_graph(get_checkpointer())


def notifications_engine():
    return get_engine()


__all__ = [
    "default_org_id",
    "public_api_base_url",
    "get_top_graph",
    "notifications_engine",
    "get_checkpointer",
    "BaseCheckpointSaver",
]
