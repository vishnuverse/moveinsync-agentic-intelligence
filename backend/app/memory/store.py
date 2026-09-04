"""Postgres-backed BaseStore setup (plan §6), wired to the shared DATABASE_URL.

Confirmed against the installed langgraph-checkpoint-postgres (3.1.2) source
directly, not assumed: `langgraph.store.postgres.PostgresStore` is real and
current, `.from_conn_string()` is a contextmanager, and `.setup()` idempotently
creates/migrates its own `store` table (see backend/db/memory_schema.sql for
what that call actually does) -- nothing here hand-writes that DDL.

`from_conn_string()` yields inside a `with` block; a long-lived FastAPI/graph
process needs the pool kept open past that block, so the contextmanager is
entered once into a module-level ExitStack instead of used as `with ...:`
directly -- the same pattern LangGraph's own docs use for a process-lifetime
store. close_memory_store() releases it (app shutdown, or between tests that
need a genuinely fresh connection/pool rather than the cached singleton).
"""

from __future__ import annotations

import functools
import os
from contextlib import ExitStack

from langgraph.store.postgres import PostgresStore

_DEFAULT_DATABASE_URL = "postgresql://moveinsync:moveinsync@localhost:5432/moveinsync"

_exit_stack = ExitStack()


def _psycopg_conn_string(database_url: str) -> str:
    # The sense subgraph's SQLAlchemy engine (backend/app/graph/sense/db.py) rewrites
    # DATABASE_URL to the `postgresql+psycopg://` SQLAlchemy dialect form; psycopg
    # itself (what PostgresStore uses directly, no SQLAlchemy) only understands the
    # plain `postgresql://` scheme, so undo that rewrite if it's already been applied.
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return database_url


@functools.lru_cache(maxsize=1)
def get_memory_store() -> PostgresStore:
    """Cached singleton -- one pooled connection per process, like get_contract()."""
    database_url = os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)
    conn_string = _psycopg_conn_string(database_url)
    store = _exit_stack.enter_context(
        PostgresStore.from_conn_string(
            conn_string,
            pool_config={"min_size": 1, "max_size": 5},
        )
    )
    store.setup()
    return store


def close_memory_store() -> None:
    """Close the pooled connection and drop the cached singleton.

    Call on app shutdown, and between test "sessions" that need to prove
    persistence survives a fresh store instance/connection rather than reading
    back from the same in-process object.
    """
    _exit_stack.close()
    get_memory_store.cache_clear()
